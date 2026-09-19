"""IA workspace checks beyond the draft text itself: focus question, source
metadata, and portfolio-level rubric requirements. Deterministic, no LLM.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from .rubric import load_rubric

VAGUE_PHRASES = ["good or bad", "affect the economy", "is good", "is bad", "good for",
                 "bad for", "help people", "the world", "everything", "in general"]

# Economic variables that make a question economic even without textbook lexicon
# terms ("tax -> consumption" is plainly an economics question). With one of
# these present, a missing lexicon term is a suggestion, not a warning.
ECON_SIGNAL_WORDS = [
    "tax", "subsidy", "price", "consumption", "demand", "supply", "market",
    "inflation", "unemployment", "revenue", "welfare", "elasticity", "tariff",
    "wage", "income", "trade", "exports", "imports", "exchange rate",
    "interest rate", "gdp", "output", "production", "cost", "spending",
]
UNITS = [
    "Unit 1: Introduction to economics",
    "Unit 2: Microeconomics",
    "Unit 3: Macroeconomics",
    "Unit 4: The global economy",
]


def check_focus_question(rq: str) -> dict:
    """Optional coaching field: a sharp focus question for the commentary."""
    rq = (rq or "").strip()
    if not rq:
        return {"id": "focus_question", "label": "Focus question", "status": "info",
                "detail": "No focus question set. Writing one sharpens the commentary: "
                          "'To what extent will the 20% tax reduce sugary-drink consumption?'"}
    low = rq.lower()
    problems = []
    if not rq.endswith("?"):
        problems.append("should be phrased as a question")
    n_words = len(re.findall(r"\S+", rq))
    if n_words < 6:
        problems.append("too short to be specific")
    elif n_words > 30:
        problems.append("over 30 words - likely trying to ask two things")
    lexicon = load_rubric()["terminology_lexicon"]
    has_lexicon = any(t in low for t in lexicon)
    has_signal = any(w in low for w in ECON_SIGNAL_WORDS)
    if not has_lexicon and not has_signal:
        problems.append("names no economic variable - what economic quantity is the question about")
    vague = [v for v in VAGUE_PHRASES if v in low]
    if vague:
        problems.append(f"vague phrasing ({', '.join(vague)}) - what measurable outcome would answer it")
    if problems:
        return {"id": "focus_question", "label": "Focus question", "status": "warn",
                "detail": "Focus question needs work: " + "; ".join(problems) + "."}
    if not has_lexicon:
        return {"id": "focus_question", "label": "Focus question", "status": "info",
                "detail": f"Focus question is economic and specific ({n_words} words). "
                          "Optional: consider making the economic lens explicit "
                          "(the theory or key concept you will apply)."}
    return {"id": "focus_question", "label": "Focus question", "status": "pass",
            "detail": f"Focus question is specific and economic ({n_words} words)."}


def check_source(meta: dict, today: date | None = None) -> list[dict]:
    """Article metadata checks against IB portfolio rules."""
    today = today or date.today()
    checks = []

    source = (meta.get("source_name") or "").strip()
    checks.append({
        "id": "source_name", "label": "Source",
        "status": "pass" if source else "warn",
        "detail": f"Source: {source}." if source
        else "No source name recorded. The portfolio needs three different sources."})

    pub_raw = (meta.get("pub_date") or "").strip()
    if not pub_raw:
        checks.append({"id": "pub_date", "label": "Article date", "status": "warn",
                       "detail": "No publication date recorded. Articles must be published "
                                 "no earlier than one year before the commentary is written."})
    else:
        try:
            pub = datetime.strptime(pub_raw, "%Y-%m-%d").date()
        except ValueError:
            checks.append({"id": "pub_date", "label": "Article date", "status": "warn",
                           "detail": f"Publication date {pub_raw!r} not in YYYY-MM-DD format."})
        else:
            age_days = (today - pub).days
            if age_days < 0:
                checks.append({"id": "pub_date", "label": "Article date", "status": "warn",
                               "detail": "Publication date is in the future - check the entry."})
            elif age_days > 365:
                checks.append({"id": "pub_date", "label": "Article date", "status": "warn",
                               "detail": f"Article is {age_days} days old - over the one-year limit "
                                         "if the commentary is written now."})
            else:
                checks.append({"id": "pub_date", "label": "Article date", "status": "pass",
                               "detail": f"Article is {age_days} days old - inside the one-year window."})
    return checks


def portfolio_status(sessions_meta: list[dict]) -> dict:
    """Cross-commentary rubric requirements over the sessions' metadata.

    Each item: {title, unit, key_concept, source_name} - empty strings allowed.
    """
    rubric = load_rubric()
    n = len(sessions_meta)
    checks = []

    def _values(field):
        return [m.get(field, "").strip() for m in sessions_meta if m.get(field, "").strip()]

    units = _values("unit")
    concepts = [c.lower() for c in _values("key_concept")]
    sources = [s.lower() for s in _values("source_name")]

    checks.append({"id": "count", "label": "Commentaries",
                   "status": "pass" if n >= 3 else "info",
                   "detail": f"{n} of 3 commentaries in the portfolio."})

    dup_units = n >= 2 and len(units) == n and len(set(units)) < len(units)
    checks.append({"id": "units", "label": "Different units",
                   "status": "warn" if dup_units else ("pass" if len(set(units)) == n and n > 0 else "info"),
                   "detail": ("Two commentaries share the same unit - each must use a different unit."
                              if dup_units else f"Units covered: {', '.join(sorted(set(units))) or 'none recorded'}.")})

    valid_concepts = set(k.lower() for k in rubric["key_concepts"])
    bad = [c for c in concepts if c not in valid_concepts]
    dup_concepts = len(concepts) == n and n >= 2 and len(set(concepts)) < len(concepts)
    if bad:
        detail = f"Unknown key concept(s): {', '.join(bad)}. Choose from the nine official concepts."
        status = "warn"
    elif dup_concepts:
        detail = "Two commentaries share the same key concept - each needs a different lens."
        status = "warn"
    else:
        detail = f"Key concepts used: {', '.join(sorted(set(concepts))) or 'none recorded'}."
        status = "pass" if len(set(concepts)) == n and n > 0 else "info"
    checks.append({"id": "key_concepts", "label": "Different key concepts", "status": status, "detail": detail})

    dup_sources = len(sources) == n and n >= 2 and len(set(sources)) < len(sources)
    checks.append({"id": "sources", "label": "Different sources",
                   "status": "warn" if dup_sources else ("pass" if len(set(sources)) == n and n > 0 else "info"),
                   "detail": ("Two commentaries share a source - the three articles must come from "
                              "three different sources." if dup_sources
                              else f"Sources: {', '.join(sorted(set(sources))) or 'none recorded'}.")})

    return {"n": n, "checks": checks}
