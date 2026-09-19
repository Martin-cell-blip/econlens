import pytest
from fastapi.testclient import TestClient

import econlens.store as store_mod
from econlens.app import app
from econlens.report import render_report


@pytest.fixture(autouse=True)
def tmp_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "DATA_DIR", tmp_path / "sessions")
    yield


client = TestClient(app)


# ---------- store ----------

def test_session_roundtrip():
    s = store_mod.create_session("Unit 2 sugar tax")
    store_mod.add_round(s, "draft one text", "article", [], None)
    loaded = store_mod.load_session(s["id"])
    assert loaded["title"] == "Unit 2 sugar tax"
    assert loaded["rounds"][0]["word_count"] == 3
    assert loaded["rounds"][0]["similarity_to_previous"] is None
    assert len(loaded["rounds"][0]["draft_sha256"]) == 64


def test_similarity_between_rounds():
    s = store_mod.create_session()
    store_mod.add_round(s, "the quick brown fox jumps over the lazy dog", "", [], None)
    r2 = store_mod.add_round(s, "the quick brown fox jumps over the lazy cat", "", [], None)
    assert 0.8 < r2["similarity_to_previous"] < 1.0


def test_invalid_session_id_rejected():
    with pytest.raises(ValueError):
        store_mod.load_session("../../etc/passwd")


def test_list_sessions():
    store_mod.create_session("one")
    store_mod.create_session("two")
    assert {s["title"] for s in store_mod.list_sessions()} == {"one", "two"}


# ---------- report ----------

def test_report_contains_rounds_and_disclaimer():
    s = store_mod.create_session("Report test")
    store_mod.add_round(s, "some draft words", "", [
        {"id": "word_limit", "label": "Word count", "status": "pass", "detail": "ok"}], None)
    html = render_report(store_mod.load_session(s["id"]))
    assert "Report test" in html
    assert "does not, by itself,\nprove authorship" in html or "prove authorship" in html
    assert "SHA-256" in html


# ---------- api ----------

def test_api_config():
    cfg = client.get("/api/config").json()
    assert cfg["word_limit"] == 800
    assert [c["id"] for c in cfg["criteria"]] == ["A", "B", "C", "D", "E"]
    assert cfg["ai_enabled"] in (True, False)


def test_api_review_offline_creates_session_and_rounds():
    r1 = client.post("/api/review", json={"draft": "As Figure 1 shows, demand is elastic.", "article": ""})
    assert r1.status_code == 200
    body = r1.json()
    assert body["round"] == 1
    assert any(c["id"] == "diagram" and c["status"] == "pass" for c in body["preflight"])

    r2 = client.post("/api/review", json={
        "session_id": body["session_id"],
        "draft": "As Figure 1 shows, demand is elastic for teenagers.", "article": ""})
    assert r2.json()["round"] == 2
    assert r2.json()["similarity_to_previous"] > 0.5


def test_api_review_empty_draft_422():
    assert client.post("/api/review", json={"draft": "   "}).status_code == 422


def test_api_review_ai_without_key(monkeypatch):
    monkeypatch.delenv("ECONLENS_API_KEY", raising=False)
    r = client.post("/api/review", json={"draft": "some draft", "use_ai": True})
    assert r.status_code == 200
    assert "no API key" in r.json()["feedback_error"]


def test_api_report_endpoint():
    sid = client.post("/api/review", json={"draft": "hello economics world"}).json()["session_id"]
    r = client.get(f"/api/report/{sid}")
    assert r.status_code == 200
    assert "process archive" in r.text


def test_api_report_missing_404():
    assert client.get("/api/report/deadbeef0000").status_code == 404


def test_api_review_with_workspace_meta_and_claims():
    r = client.post("/api/review", json={
        "draft": "The tax leads to a 22% fall in sales, according to the article.",
        "unit": "Unit 2: Microeconomics", "key_concept": "intervention",
        "source_name": "FT", "pub_date": "2026-05-01",
        "focus_question": "To what extent will the indirect tax reduce demand?"})
    assert r.status_code == 200
    body = r.json()
    ids = [c["id"] for c in body["preflight"]]
    assert ids[:3] == ["focus_question", "source_name", "pub_date"]
    assert "claims" in ids
    assert body["claims"] and body["claims"][0]["supported"] is True


def test_api_portfolio_aggregates_sessions():
    client.post("/api/review", json={"draft": "draft one", "title": "C1",
                                     "unit": "Unit 2: Microeconomics",
                                     "key_concept": "intervention", "source_name": "FT"})
    client.post("/api/review", json={"draft": "draft two", "title": "C2",
                                     "unit": "Unit 2: Microeconomics",
                                     "key_concept": "equity", "source_name": "Reuters"})
    data = client.get("/api/portfolio").json()
    assert len(data["sessions"]) == 2
    by_id = {c["id"]: c for c in data["status"]["checks"]}
    assert by_id["units"]["status"] == "warn"  # duplicate unit across the two


def test_meta_persists_on_session():
    r = client.post("/api/review", json={"draft": "some words here",
                                         "key_concept": "efficiency"})
    sid = r.json()["session_id"]
    sessions = client.get("/api/sessions").json()
    me = next(s for s in sessions if s["id"] == sid)
    assert me["meta"]["key_concept"] == "efficiency"
