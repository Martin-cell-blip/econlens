from datetime import date

import pytest

from econlens.claims import check_claims, map_claims, split_sentences
from econlens.workspace import (
    check_focus_question, check_source, portfolio_status,
)


# ---------- claims ----------

def test_split_sentences():
    text = 'Price rises. Demand falls! Will revenue rise? "Yes," said the mayor.'
    assert len(split_sentences(text)) == 4


def test_causal_claim_with_evidence_in_same_sentence():
    draft = "The tax leads to a 22% fall in sales, according to the article."
    claims = map_claims(draft)
    assert len(claims) == 1
    assert claims[0]["type"] == "causal"
    assert claims[0]["supported"] is True


def test_causal_claim_supported_by_adjacent_sentence():
    draft = ("The tax will reduce consumption among teenagers. "
             "The article reports a 15% fall in a similar case.")
    claims = map_claims(draft)
    assert claims[0]["supported"] is True


def test_unsupported_evaluative_claim_flagged():
    draft = ("Clearly the government should ban these drinks entirely. "
             "Many people enjoy them very much.")
    claims = map_claims(draft)
    assert claims[0]["type"] == "evaluative"
    assert claims[0]["supported"] is False


def test_causal_plus_evaluative_combined_type():
    draft = "The subsidy causes waste and therefore should be removed immediately."
    assert map_claims(draft)[0]["type"] == "causal+evaluative"


def test_check_claims_statuses():
    assert check_claims("The sky is nice today. It is blue.")["status"] == "info"
    assert check_claims("The tax should be scrapped without question or delay.")["status"] == "warn"
    ok = check_claims("The tax leads to a 22% fall in sales, according to the article.")
    assert ok["status"] == "pass"
    assert ok["value"] == 0


# ---------- focus question ----------

def test_focus_question_empty_is_info():
    assert check_focus_question("")["status"] == "info"


def test_focus_question_good():
    rq = "To what extent will the 20% indirect tax reduce sugary drink consumption?"
    assert check_focus_question(rq)["status"] == "pass"


def test_focus_question_not_a_question():
    res = check_focus_question("The tax and its effects on demand for drinks")
    assert res["status"] == "warn"
    assert "question" in res["detail"]


def test_focus_question_no_economics():
    res = check_focus_question("Is this policy going to help people in the city?")
    assert res["status"] == "warn"
    assert "no economic variable" in res["detail"]


def test_focus_question_signal_word_without_lexicon_is_info_not_warn():
    # v0.3.1 calibration: "tax -> consumption" is plainly economic even without
    # textbook lexicon terms - suggestion, not a warning
    rq = ("To what extent has the Irish sugar-sweetened drinks tax been effective "
          "in reducing the consumption of sugary drinks in Ireland?")
    res = check_focus_question(rq)
    assert res["status"] == "info"
    assert "economic lens explicit" in res["detail"]


def test_focus_question_bad_rq_blocked():
    res = check_focus_question("Is the sugar tax good for Ireland?")
    assert res["status"] == "warn"
    assert "vague" in res["detail"] or "too short" in res["detail"]


def test_focus_question_too_long():
    rq = ("To what extent will the proposed indirect tax on sugary drinks reduce demand "
          "among teenagers and adults and also improve long term health outcomes and raise "
          "government revenue and protect employment and promote equity across all income groups involved?")
    assert check_focus_question(rq)["status"] == "warn"


# ---------- source checks ----------

TODAY = date(2026, 8, 25)


def test_source_recent_article_passes():
    checks = check_source({"source_name": "FT", "pub_date": "2026-05-01"}, today=TODAY)
    by_id = {c["id"]: c for c in checks}
    assert by_id["source_name"]["status"] == "pass"
    assert by_id["pub_date"]["status"] == "pass"


def test_source_stale_article_warns():
    checks = check_source({"source_name": "FT", "pub_date": "2024-01-01"}, today=TODAY)
    by_id = {c["id"]: c for c in checks}
    assert by_id["pub_date"]["status"] == "warn"
    assert "one-year" in by_id["pub_date"]["detail"]


def test_source_bad_date_format_warns():
    checks = check_source({"pub_date": "01/05/2026"}, today=TODAY)
    assert {c["id"]: c for c in checks}["pub_date"]["status"] == "warn"


def test_source_missing_fields_warn():
    checks = check_source({}, today=TODAY)
    assert all(c["status"] == "warn" for c in checks)


# ---------- portfolio ----------

def _meta(unit, concept, source):
    return {"title": "t", "unit": unit, "key_concept": concept, "source_name": source}


def test_portfolio_complete_and_distinct():
    metas = [
        _meta("Unit 2: Microeconomics", "intervention", "FT"),
        _meta("Unit 3: Macroeconomics", "equity", "The Economist"),
        _meta("Unit 4: The global economy", "interdependence", "Reuters"),
    ]
    status = portfolio_status(metas)
    by_id = {c["id"]: c for c in status["checks"]}
    assert by_id["count"]["status"] == "pass"
    assert by_id["units"]["status"] == "pass"
    assert by_id["key_concepts"]["status"] == "pass"
    assert by_id["sources"]["status"] == "pass"


def test_portfolio_duplicate_unit_warns():
    metas = [_meta("Unit 2: Microeconomics", "intervention", "FT"),
             _meta("Unit 2: Microeconomics", "equity", "Reuters")]
    by_id = {c["id"]: c for c in portfolio_status(metas)["checks"]}
    assert by_id["units"]["status"] == "warn"


def test_portfolio_duplicate_source_warns():
    metas = [_meta("Unit 2: Microeconomics", "intervention", "FT"),
             _meta("Unit 3: Macroeconomics", "equity", "ft")]  # case-insensitive
    by_id = {c["id"]: c for c in portfolio_status(metas)["checks"]}
    assert by_id["sources"]["status"] == "warn"


def test_portfolio_unknown_key_concept_warns():
    metas = [_meta("Unit 2: Microeconomics", "synergy", "FT")]
    by_id = {c["id"]: c for c in portfolio_status(metas)["checks"]}
    assert by_id["key_concepts"]["status"] == "warn"
    assert "synergy" in by_id["key_concepts"]["detail"]
