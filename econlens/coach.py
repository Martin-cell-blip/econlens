"""Workflow-native AI Coach - four coaches bound to the deterministic pipeline.

Product rule (enforced by contract, not by hope):
    The coach never answers "how should I write this?" -
    it answers "which step of your reasoning is not yet proven?"

Contract for every finding (AI or deterministic - the UI sees ONE format):
    {stage, finding, evidence, question, severity}
  * stage    in {rq, claim, evaluation, draft}
  * severity in {info, minor, major}
  * question ends with "?" - the student does the thinking
  * evidence must be GROUNDED: cite a deterministic check id ("check:claims")
    or quote the draft verbatim ("quote:..."). Ungrounded findings are rejected.
  * no ghost-writing: rewrite markers are banned, fields are length-capped
  * AI output never touches the mechanical results - preflight is computed
    before the coach runs and returned unchanged.

No API key / API failure -> deterministic fallback with the same contract shape.
"""
from __future__ import annotations

import re

from .claims import CAUSAL_MARKERS, map_claims
from .feedback import GHOSTWRITE_MARKERS, _norm, _word_len
from .llm import chat, extract_json
from .rubric import load_rubric

STAGES = ("rq", "claim", "evaluation", "draft")
# calibrated scale (v0.3.1):
#   critical    - if unfixed, the core argument fails
#   major       - clear logic / evidence / economic-reasoning defect
#   minor       - a problem, but the core argument survives it
#   opportunity - already correct; room to deepen. NEVER a defect label.
SEVERITIES = ("critical", "major", "minor", "opportunity")
SEVERITY_RANK = {s: i for i, s in enumerate(reversed(SEVERITIES))}
MUST_FIX = ("critical", "major")
MAX_FIELD_WORDS = 60

# ------------------------------------------------------------------ evaluation chain

CHAIN_STEPS = ("criterion", "mechanism", "consequence", "judgement")
_CONSEQUENCE_MARKERS = ["this means", "as a result", "so that", "which would", "leading to",
                        "the effect is", "in turn", "this reduces", "this increases"]
_JUDGEMENT_MARKERS = ["on balance", "overall", "therefore", "is justified", "outweigh",
                      "most significant", "should", "the strongest", "i conclude", "in conclusion"]


# ------------------------------------------------------------------ mastery detector

_DEMONSTRATED_PATTERNS = {
    "limitation_acknowledged": [
        "does not isolate", "cannot be isolated", "may be due to other factors",
        "not necessarily", "correlation does not", "difficult to attribute",
        "although it does not", "we cannot conclude", "may not reflect",
        "cannot be certain", "other changes in consumer behaviour",
        "other factors may", "confound",
    ],
    "counterargument_present": ["however", "on the other hand", "conversely", "critics"],
    "elasticity_reasoning": ["elastic", "inelastic", "elasticity"],
}


def detect_demonstrated(draft: str) -> list[dict]:
    """What has the student ALREADY done well? Fed to the coach so that
    demonstrated moves are credited, not re-raised as defects."""
    low = re.sub(r"\s+", " ", draft.lower())
    out = []
    for move, pats in _DEMONSTRATED_PATTERNS.items():
        hit = next((p for p in pats if p in low), None)
        if hit:
            out.append({"move": move, "signal": hit})
    stakeholders = [w for w in ("consumers", "producers", "government", "taxpayers", "workers")
                    if w in low]
    if len(stakeholders) >= 2:
        out.append({"move": "stakeholder_weighing", "signal": ", ".join(stakeholders)})
    return out


def detect_evaluation_chain(draft: str) -> dict:
    """Deterministic presence check for criterion -> mechanism -> consequence -> judgement."""
    low = re.sub(r"\s+", " ", draft.lower())
    cues = load_rubric()["evaluation_cues"]
    present = {
        "criterion": any(kw in low for kws in cues.values() for kw in kws),
        "mechanism": any(m in low for m in CAUSAL_MARKERS),
        "consequence": any(m in low for m in _CONSEQUENCE_MARKERS),
        "judgement": any(m in low for m in _JUDGEMENT_MARKERS),
    }
    return {"present": present, "missing": [s for s in CHAIN_STEPS if not present[s]]}


# ------------------------------------------------------------------ contract validation

def _evidence_grounded(evidence: str, draft: str, det_ids: set[str]) -> bool:
    ev = evidence.strip()
    m = re.match(r"check:\s*([a-z_]+)", ev)
    if m:
        return m.group(1) in det_ids
    m = re.match(r"quote:\s*(.+)", ev, re.DOTALL)
    if m:
        return _norm(m.group(1)) in _norm(draft)
    return False


HEDGE_WORDS = ("may", "might", "probably", "perhaps", "maybe", "partly",
               "potentially", "possibly", "could be", "i think", "i do not know",
               "not certain", "uncertain")


def _quote_text(evidence: str) -> str | None:
    m = re.match(r"quote:\s*(.+)", evidence.strip(), re.DOTALL)
    return _norm(m.group(1)) if m else None


def _is_hedged(norm_quote: str) -> bool:
    padded = f" {norm_quote} "
    return any(f" {w} " in padded for w in HEDGE_WORDS)


def calibration_violations(findings: list[dict], draft: str) -> list[dict]:
    """v0.3.2 severity-calibration rules, mechanical and auditable.

    demonstrated_quote_downgrade: a quote that IS the student's demonstrated move
      (limitation acknowledged) cannot carry more than 'opportunity' - unless the
      finding re-anchors to a different, genuinely incomplete step (the retry lets
      the model re-quote).
    hedged_claim_downgrade: the student already qualified the claim (may/probably/
      partly...) - a non-critical finding on that quote caps at 'minor'. critical
      stays available: a hedge does not immunise a conclusion that still fails.
    """
    demo_signals = [_norm(d["signal"]) for d in detect_demonstrated(draft)
                    if d["move"] == "limitation_acknowledged"]
    out = []
    for i, f in enumerate(findings):
        q = _quote_text(f.get("evidence", ""))
        if not q:
            continue
        sev = f.get("severity")
        if sev != "opportunity" and any(s in q for s in demo_signals):
            out.append({"index": i, "rule": "demonstrated_quote_downgrade",
                        "original_severity": sev,
                        "detail": f"findings[{i}]: the quoted text is a move the student has "
                                  "already demonstrated (limitation acknowledged) - at most "
                                  "'opportunity', or re-anchor the finding to the step that is "
                                  "actually incomplete"})
        elif sev == "major" and _is_hedged(q):
            out.append({"index": i, "rule": "hedged_claim_downgrade",
                        "original_severity": sev,
                        "detail": f"findings[{i}]: the student already hedged this claim - "
                                  "cap at 'minor' unless the hedged conclusion still fails "
                                  "outright (then use 'critical' and say why)"})
    return out


def _leaked_terms(question: str, allowed_text: str) -> list[str]:
    """Economics lexicon terms used in the question but absent from the student's
    own text - i.e. the coach naming the answer instead of eliciting it."""
    lexicon = load_rubric()["terminology_lexicon"]
    q, allowed = question.lower(), allowed_text.lower()
    return [t for t in lexicon if t in q and t not in allowed]


def validate_coach_output(payload: dict, draft: str, det_ids: set[str],
                          expected_stage: str | None = None,
                          focus_question: str = "") -> list[str]:
    problems: list[str] = []
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return ["findings must be a list"]
    if len(findings) > 8:
        problems.append("more than 8 findings - coach must prioritise")
    allowed_text = draft + " " + focus_question
    for i, f in enumerate(findings):
        if f.get("stage") not in STAGES:
            problems.append(f"findings[{i}]: bad stage {f.get('stage')!r}")
        elif expected_stage and f["stage"] != expected_stage:
            problems.append(f"findings[{i}]: stage must be {expected_stage!r}")
        if f.get("severity") not in SEVERITIES:
            problems.append(f"findings[{i}]: bad severity {f.get('severity')!r} "
                            f"(use one of {'/'.join(SEVERITIES)})")
        q = f.get("question", "")
        if not q.rstrip().endswith("?"):
            problems.append(f"findings[{i}]: question must end with '?'")
        leaked = _leaked_terms(q, allowed_text)
        if leaked:
            problems.append(f"findings[{i}]: question names concepts absent from the student's "
                            f"text ({', '.join(leaked[:3])}) - ask a question that lets the "
                            "student find the concept themselves")
        if not _evidence_grounded(f.get("evidence", ""), draft, det_ids):
            problems.append(f"findings[{i}]: evidence not grounded "
                            "(use 'check:<id>' or 'quote:<verbatim draft text>')")
        for field in ("finding", "evidence", "question"):
            if _word_len(f.get(field, "")) > MAX_FIELD_WORDS:
                problems.append(f"findings[{i}]: {field} over {MAX_FIELD_WORDS} words")
    blob = str(payload).lower()
    for marker in GHOSTWRITE_MARKERS:
        if marker in blob:
            problems.append(f"ghostwriting marker detected: {marker!r}")
    for v in calibration_violations(findings, draft):
        problems.append(f"calibration:{v['rule']}: {v['detail']}")
    return problems


# ------------------------------------------------------------------ deterministic coach

_QUESTION_BANK = {
    "focus_question": "Which single variable, actor and time frame is your question actually about?",
    "source_name": "Where exactly did this article come from, and would the portfolio rule accept it?",
    "pub_date": "Was this article published inside the one-year window for this commentary?",
    "word_limit": "Which paragraph does the least analytical work per word it spends?",
    "diagram": "Which relationship in your argument could a labelled diagram make explicit?",
    "terminology": "Where could a precise economic term replace an everyday phrase?",
    "key_concept": "Which of the nine key concepts is actually doing work in your argument?",
    "application": "Which specific number or actor from the article does your analysis hang on?",
    "evaluation": "Whose losses, or what time frame, would weaken your judgement if taken seriously?",
    "claims": "What evidence would let this claim survive a sceptical reader?",
    "structure": "What is the one job of each paragraph, in order?",
}
_CHAIN_QUESTIONS = {
    "criterion": "Against which criterion (stakeholders, time frame, magnitude) are you judging this?",
    "mechanism": "What is the step-by-step mechanism from the policy to the outcome you assert?",
    "consequence": "What follows concretely if your mechanism holds - for whom, by how much?",
    "judgement": "What is your position, and which argument carries the most weight for it?",
}
_STAGE_OF_CHECK = {
    "focus_question": "rq", "source_name": "rq", "pub_date": "rq",
    "claims": "claim",
    "evaluation": "evaluation",
}


def deterministic_coach(stage: str, draft: str, preflight: list[dict]) -> list[dict]:
    """Map deterministic detections into contract-shaped findings. Fully offline."""
    findings: list[dict] = []
    for c in preflight:
        check_stage = _STAGE_OF_CHECK.get(c["id"], "draft")
        if check_stage != stage or c["status"] != "warn":
            continue
        findings.append({
            "stage": stage,
            "finding": c["detail"],
            "evidence": f"check:{c['id']}",
            "question": _QUESTION_BANK.get(c["id"], "What would resolve this warning?"),
            "severity": "major" if c["id"] in ("claims", "application", "pub_date") else "minor",
        })
    if stage == "claim":
        for cl in map_claims(draft):
            if not cl["supported"]:
                hedged = _is_hedged(_norm(cl["sentence"]))
                findings.append({
                    "stage": "claim",
                    "finding": (f"{cl['type']} claim has no evidence signal in or next to it."
                                + (" (Student already hedged it - severity capped.)" if hedged else "")),
                    "evidence": f"quote:{cl['sentence']}",
                    "question": "What evidence would justify this claim - or should its strength come down?",
                    "severity": "minor" if hedged else "major",
                })
    if stage == "evaluation":
        chain = detect_evaluation_chain(draft)
        for step in chain["missing"]:
            findings.append({
                "stage": "evaluation",
                "finding": f"Evaluation chain is missing the '{step}' step "
                           "(criterion -> mechanism -> consequence -> judgement).",
                "evidence": "check:evaluation_chain",
                "question": _CHAIN_QUESTIONS[step],
                "severity": "major" if step in ("mechanism", "judgement") else "minor",
            })
    return findings[:8]


# ------------------------------------------------------------------ AI coach

_COACH_SYSTEM = """You are an economics reasoning coach inside a deterministic IA workflow.

You are given machine detections (already shown to the student) and the student's text.
Your job is to find what the machine cannot: reasoning gaps. You answer ONE question only:
"which step of the student's reasoning is not yet proven?" - never "how should it be written".

Hard rules:
1. NEVER rewrite, draft, or complete any student text. No suggested sentences.
2. Every finding must be grounded: its "evidence" field is either "check:<detection id>"
   (one of: {det_ids}) or "quote:<verbatim phrase copied from the student's text>".
3. Every finding ends with one question the student must answer themselves.
4. Distinguish precisely (use in "finding"): evidence missing / evidence adjacent but
   insufficient / claim overreach (claim asserts more than evidence supports) /
   evaluation claim unsupported / missing reasoning step.
5. At most 5 findings, most important first. Fields under {max_words} words.
6. severity calibration - "must fix" and "could deepen" are different things:
   critical    = if unfixed, the core argument fails
   major       = clear defect in logic, evidence use, or economic reasoning
   minor       = a real problem, but the core argument survives it
   opportunity = the student is ALREADY CORRECT here; there is room to deepen.
   Never label depth-potential as major: "this could go further" is opportunity,
   not a defect. Reserve critical/major for things that genuinely break marks.
7. Mastery awareness: the input lists what the student has ALREADY DEMONSTRATED.
   Never raise a demonstrated move as a defect. If you touch it, start the finding
   by crediting it ("Good: limitation acknowledged...") and mark it "opportunity"
   with the next reasoning frontier as the question.
8. Never name an economic concept in your question that the student has not used
   in their own text - elicit it ("What potential costs might weaken your
   judgement?"), do not teach it ("Consider regressive effects").

Respond ONLY with JSON:
{{"findings": [{{"stage": "{stage}", "finding": "...", "evidence": "check:... or quote:...",
"question": "...?", "severity": "major"}}]}}
An empty findings list is a valid answer when the stage is genuinely solid."""

_STAGE_TASK = {
    "rq": ("Assess the focus question: are the variable, causal direction, time frame and "
           "economic concept focused enough to be answerable in 800 words? The mechanical "
           "checks passed; look for economic focus problems they cannot see."),
    "claim": ("Compare each claim's strength with its evidence's strength. The claim map "
              "marks adjacency, not sufficiency: adjacent evidence may still not support "
              "the full assertion (e.g. quantity evidence used for a welfare claim)."),
    "evaluation": ("Check the evaluation chain criterion -> mechanism -> consequence -> "
                   "judgement. Name the missing or weakest step. Do not supply the step's "
                   "content - expose that it is missing."),
    "draft": ("Give per-paragraph feedback. Every finding must cite a detection id or "
              "quote the paragraph. Point at reasoning, not wording."),
}


def _bundle_text(draft: str, article: str, focus_question: str,
                 preflight: list[dict], stage: str) -> str:
    det_lines = "\n".join(f"- [{c['id']}] {c['status']}: {c['detail']}" for c in preflight)
    claims = map_claims(draft)
    claim_lines = "\n".join(
        f"- ({c['type']}, {'supported' if c['supported'] else 'UNSUPPORTED'}) \"{c['sentence']}\""
        for c in claims) or "(no claims detected)"
    chain = detect_evaluation_chain(draft)
    demonstrated = detect_demonstrated(draft)
    demo_lines = "\n".join(f"- {d['move']} (signal: \"{d['signal']}\")"
                           for d in demonstrated) or "(none detected)"
    paras = [p.strip() for p in re.split(r"\n\s*\n", draft.strip()) if p.strip()]
    para_lines = "\n".join(f"[P{i+1}] {p}" for i, p in enumerate(paras))
    return (f"TASK\n{_STAGE_TASK[stage]}\n\n"
            f"FOCUS QUESTION\n{focus_question or '(none)'}\n\n"
            f"MACHINE DETECTIONS\n{det_lines}\n\n"
            f"CLAIM MAP\n{claim_lines}\n\n"
            f"EVALUATION CHAIN missing steps: {', '.join(chain['missing']) or 'none'}\n\n"
            f"ALREADY DEMONSTRATED BY THE STUDENT (rule 7 applies - never a defect)\n{demo_lines}\n\n"
            f"ARTICLE (context)\n{article.strip() or '(not provided)'}\n\n"
            f"STUDENT TEXT (by paragraph)\n{para_lines}")


def ai_coach(stage: str, draft: str, article: str, focus_question: str,
             preflight: list[dict], _chat=chat) -> list[dict]:
    det_ids = {c["id"] for c in preflight} | {"evaluation_chain"}
    system = _COACH_SYSTEM.format(det_ids=", ".join(sorted(det_ids)),
                                  max_words=MAX_FIELD_WORDS, stage=stage)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": _bundle_text(draft, article, focus_question,
                                                         preflight, stage)}]
    last: list[str] = []
    calibration_log: list[dict] = []
    for _ in range(2):
        raw = _chat(messages)
        payload = extract_json(raw)
        problems = validate_coach_output(payload, draft, det_ids, expected_stage=stage,
                                         focus_question=focus_question)
        # audit trail: which calibration rules fired on this (possibly rejected) attempt
        calibration_log.extend(calibration_violations(payload.get("findings", []), draft))
        if not problems:
            return payload["findings"], calibration_log
        last = problems
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Contract violations: " + "; ".join(problems[:5])
             + ". Re-emit the full JSON, fixed. Ground every evidence field; never rewrite student text."}]
    raise ValueError("coach output failed contract: " + "; ".join(last[:5]))


def run_coach(stage: str, draft: str, article: str = "", focus_question: str = "",
              preflight: list[dict] | None = None, use_ai: bool = True, _chat=chat) -> dict:
    """One coach pass. AI when configured; deterministic fallback with the same shape.
    Never mutates preflight - mechanical results are computed upstream and stay authoritative."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    preflight = preflight or []
    from . import llm
    if use_ai and llm.is_configured():
        try:
            findings, calibration = ai_coach(stage, draft, article, focus_question,
                                             preflight, _chat=_chat)
            return {"stage": stage, "source": "ai", "findings": findings,
                    "fallback_reason": None, "calibration": calibration}
        except Exception as exc:
            det = deterministic_coach(stage, draft, preflight)
            return {"stage": stage, "source": "deterministic", "findings": det,
                    "fallback_reason": f"AI coach unavailable: {exc}", "calibration": []}
    det = deterministic_coach(stage, draft, preflight)
    reason = None if not use_ai else "no API key configured"
    return {"stage": stage, "source": "deterministic", "findings": det,
            "fallback_reason": reason, "calibration": []}


# ------------------------------------------------------------------ cross-stage dedup

_STOPWORDS = frozenset(
    "the a an of to in on for and or but is are was were be been that this it its "
    "with as by not no may might can could should would does do did has have had "
    "student students claim evidence".split())


def _content_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _quote_of(evidence: str) -> str | None:
    m = re.match(r"quote:\s*(.+)", evidence.strip(), re.DOTALL)
    return _norm(m.group(1)) if m else None


def _same_issue(f1: dict, f2: dict) -> bool:
    q1, q2 = _quote_of(f1.get("evidence", "")), _quote_of(f2.get("evidence", ""))
    if q1 and q2 and (q1 in q2 or q2 in q1 or _jaccard(set(q1.split()), set(q2.split())) >= 0.6):
        return True
    if not q1 and not q2 and f1.get("evidence") == f2.get("evidence"):
        return True
    return _jaccard(_content_tokens(f1.get("finding", "")),
                    _content_tokens(f2.get("finding", ""))) >= 0.45


def aggregate_core_findings(results: list[dict]) -> list[dict]:
    """Merge per-stage findings into core findings: one issue appears once, at its
    highest severity, with each stage's view folded underneath."""
    groups: list[list[tuple[str, dict]]] = []
    for res in results:
        for f in res["findings"]:
            for g in groups:
                if _same_issue(g[0][1], f):
                    g.append((res["stage"], f))
                    break
            else:
                groups.append([(res["stage"], f)])
    core = []
    for i, g in enumerate(groups, 1):
        primary_stage, primary = max(g, key=lambda x: SEVERITY_RANK.get(x[1]["severity"], 0))
        core.append({
            "n": i,
            "severity": primary["severity"],
            "must_fix": primary["severity"] in MUST_FIX,
            "finding": primary["finding"],
            "evidence": primary["evidence"],
            "question": primary["question"],
            "stages": sorted({s for s, _ in g}),
            "views": [{"stage": s, "finding": f["finding"], "question": f["question"],
                       "severity": f["severity"]}
                      for s, f in g if f is not primary],
        })
    core.sort(key=lambda c: -SEVERITY_RANK.get(c["severity"], 0))
    for i, c in enumerate(core, 1):
        c["n"] = i
    return core
