import json

import pytest
from fastapi.testclient import TestClient

import econlens.store as store_mod
from econlens.app import app
from econlens.coach import (
    aggregate_core_findings, ai_coach, deterministic_coach, detect_demonstrated,
    detect_evaluation_chain, run_coach, validate_coach_output, _bundle_text,
)
from econlens.preflight import run_preflight

DRAFT_WEAK_EVAL = (
    "The tax shifts the supply curve upward as Figure 1 shows. "
    "Many consumers enjoy these drinks a great deal. "
    "Clearly the government should ban these drinks entirely without question."
)
DRAFT_FULL_CHAIN = (
    "For low-income consumers, the tax leads to a larger burden as a share of income. "
    "As a result, their real consumption falls further than for richer households. "
    "On balance the policy is justified because the 22% fall in consumption outweighs the cost."
)


def _preflight(draft):
    return run_preflight(draft, "")


# ---------- evaluation chain detector ----------

def test_full_chain_detected():
    chain = detect_evaluation_chain(DRAFT_FULL_CHAIN)
    assert chain["missing"] == []


def test_missing_judgement_and_criterion():
    draft = "The tax leads to lower sales. As a result, revenue falls."
    chain = detect_evaluation_chain(draft)
    assert "judgement" in chain["missing"]
    assert "mechanism" not in chain["missing"]


def test_empty_draft_missing_everything():
    assert detect_evaluation_chain("Nice weather today.")["missing"] == list(
        ("criterion", "mechanism", "consequence", "judgement"))


# ---------- contract validation ----------

def _finding(**over):
    f = {"stage": "claim", "finding": "Claim asserts welfare gain; evidence covers quantity only.",
         "evidence": "check:claims",
         "question": "What evidence would justify the welfare step?", "severity": "major"}
    f.update(over)
    return f


def test_valid_output_passes():
    payload = {"findings": [_finding()]}
    assert validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"}) == []


def test_bad_stage_and_severity_rejected():
    payload = {"findings": [_finding(stage="vibes", severity="huge")]}
    problems = validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"})
    assert any("bad stage" in p for p in problems)
    assert any("bad severity" in p for p in problems)


def test_stage_mismatch_rejected():
    payload = {"findings": [_finding(stage="draft")]}
    problems = validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"}, expected_stage="claim")
    assert any("must be 'claim'" in p for p in problems)


def test_question_must_be_question():
    payload = {"findings": [_finding(question="Fix the welfare step.")]}
    assert any("'?'" in p for p in validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"}))


def test_evidence_check_id_must_exist():
    payload = {"findings": [_finding(evidence="check:vibes")]}
    assert any("not grounded" in p for p in validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"}))


def test_evidence_quote_must_be_verbatim():
    good = {"findings": [_finding(evidence="quote:shifts the supply curve upward")]}
    assert validate_coach_output(good, DRAFT_WEAK_EVAL, set()) == []
    bad = {"findings": [_finding(evidence="quote:something never written")]}
    assert any("not grounded" in p for p in validate_coach_output(bad, DRAFT_WEAK_EVAL, set()))


def test_ungrounded_free_text_evidence_rejected():
    payload = {"findings": [_finding(evidence="the second paragraph feels weak")]}
    assert any("not grounded" in p for p in validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"}))


def test_ghostwriting_marker_rejected():
    payload = {"findings": [_finding(finding="Here is a revised version of your claim.")]}
    assert any("ghostwriting" in p for p in validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"}))


# ---------- v0.3.1 calibration: severities, leak guard, mastery, dedup ----------

def test_new_severity_scale_accepted():
    for sev in ("critical", "major", "minor", "opportunity"):
        payload = {"findings": [_finding(severity=sev)]}
        assert validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"}) == []


def test_old_info_severity_now_rejected():
    payload = {"findings": [_finding(severity="info")]}
    assert any("bad severity" in p for p in validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"}))


def test_question_leaking_concept_rejected():
    # "price elasticity of demand" is not in the student's text -> answer leakage
    payload = {"findings": [_finding(
        question="Have you considered the price elasticity of demand here?")]}
    problems = validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"})
    assert any("names concepts absent" in p for p in problems)


def test_question_using_students_own_concept_allowed():
    draft = DRAFT_WEAK_EVAL + " Demand may be price elasticity of demand related."
    payload = {"findings": [_finding(
        question="How does the price elasticity of demand support this claim?")]}
    assert validate_coach_output(payload, draft, {"claims"}) == []


def test_detect_demonstrated_limitation_and_stakeholders():
    draft = ("Sales fell by 22%, although it does not isolate the effect of the tax "
             "from other changes in consumer behaviour. Consumers lose surplus while "
             "producers face lower revenue.")
    moves = {d["move"] for d in detect_demonstrated(draft)}
    assert "limitation_acknowledged" in moves
    assert "stakeholder_weighing" in moves


def test_bundle_includes_demonstrated_section():
    draft = "However, this does not isolate the effect of the tax."
    bundle = _bundle_text(draft, "", "", run_preflight(draft, ""), "claim")
    assert "ALREADY DEMONSTRATED" in bundle
    assert "limitation_acknowledged" in bundle


def _res(stage, findings):
    return {"stage": stage, "source": "ai", "findings": findings, "fallback_reason": None}


def test_aggregate_merges_same_quote_across_stages():
    f_claim = _finding(stage="claim", severity="major",
                       evidence="quote:shifts the supply curve upward")
    f_eval = _finding(stage="evaluation", severity="minor",
                      evidence="quote:shifts the supply curve upward",
                      finding="Different wording, same underlying causal issue here.")
    core = aggregate_core_findings([_res("claim", [f_claim]), _res("evaluation", [f_eval])])
    assert len(core) == 1
    assert core[0]["severity"] == "major"          # highest severity wins
    assert core[0]["stages"] == ["claim", "evaluation"]
    assert len(core[0]["views"]) == 1


def test_aggregate_merges_similar_findings_without_quotes():
    f1 = _finding(stage="claim", evidence="check:claims",
                  finding="The causal attribution of the sales fall to the tax lacks a counterfactual.")
    f2 = _finding(stage="draft", evidence="check:claims",
                  finding="Causal attribution of the sales fall to the tax lacks any counterfactual comparison.")
    core = aggregate_core_findings([_res("claim", [f1]), _res("draft", [f2])])
    assert len(core) == 1


def test_aggregate_keeps_distinct_issues_separate():
    f1 = _finding(stage="claim", evidence="quote:shifts the supply curve upward",
                  finding="Mechanism of the supply shift is asserted, never explained.")
    f2 = _finding(stage="evaluation", evidence="quote:larger share of income",
                  finding="Distributional burden on poorer households is raised then dropped entirely.")
    core = aggregate_core_findings([_res("claim", [f1]), _res("evaluation", [f2])])
    assert len(core) == 2


# ---------- v0.3.2: severity-calibration rules + audit trail ----------

HEDGED_DRAFT = ("Therefore, the tax may have only a limited effect on consumption overall. "
                "Many people continue to buy these drinks anyway.")
DEMO_DRAFT = ("Sales fell by 22%, although it does not isolate the effect of the tax "
              "from other changes in consumer behaviour.")


def test_hedged_claim_major_rejected_and_tagged():
    payload = {"findings": [_finding(
        evidence="quote:The tax may have only a limited effect on consumption overall.",
        severity="major")]}
    problems = validate_coach_output(payload, HEDGED_DRAFT, {"claims"})
    assert any(p.startswith("calibration:hedged_claim_downgrade") for p in problems)


def test_hedged_claim_minor_and_critical_allowed():
    for sev in ("minor", "critical", "opportunity"):
        payload = {"findings": [_finding(
            evidence="quote:The tax may have only a limited effect on consumption overall.",
            severity=sev)]}
        problems = validate_coach_output(payload, HEDGED_DRAFT, {"claims"})
        assert not any("calibration" in p for p in problems), sev


def test_demonstrated_quote_must_be_opportunity():
    for sev in ("critical", "major", "minor"):
        payload = {"findings": [_finding(
            evidence="quote:it does not isolate the effect of the tax",
            severity=sev)]}
        problems = validate_coach_output(payload, DEMO_DRAFT, {"claims"})
        assert any(p.startswith("calibration:demonstrated_quote_downgrade") for p in problems), sev
    ok = {"findings": [_finding(
        evidence="quote:it does not isolate the effect of the tax", severity="opportunity")]}
    assert not any("calibration" in p for p in validate_coach_output(ok, DEMO_DRAFT, {"claims"}))


def test_calibration_audit_trail_recorded_on_retry():
    bad = {"findings": [_finding(
        evidence="quote:The tax may have only a limited effect on consumption overall.",
        severity="major")]}
    good = {"findings": [_finding(
        evidence="quote:The tax may have only a limited effect on consumption overall.",
        severity="minor")]}
    responses = [json.dumps(bad), json.dumps(good)]

    def fake_chat(messages, temperature=0.2):
        return responses.pop(0)

    findings, calibration = ai_coach("claim", HEDGED_DRAFT, "", "",
                                     _preflight(HEDGED_DRAFT), _chat=fake_chat)
    assert findings[0]["severity"] == "minor"
    assert calibration and calibration[0]["rule"] == "hedged_claim_downgrade"
    assert calibration[0]["original_severity"] == "major"


def test_deterministic_coach_caps_hedged_claims_at_minor():
    findings = deterministic_coach("claim", HEDGED_DRAFT, _preflight(HEDGED_DRAFT))
    hedged = [f for f in findings if f["evidence"].startswith("quote:")
              and "may have only" in f["evidence"]]
    assert hedged and hedged[0]["severity"] == "minor"
    assert "hedged" in hedged[0]["finding"].lower() or "capped" in hedged[0]["finding"].lower()


def test_deterministic_findings_pass_calibration_rules():
    for draft in (DRAFT_WEAK_EVAL, HEDGED_DRAFT, DEMO_DRAFT):
        pf = _preflight(draft)
        for stage in ("rq", "claim", "evaluation", "draft"):
            findings = deterministic_coach(stage, draft, pf)
            det_ids = {c["id"] for c in pf} | {"evaluation_chain"}
            problems = validate_coach_output({"findings": findings}, draft, det_ids)
            assert not any("calibration" in p for p in problems), (stage, problems)


def test_aggregate_sorts_must_fix_first():
    f1 = _finding(stage="claim", severity="opportunity",
                  evidence="quote:shifts the supply curve upward",
                  finding="Good diagram link; the incidence split could be quantified further.")
    f2 = _finding(stage="evaluation", severity="critical",
                  evidence="quote:larger share of income",
                  finding="Core judgement rests on an equity claim with zero support anywhere.")
    core = aggregate_core_findings([_res("claim", [f1]), _res("evaluation", [f2])])
    assert core[0]["severity"] == "critical" and core[0]["must_fix"] is True
    assert core[1]["severity"] == "opportunity" and core[1]["must_fix"] is False


def test_too_many_findings_rejected():
    payload = {"findings": [_finding() for _ in range(9)]}
    assert any("prioritise" in p for p in validate_coach_output(payload, DRAFT_WEAK_EVAL, {"claims"}))


# ---------- deterministic coach ----------

def test_deterministic_claim_coach_flags_unsupported():
    findings = deterministic_coach("claim", DRAFT_WEAK_EVAL, _preflight(DRAFT_WEAK_EVAL))
    unsupported = [f for f in findings if f["evidence"].startswith("quote:")]
    assert unsupported and unsupported[0]["severity"] == "major"
    assert unsupported[0]["question"].endswith("?")


def test_deterministic_evaluation_coach_names_missing_steps():
    findings = deterministic_coach("evaluation", DRAFT_WEAK_EVAL, _preflight(DRAFT_WEAK_EVAL))
    texts = " ".join(f["finding"] for f in findings)
    assert "consequence" in texts or "criterion" in texts or "mechanism" in texts


def test_deterministic_findings_satisfy_own_contract():
    for stage in ("rq", "claim", "evaluation", "draft"):
        pf = _preflight(DRAFT_WEAK_EVAL)
        findings = deterministic_coach(stage, DRAFT_WEAK_EVAL, pf)
        det_ids = {c["id"] for c in pf} | {"evaluation_chain"}
        assert validate_coach_output({"findings": findings}, DRAFT_WEAK_EVAL, det_ids) == []


# ---------- ai coach with mock ----------

def test_ai_coach_happy_path():
    good = {"findings": [_finding()]}

    def fake_chat(messages, temperature=0.2):
        return json.dumps(good)

    out, calibration = ai_coach("claim", DRAFT_WEAK_EVAL, "", "",
                                _preflight(DRAFT_WEAK_EVAL), _chat=fake_chat)
    assert out[0]["severity"] == "major"
    assert calibration == []


def test_ai_coach_retry_then_fail():
    bad = {"findings": [_finding(evidence="check:nonsense")]}
    calls = {"n": 0}

    def fake_chat(messages, temperature=0.2):
        calls["n"] += 1
        return json.dumps(bad)

    with pytest.raises(ValueError, match="contract"):
        ai_coach("claim", DRAFT_WEAK_EVAL, "", "", _preflight(DRAFT_WEAK_EVAL), _chat=fake_chat)
    assert calls["n"] == 2


def test_run_coach_without_key_uses_deterministic(monkeypatch):
    monkeypatch.delenv("ECONLENS_API_KEY", raising=False)
    res = run_coach("claim", DRAFT_WEAK_EVAL, preflight=_preflight(DRAFT_WEAK_EVAL))
    assert res["source"] == "deterministic"
    assert res["fallback_reason"] == "no API key configured"
    assert res["findings"]


def test_run_coach_falls_back_on_ai_error(monkeypatch):
    monkeypatch.setenv("ECONLENS_API_KEY", "sk-test")

    def broken_chat(messages, temperature=0.2):
        raise RuntimeError("provider down")

    res = run_coach("claim", DRAFT_WEAK_EVAL, preflight=_preflight(DRAFT_WEAK_EVAL),
                    _chat=broken_chat)
    assert res["source"] == "deterministic"
    assert "provider down" in res["fallback_reason"]


# ---------- api + experiment log ----------

@pytest.fixture(autouse=True)
def tmp_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "DATA_DIR", tmp_path / "sessions")
    monkeypatch.delenv("ECONLENS_API_KEY", raising=False)
    yield


client = TestClient(app)


def test_api_coach_all_stages_offline():
    r = client.post("/api/coach", json={"draft": DRAFT_WEAK_EVAL, "stage": "all"})
    assert r.status_code == 200
    body = r.json()
    assert [x["stage"] for x in body["results"]] == ["rq", "claim", "evaluation", "draft"]
    assert all(x["source"] == "deterministic" for x in body["results"])
    assert all(x["log_id"] for x in body["results"])


def test_api_coach_does_not_change_mechanical_results():
    review = client.post("/api/review", json={"draft": DRAFT_WEAK_EVAL}).json()
    coach = client.post("/api/coach", json={"session_id": review["session_id"],
                                            "draft": DRAFT_WEAK_EVAL}).json()
    strip = lambda pf: [(c["id"], c["status"]) for c in pf]
    assert strip(coach["preflight"]) == strip(review["preflight"])


def test_api_coach_logs_experiment_record():
    r = client.post("/api/coach", json={"draft": DRAFT_WEAK_EVAL, "stage": "claim"}).json()
    session = store_mod.load_session(r["session_id"])
    log = session["coach_log"]
    assert len(log) == 1
    entry = log[0]
    assert entry["stage"] == "claim"
    assert len(entry["input_sha256"]) == 64
    assert entry["deterministic"] and entry["action"] is None


def test_api_coach_action_roundtrip():
    r = client.post("/api/coach", json={"draft": DRAFT_WEAK_EVAL, "stage": "claim"}).json()
    log_id = r["results"][0]["log_id"]
    a = client.post("/api/coach/action", json={
        "session_id": r["session_id"], "log_id": log_id,
        "action": "accepted", "note": "adding elasticity evidence"})
    assert a.status_code == 200
    session = store_mod.load_session(r["session_id"])
    assert session["coach_log"][0]["action"]["action"] == "accepted"


def test_api_coach_action_validates():
    r = client.post("/api/coach", json={"draft": DRAFT_WEAK_EVAL, "stage": "claim"}).json()
    bad = client.post("/api/coach/action", json={
        "session_id": r["session_id"], "log_id": "c999", "action": "accepted"})
    assert bad.status_code == 404
    bad2 = client.post("/api/coach/action", json={
        "session_id": r["session_id"], "log_id": r["results"][0]["log_id"], "action": "loved"})
    assert bad2.status_code == 422


def test_api_coach_bad_stage_422():
    assert client.post("/api/coach", json={"draft": "x", "stage": "vibes"}).status_code == 422
