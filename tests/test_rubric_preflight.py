from pathlib import Path

import pytest

from econlens.preflight import (
    check_application_overlap, check_diagram_reference, check_evaluation_cues,
    check_key_concept, check_terminology, check_word_limit, run_preflight, word_count,
)
from econlens.rubric import criterion_map, load_rubric

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
ARTICLE = (SAMPLES / "article_sugar_tax.txt").read_text(encoding="utf-8")
DRAFT = (SAMPLES / "draft_round1.txt").read_text(encoding="utf-8")


# ---------- rubric ----------

def test_rubric_loads_and_is_consistent():
    rubric = load_rubric()
    assert sum(c["max"] for c in rubric["criteria"]) == 14
    assert rubric["portfolio"]["total_marks"] == 45
    assert len(rubric["key_concepts"]) == 9


def test_criterion_map_ids():
    assert sorted(criterion_map()) == ["A", "B", "C", "D", "E"]


# ---------- word count ----------

def test_word_count_basic():
    assert word_count("The cat sat on the mat.") == 6


def test_word_limit_over():
    long_text = "word " * 900
    res = check_word_limit(long_text, 800)
    assert res["status"] == "warn"
    assert "over" in res["detail"]


def test_word_limit_sample_draft_under_limit():
    res = check_word_limit(DRAFT, 800)
    # the sample round-1 draft is deliberately compact: under the limit,
    # flagged "info" (room to develop) rather than "warn"
    assert res["status"] in ("pass", "info")
    assert res["value"] < 800


# ---------- individual checks ----------

def test_diagram_reference_found_in_sample():
    assert check_diagram_reference(DRAFT)["status"] == "pass"


def test_diagram_reference_missing():
    assert check_diagram_reference("no visual aid here at all")["status"] == "warn"


def test_terminology_rich_sample():
    res = check_terminology(DRAFT)
    assert res["status"] == "pass"
    assert "negative externality" in res["terms"]
    assert res["value"] >= 6


def test_terminology_sparse():
    assert check_terminology("This tax is bad for people.")["status"] == "warn"


def test_key_concept_detected():
    res = check_key_concept(DRAFT)
    assert res["status"] == "pass"
    assert "intervention" in res["value"]


def test_key_concept_missing():
    assert check_key_concept("supply and demand shift around")["status"] == "warn"


def test_application_overlap_engages_article():
    res = check_application_overlap(DRAFT, ARTICLE)
    assert res["status"] == "pass"
    assert res["value"] > 0


def test_application_overlap_parallel_essay():
    generic = "Demand curves slope downward. Supply curves slope upward. Equilibrium occurs where they cross."
    assert check_application_overlap(generic, ARTICLE)["status"] == "warn"


def test_application_overlap_no_article():
    assert check_application_overlap(DRAFT, "")["status"] == "info"


def test_evaluation_cues_sample():
    res = check_evaluation_cues(DRAFT)
    assert res["status"] == "pass"
    assert {"stakeholders", "time_frame"} <= set(res["value"])


def test_evaluation_cues_absent():
    assert check_evaluation_cues("The tax shifts supply left. Price rises.")["status"] == "warn"


# ---------- full run ----------

def test_run_preflight_shape():
    results = run_preflight(DRAFT, ARTICLE)
    ids = [r["id"] for r in results]
    assert ids == ["word_limit", "diagram", "terminology", "key_concept",
                   "application", "evaluation", "claims", "structure"]
    for r in results:
        assert r["status"] in ("pass", "warn", "info")
        assert r["detail"]
