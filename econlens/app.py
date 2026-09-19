"""EconLens web app.

Run:  uvicorn econlens.app:app --port 8760
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import llm, store
from .coach import STAGES, aggregate_core_findings, run_coach
from .feedback import get_feedback
from .preflight import run_preflight
from .report import render_report
from .rubric import load_rubric
from .workspace import UNITS, check_focus_question, check_source, portfolio_status

app = FastAPI(title="EconLens", version="0.3.0")
STATIC = Path(__file__).resolve().parent.parent / "static"


class ReviewRequest(BaseModel):
    session_id: str | None = None
    title: str = ""
    article: str = ""
    draft: str
    use_ai: bool = False
    # workspace metadata (all optional)
    unit: str = ""
    key_concept: str = ""
    source_name: str = ""
    pub_date: str = ""
    focus_question: str = ""


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config():
    rubric = load_rubric()
    return {
        "ai_enabled": llm.is_configured(),
        "word_limit": rubric["word_limit"],
        "criteria": [{"id": c["id"], "name": c["name"], "max": c["max"],
                      "checklist": c["coach_checklist"]} for c in rubric["criteria"]],
        "key_concepts": rubric["key_concepts"],
        "units": UNITS,
        "verify_note": rubric["verify_note"],
    }


@app.get("/api/sessions")
def sessions():
    return store.list_sessions()


@app.post("/api/review")
def review(req: ReviewRequest):
    if not req.draft.strip():
        raise HTTPException(422, "draft is empty")
    if req.session_id:
        try:
            session = store.load_session(req.session_id)
        except FileNotFoundError:
            raise HTTPException(404, "session not found")
    else:
        session = store.create_session(req.title)

    meta = {"unit": req.unit, "key_concept": req.key_concept,
            "source_name": req.source_name, "pub_date": req.pub_date,
            "focus_question": req.focus_question}
    store.update_meta(session, meta)

    workspace_checks = [check_focus_question(req.focus_question)] + check_source(meta)
    preflight = workspace_checks + run_preflight(req.draft, req.article)

    feedback = None
    feedback_error = None
    if req.use_ai:
        if not llm.is_configured():
            feedback_error = "AI feedback is off: no API key configured (see README)."
        else:
            try:
                feedback = get_feedback(req.article, req.draft)
            except Exception as exc:  # surfaced to the UI, round still recorded
                feedback_error = f"AI feedback failed: {exc}"

    round_rec = store.add_round(session, req.draft, req.article, preflight, feedback)
    claims = next((c.get("claims", []) for c in preflight if c["id"] == "claims"), [])
    return {
        "session_id": session["id"],
        "round": round_rec["n"],
        "similarity_to_previous": round_rec["similarity_to_previous"],
        "preflight": preflight,
        "claims": claims,
        "feedback": feedback,
        "feedback_error": feedback_error,
    }


@app.get("/api/portfolio")
def portfolio():
    sessions = store.list_sessions()
    metas = [{"title": s["title"], **s.get("meta", {})} for s in sessions]
    return {"sessions": sessions, "status": portfolio_status(metas)}


class CoachRequest(BaseModel):
    session_id: str | None = None
    title: str = ""
    article: str = ""
    draft: str
    focus_question: str = ""
    source_name: str = ""
    pub_date: str = ""
    stage: str = "all"           # "all" or one of rq/claim/evaluation/draft


class CoachActionRequest(BaseModel):
    session_id: str
    log_id: str
    action: str                  # accepted / dismissed / revised
    note: str = ""


@app.post("/api/coach")
def coach(req: CoachRequest):
    if not req.draft.strip():
        raise HTTPException(422, "draft is empty")
    stages = list(STAGES) if req.stage == "all" else [req.stage]
    if any(s not in STAGES for s in stages):
        raise HTTPException(422, f"stage must be 'all' or one of {STAGES}")
    if req.session_id:
        try:
            session = store.load_session(req.session_id)
        except FileNotFoundError:
            raise HTTPException(404, "session not found")
    else:
        session = store.create_session(req.title)

    # mechanical results first - the coach never changes them
    meta = {"source_name": req.source_name, "pub_date": req.pub_date,
            "focus_question": req.focus_question}
    preflight = ([check_focus_question(req.focus_question)] + check_source(meta)
                 + run_preflight(req.draft, req.article))

    results = []
    for stage in stages:
        res = run_coach(stage, req.draft, req.article, req.focus_question, preflight)
        entry = store.add_coach_log(session, stage, res["source"], req.draft,
                                    preflight, res["findings"], res["fallback_reason"],
                                    calibration=res.get("calibration"))
        res["log_id"] = entry["id"]
        results.append(res)
    core = aggregate_core_findings(results) if len(results) > 1 else None
    return {"session_id": session["id"], "preflight": preflight, "results": results,
            "core_findings": core}


@app.post("/api/coach/action")
def coach_action(req: CoachActionRequest):
    try:
        session = store.load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(404, "session not found")
    try:
        entry = store.record_coach_action(session, req.log_id, req.action, req.note)
    except KeyError:
        raise HTTPException(404, "log entry not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "log_id": entry["id"], "action": entry["action"]}


@app.get("/api/report/{session_id}", response_class=HTMLResponse)
def report(session_id: str):
    try:
        session = store.load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(404, "session not found")
    return HTMLResponse(render_report(session))
