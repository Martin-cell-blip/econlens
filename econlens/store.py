"""Session persistence: every round is recorded for the process/integrity archive."""
from __future__ import annotations

import difflib
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .preflight import word_count

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"


def _path(session_id: str) -> Path:
    if not session_id.replace("-", "").isalnum():
        raise ValueError("invalid session id")
    return DATA_DIR / f"{session_id}.json"


META_FIELDS = ("unit", "key_concept", "source_name", "pub_date", "focus_question")


def create_session(title: str = "") -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = {
        "id": uuid.uuid4().hex[:12],
        "title": title or "Untitled commentary",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "meta": {k: "" for k in META_FIELDS},
        "rounds": [],
    }
    save_session(session)
    return session


def update_meta(session: dict, meta: dict) -> None:
    current = session.setdefault("meta", {k: "" for k in META_FIELDS})
    for k in META_FIELDS:
        if k in meta and meta[k] is not None:
            current[k] = str(meta[k]).strip()
    save_session(session)


def load_session(session_id: str) -> dict:
    with open(_path(session_id), encoding="utf-8") as f:
        return json.load(f)


def save_session(session: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_path(session["id"]), "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=1)


def similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)


def add_round(session: dict, draft: str, article: str,
              preflight: list[dict], feedback: dict | None) -> dict:
    prev_draft = session["rounds"][-1]["draft"] if session["rounds"] else None
    bands = None
    if feedback:
        bands = {c["id"]: c["band_estimate"] for c in feedback.get("criteria", [])}
    round_rec = {
        "n": len(session["rounds"]) + 1,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "article_sha256": hashlib.sha256(article.encode("utf-8")).hexdigest() if article else None,
        "word_count": word_count(draft),
        "similarity_to_previous": similarity(prev_draft, draft) if prev_draft is not None else None,
        "preflight": [{k: v for k, v in c.items() if k in ("id", "label", "status", "detail")} for c in preflight],
        "bands": bands,
        "draft": draft,
    }
    session["rounds"].append(round_rec)
    save_session(session)
    return round_rec


COACH_ACTIONS = ("accepted", "dismissed", "revised")


def add_coach_log(session: dict, stage: str, source: str, draft: str,
                  preflight: list[dict], findings: list[dict],
                  fallback_reason: str | None,
                  calibration: list[dict] | None = None) -> dict:
    """Experiment record: input snapshot + deterministic findings + coach output.
    Student action and before/after arrive later (record_coach_action / next round)."""
    log = session.setdefault("coach_log", [])
    entry = {
        "id": f"c{len(log) + 1:03d}",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "round_n": len(session["rounds"]),          # round the input snapshot belongs to
        "stage": stage,
        "source": source,                            # "ai" | "deterministic"
        "fallback_reason": fallback_reason,
        "input_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "deterministic": [{"id": c["id"], "status": c["status"]} for c in preflight],
        "findings": findings,
        "calibration": calibration or [],            # severity-calibration rule firings (audit)
        "action": None,                              # {action, note, at} - student's response
    }
    log.append(entry)
    save_session(session)
    return entry


def record_coach_action(session: dict, log_id: str, action: str, note: str = "") -> dict:
    if action not in COACH_ACTIONS:
        raise ValueError(f"action must be one of {COACH_ACTIONS}")
    entry = next((e for e in session.get("coach_log", []) if e["id"] == log_id), None)
    if entry is None:
        raise KeyError(f"no coach log entry {log_id!r}")
    entry["action"] = {"action": action, "note": note,
                       "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    save_session(session)
    return entry


def list_sessions() -> list[dict]:
    if not DATA_DIR.exists():
        return []
    out = []
    for p in sorted(DATA_DIR.glob("*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
            out.append({"id": s["id"], "title": s["title"], "created_at": s["created_at"],
                        "rounds": len(s["rounds"]),
                        "meta": s.get("meta", {})})
        except (json.JSONDecodeError, KeyError):
            continue
    return out
