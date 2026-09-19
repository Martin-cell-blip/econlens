import json

import pytest

from econlens.feedback import build_messages, get_feedback, validate_feedback
from econlens.llm import extract_json

DRAFT = ("The tax shifts the supply curve upward as Figure 1 shows. "
         "Low-income households spend a larger share of income on these drinks.")


def make_payload(**overrides):
    base = {
        "criteria": [
            {"id": cid, "band_estimate": {"low": 1, "high": mx},
             "strengths": ["Clear link to the article's figures."],
             "issues": [{"quote": "shifts the supply curve upward",
                         "why": "The direction is stated but the mechanism is not explained.",
                         "socratic_question": "Why does a per-unit tax shift supply upward by the tax amount?"}]}
            for cid, mx in [("A", 3), ("B", 2), ("C", 2), ("D", 3), ("E", 4)]
        ],
        "next_focus": "Explain the mechanism behind each shift.",
    }
    base.update(overrides)
    return base


def test_valid_payload_passes():
    assert validate_feedback(make_payload(), DRAFT) == []


def test_quote_must_exist_in_draft():
    p = make_payload()
    p["criteria"][0]["issues"][0]["quote"] = "this phrase is not in the draft"
    problems = validate_feedback(p, DRAFT)
    assert any("quote not found" in x for x in problems)


def test_quote_matching_ignores_case_and_punct():
    p = make_payload()
    p["criteria"][0]["issues"][0]["quote"] = "Shifts the SUPPLY curve, upward"
    assert validate_feedback(p, DRAFT) == []


def test_band_bounds_enforced():
    p = make_payload()
    p["criteria"][0]["band_estimate"] = {"low": 0, "high": 9}
    problems = validate_feedback(p, DRAFT)
    assert any("band_estimate" in x for x in problems)


def test_all_criteria_required():
    p = make_payload()
    p["criteria"] = p["criteria"][:3]
    problems = validate_feedback(p, DRAFT)
    assert any("missing criteria" in x for x in problems)


def test_ghostwriting_markers_rejected():
    p = make_payload()
    p["criteria"][0]["strengths"] = ["Here is a revised opening you could use."]
    problems = validate_feedback(p, DRAFT)
    assert any("ghostwriting marker" in x for x in problems)


def test_overlong_fields_rejected():
    p = make_payload()
    p["criteria"][0]["issues"][0]["why"] = "word " * 60
    problems = validate_feedback(p, DRAFT)
    assert any("over 45 words" in x for x in problems)


def test_socratic_question_must_be_question():
    p = make_payload()
    p["criteria"][0]["issues"][0]["socratic_question"] = "You should explain the mechanism."
    problems = validate_feedback(p, DRAFT)
    assert any("must end with" in x for x in problems)


# ---------- prompt & pipeline with mock LLM ----------

def test_build_messages_contains_rubric_and_texts():
    msgs = build_messages("ARTICLE BODY", DRAFT)
    assert msgs[0]["role"] == "system"
    assert "NEVER write" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "Criterion A" in user and "Criterion E" in user
    assert "ARTICLE BODY" in user and "supply curve" in user


def test_get_feedback_happy_path_with_mock():
    payload = make_payload()

    def fake_chat(messages, temperature=0.2):
        return json.dumps(payload)

    out = get_feedback("article", DRAFT, _chat=fake_chat)
    assert out["next_focus"].startswith("Explain")


def test_get_feedback_retries_then_fails_on_bad_model():
    bad = make_payload()
    bad["criteria"][0]["issues"][0]["quote"] = "hallucinated quote"
    calls = {"n": 0}

    def fake_chat(messages, temperature=0.2):
        calls["n"] += 1
        return json.dumps(bad)

    with pytest.raises(ValueError, match="contract-compliant"):
        get_feedback("article", DRAFT, _chat=fake_chat)
    assert calls["n"] == 2  # one retry with violation feedback


def test_get_feedback_retry_recovers():
    bad = make_payload()
    bad["criteria"][0]["issues"][0]["quote"] = "hallucinated quote"
    good = make_payload()
    responses = [json.dumps(bad), json.dumps(good)]

    def fake_chat(messages, temperature=0.2):
        return responses.pop(0)

    out = get_feedback("article", DRAFT, _chat=fake_chat)
    assert validate_feedback(out, DRAFT) == []


def test_extract_json_tolerates_code_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('noise {"a": {"b": 2}} trailing') == {"a": {"b": 2}}
