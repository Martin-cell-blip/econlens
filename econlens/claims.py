"""Claim / evidence mapping - deterministic, no LLM.

Splits the draft into sentences, flags causal and evaluative claims, and marks
claims that have no evidence signal in the same or an adjacent sentence.
"This point may need evidence" is a coaching flag, not a verdict.
"""
from __future__ import annotations

import re

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")

CAUSAL_MARKERS = [
    "leads to", "lead to", "causes", "caused by", "results in", "resulting in",
    "will increase", "will decrease", "will reduce", "will raise", "will fall",
    "due to", "because of", "therefore", "as a result", "consequently",
    "drives", "pushes up", "pushes down", "brings about",
]
EVALUATIVE_MARKERS = [
    "should", "must", "ought to", "is justified", "is not justified", "is effective",
    "is ineffective", "the best", "the worst", "outweigh", "outweighs", "on balance",
    "is likely to be", "is desirable", "is undesirable", "benefits exceed", "is preferable",
]
EVIDENCE_SIGNALS = [
    re.compile(r"\d"),                                   # any figure
    re.compile(r"according to", re.I),
    re.compile(r"the article (states|notes|reports|says)", re.I),
    re.compile(r"\""),                                    # quotation
    re.compile(r"figure|diagram|graph", re.I),            # anchored in the diagram
    re.compile(r"(data|evidence|study|survey|statistics)", re.I),
]


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    return [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]


def _has_evidence(sentence: str) -> bool:
    return any(p.search(sentence) for p in EVIDENCE_SIGNALS)


def _claim_type(sentence: str) -> str | None:
    low = sentence.lower()
    causal = any(m in low for m in CAUSAL_MARKERS)
    evaluative = any(m in low for m in EVALUATIVE_MARKERS)
    if causal and evaluative:
        return "causal+evaluative"
    if causal:
        return "causal"
    if evaluative:
        return "evaluative"
    return None


def map_claims(draft: str) -> list[dict]:
    """Per-sentence claim map. Evidence counts if it sits in the claim sentence
    or in the sentence immediately before/after (typical citation placement)."""
    sentences = split_sentences(draft)
    out = []
    for i, s in enumerate(sentences):
        ctype = _claim_type(s)
        if not ctype:
            continue
        nearby = [s]
        if i > 0:
            nearby.append(sentences[i - 1])
        if i + 1 < len(sentences):
            nearby.append(sentences[i + 1])
        supported = any(_has_evidence(x) for x in nearby)
        out.append({
            "sentence": s if len(s) <= 220 else s[:217] + "...",
            "index": i,
            "type": ctype,
            "supported": supported,
        })
    return out


def check_claims(draft: str) -> dict:
    """Preflight-shaped summary of the claim map."""
    claims = map_claims(draft)
    unsupported = [c for c in claims if not c["supported"]]
    if not claims:
        status = "info"
        detail = ("No causal or evaluative claims detected. A commentary needs argued claims - "
                  "check the analysis and evaluation actually take positions.")
    elif unsupported:
        status = "warn"
        detail = (f"{len(claims)} claim(s) found; {len(unsupported)} may need evidence nearby "
                  "(a figure, a quote, or an article reference in the same or adjacent sentence).")
    else:
        status = "pass"
        detail = f"{len(claims)} claim(s) found, all with an evidence signal nearby."
    return {"id": "claims", "label": "Claim / evidence map", "status": status,
            "detail": detail, "value": len(unsupported), "claims": claims}
