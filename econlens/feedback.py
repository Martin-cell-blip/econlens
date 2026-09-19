"""Criterion-referenced feedback: prompt construction + anti-ghostwriting validation.

Contract enforced on every AI response:
  * feedback quotes the student's own sentences (quotes must exist in the draft)
  * no rewritten text: every field is length-capped and screened for ghostwriting markers
  * socratic questions must actually be questions
Invalid responses are rejected (never shown to the student).
"""
from __future__ import annotations

import re

from .llm import chat, extract_json
from .rubric import criterion_map, load_rubric

MAX_FIELD_WORDS = 45
GHOSTWRITE_MARKERS = [
    "here is a revised", "here's a revised", "improved version", "rewritten version",
    "you could write:", "you can write:", "suggested paragraph", "sample answer",
    "for example, you could say", "replace it with:", "try this instead:",
]

SYSTEM_PROMPT = """You are an IB Economics IA coach. You give criterion-referenced feedback on a student's commentary draft.

Hard rules - violating any of them makes your output unusable:
1. NEVER write, rewrite, or complete any sentence of the commentary for the student. No model paragraphs, no sample answers, no "you could write...".
2. Every issue you raise MUST quote a short phrase (3-12 words) copied verbatim from the student's draft.
3. Every issue ends with ONE socratic question that pushes the student to fix it themselves.
4. Keep every field under 40 words.
5. Band estimates are coaching guesses, not official marks.

Respond with ONLY a JSON object in this exact shape:
{
  "criteria": [
    {
      "id": "A",
      "band_estimate": {"low": 0, "high": 3},
      "strengths": ["..."],
      "issues": [
        {"quote": "verbatim phrase from draft", "why": "which descriptor this falls short of", "socratic_question": "...?"}
      ]
    }
  ],
  "next_focus": "the single highest-leverage thing to work on next"
}
Include all five criteria A-E in order. strengths/issues may be empty lists."""


def build_messages(article: str, draft: str) -> list[dict]:
    rubric = load_rubric()
    crit_text = "\n".join(
        f"Criterion {c['id']} - {c['name']} (0-{c['max']}): {c['asks']}\n  Checklist: " + "; ".join(c["coach_checklist"])
        for c in rubric["criteria"]
    )
    user = (
        f"MARKING CRITERIA\n{crit_text}\n\n"
        f"KEY CONCEPTS (one must serve as the lens): {', '.join(rubric['key_concepts'])}\n\n"
        f"ARTICLE\n---\n{article.strip() or '(not provided)'}\n---\n\n"
        f"STUDENT DRAFT\n---\n{draft.strip()}\n---"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _word_len(s: str) -> int:
    return len(re.findall(r"\S+", s))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", s.lower())).strip()


def validate_feedback(payload: dict, draft: str) -> list[str]:
    """Return a list of violations. Empty list == valid."""
    problems: list[str] = []
    crits = criterion_map()
    seen: list[str] = []
    norm_draft = _norm(draft)

    for c in payload.get("criteria", []):
        cid = c.get("id")
        if cid not in crits:
            problems.append(f"unknown criterion id {cid!r}")
            continue
        seen.append(cid)
        band = c.get("band_estimate", {})
        lo, hi = band.get("low"), band.get("high")
        cmax = crits[cid]["max"]
        if not (isinstance(lo, int) and isinstance(hi, int) and 0 <= lo <= hi <= cmax):
            problems.append(f"criterion {cid}: band_estimate must satisfy 0 <= low <= high <= {cmax}")
        for s in c.get("strengths", []):
            if _word_len(s) > MAX_FIELD_WORDS:
                problems.append(f"criterion {cid}: strength over {MAX_FIELD_WORDS} words")
        for issue in c.get("issues", []):
            quote = issue.get("quote", "")
            if not quote or _norm(quote) not in norm_draft:
                problems.append(f"criterion {cid}: quote not found verbatim in draft: {quote[:50]!r}")
            q = issue.get("socratic_question", "")
            if not q.rstrip().endswith("?"):
                problems.append(f"criterion {cid}: socratic_question must end with '?'")
            for field in ("why", "socratic_question"):
                if _word_len(issue.get(field, "")) > MAX_FIELD_WORDS:
                    problems.append(f"criterion {cid}: {field} over {MAX_FIELD_WORDS} words")

    missing = set(crits) - set(seen)
    if missing:
        problems.append(f"missing criteria: {', '.join(sorted(missing))}")

    blob = str(payload).lower()
    for marker in GHOSTWRITE_MARKERS:
        if marker in blob:
            problems.append(f"ghostwriting marker detected: {marker!r}")
    if _word_len(payload.get("next_focus", "")) > MAX_FIELD_WORDS:
        problems.append(f"next_focus over {MAX_FIELD_WORDS} words")
    return problems


def get_feedback(article: str, draft: str, _chat=chat) -> dict:
    """One feedback round. Retries once if the model breaks the contract."""
    messages = build_messages(article, draft)
    last_problems: list[str] = []
    for _ in range(2):
        raw = _chat(messages)
        payload = extract_json(raw)
        problems = validate_feedback(payload, draft)
        if not problems:
            return payload
        last_problems = problems
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Your response violated the contract: "
             + "; ".join(problems[:5])
             + ". Re-emit the full JSON with these fixed. Remember: quote verbatim, never rewrite the student's text."},
        ]
    raise ValueError("model could not produce contract-compliant feedback: " + "; ".join(last_problems[:5]))
