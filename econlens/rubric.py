"""Load and validate the marking rubric."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

RUBRIC_PATH = Path(__file__).resolve().parent.parent / "rubric" / "ib_econ_ia.json"


@lru_cache(maxsize=1)
def load_rubric() -> dict:
    with open(RUBRIC_PATH, encoding="utf-8") as f:
        rubric = json.load(f)
    _validate(rubric)
    return rubric


def _validate(rubric: dict) -> None:
    criteria = rubric["criteria"]
    ids = [c["id"] for c in criteria]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate criterion ids in rubric")
    per_commentary = sum(c["max"] for c in criteria)
    expected_total = (
        per_commentary * rubric["portfolio"]["commentaries"]
        + rubric["portfolio"]["rubric_marks"]
    )
    if expected_total != rubric["portfolio"]["total_marks"]:
        raise ValueError(
            f"rubric marks inconsistent: {per_commentary}x"
            f"{rubric['portfolio']['commentaries']}+{rubric['portfolio']['rubric_marks']}"
            f" != {rubric['portfolio']['total_marks']}"
        )


def criterion_map(rubric: dict | None = None) -> dict[str, dict]:
    rubric = rubric or load_rubric()
    return {c["id"]: c for c in rubric["criteria"]}
