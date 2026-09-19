"""Mechanical pre-flight checks on a commentary draft. No LLM, fully offline.

Each check returns a dict: {id, label, status, detail}
status: "pass" | "warn" | "info"
"""
from __future__ import annotations

import re

from .rubric import load_rubric

WORD_RE = re.compile(r"[A-Za-z][A-Za-z''-]*")
DIAGRAM_RE = re.compile(r"\b(figure|diagram|graph|fig\.?)\s*\d*", re.IGNORECASE)


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def check_word_limit(draft: str, limit: int) -> dict:
    n = word_count(draft)
    if n > limit:
        status, detail = "warn", f"{n} words - over the {limit}-word limit by {n - limit}. Examiners stop reading at the limit."
    elif n < int(limit * 0.6):
        status, detail = "info", f"{n} words - well under the limit; likely room to develop analysis or evaluation."
    else:
        status, detail = "pass", f"{n} words (limit {limit})."
    return {"id": "word_limit", "label": "Word count", "status": status, "detail": detail, "value": n}


def check_diagram_reference(draft: str) -> dict:
    hits = DIAGRAM_RE.findall(draft)
    if not hits:
        return {"id": "diagram", "label": "Diagram reference (Criterion A)", "status": "warn",
                "detail": "No reference to a figure/diagram found. Criterion A needs a diagram that the text explains explicitly.",
                "value": 0}
    return {"id": "diagram", "label": "Diagram reference (Criterion A)", "status": "pass",
            "detail": f"{len(hits)} diagram reference(s) found. Check each is explained (shift, cause, new outcome).",
            "value": len(hits)}


def check_terminology(draft: str) -> dict:
    lexicon = load_rubric()["terminology_lexicon"]
    body = _norm(draft)
    found = sorted({term for term in lexicon if term in body})
    if len(found) < 4:
        status = "warn"
        detail = f"Only {len(found)} distinct economic terms detected ({', '.join(found) or 'none'}). Criterion B expects terminology used throughout."
    else:
        status = "pass"
        detail = f"{len(found)} distinct terms detected: {', '.join(found[:10])}{'...' if len(found) > 10 else ''}"
    return {"id": "terminology", "label": "Terminology (Criterion B)", "status": status,
            "detail": detail, "value": len(found), "terms": found}


def check_key_concept(draft: str) -> dict:
    concepts = load_rubric()["key_concepts"]
    body = _norm(draft)
    found = [c for c in concepts if c in body]
    if not found:
        return {"id": "key_concept", "label": "Key concept (Criterion C)", "status": "warn",
                "detail": "None of the nine key concepts is named. The commentary must use one key concept as an explicit lens.",
                "value": []}
    return {"id": "key_concept", "label": "Key concept (Criterion C)", "status": "pass",
            "detail": f"Key concept(s) mentioned: {', '.join(found)}. Check it works as a lens, not a name-drop.",
            "value": found}


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    words = WORD_RE.findall(text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def check_application_overlap(draft: str, article: str) -> dict:
    """Does the draft engage the article's specifics? 4-gram overlap + digits shared."""
    if not article.strip():
        return {"id": "application", "label": "Application to article (Criterion C)", "status": "info",
                "detail": "No article text provided - overlap not measured.", "value": None}
    overlap = _ngrams(draft, 4) & _ngrams(article, 4)
    article_numbers = set(re.findall(r"\d[\d,.]*%?", article))
    used_numbers = article_numbers & set(re.findall(r"\d[\d,.]*%?", draft))
    score = len(overlap) + len(used_numbers)
    if score == 0:
        status, detail = "warn", "No shared phrases or figures with the article. The commentary may be running parallel to the article instead of applying theory to it."
    else:
        status = "pass"
        detail = f"{len(overlap)} shared phrase(s), {len(used_numbers)} figure(s) from the article reused ({', '.join(sorted(used_numbers)[:6])})."
    return {"id": "application", "label": "Application to article (Criterion C)", "status": status,
            "detail": detail, "value": score}


def check_evaluation_cues(draft: str) -> dict:
    cues = load_rubric()["evaluation_cues"]
    body = _norm(draft)
    present = {cat: [kw for kw in kws if kw in body] for cat, kws in cues.items()}
    hit_cats = [cat for cat, kws in present.items() if kws]
    if len(hit_cats) < 2:
        status = "warn"
        detail = (f"Evaluation angles detected: {', '.join(hit_cats) or 'none'}. "
                  "Criterion E rewards judgments weighed across angles (stakeholders, time frame, magnitude, assumptions, counterargument).")
    else:
        status = "pass"
        detail = f"Evaluation angles present: {', '.join(hit_cats)}."
    return {"id": "evaluation", "label": "Evaluation cues (Criterion E)", "status": status,
            "detail": detail, "value": hit_cats}


def check_structure(draft: str) -> dict:
    paras = [p for p in re.split(r"\n\s*\n", draft.strip()) if p.strip()]
    if len(paras) < 3:
        return {"id": "structure", "label": "Structure", "status": "info",
                "detail": f"{len(paras)} paragraph(s). Typical commentaries run 4-6 paragraphs (issue, diagram+analysis, evaluation, conclusion).",
                "value": len(paras)}
    return {"id": "structure", "label": "Structure", "status": "pass",
            "detail": f"{len(paras)} paragraphs.", "value": len(paras)}


def run_preflight(draft: str, article: str = "") -> list[dict]:
    from .claims import check_claims  # local import to avoid cycles
    limit = load_rubric()["word_limit"]
    return [
        check_word_limit(draft, limit),
        check_diagram_reference(draft),
        check_terminology(draft),
        check_key_concept(draft),
        check_application_overlap(draft, article),
        check_evaluation_cues(draft),
        check_claims(draft),
        check_structure(draft),
    ]
