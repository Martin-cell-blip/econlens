"""Process archive report: an HTML record of the revision history.

What it proves: the revision process happened in-tool, round by round, with
hashes and similarity deltas. What it does NOT prove: that every word was
written by the student. The report says so explicitly.
"""
from __future__ import annotations

import html


def _esc(s) -> str:
    return html.escape(str(s))


def _band_str(bands: dict | None) -> str:
    if not bands:
        return "—"
    return ", ".join(f"{cid} {b['low']}–{b['high']}" for cid, b in sorted(bands.items()))


def render_report(session: dict) -> str:
    rows = []
    for r in session["rounds"]:
        sim = r.get("similarity_to_previous")
        sim_str = "first draft" if sim is None else f"{sim * 100:.1f}% similar to prev."
        warns = sum(1 for c in r["preflight"] if c["status"] == "warn")
        rows.append(
            "<tr>"
            f"<td>{r['n']}</td><td>{_esc(r['at'])}</td><td>{r['word_count']}</td>"
            f"<td>{_esc(sim_str)}</td><td>{warns}</td><td>{_esc(_band_str(r.get('bands')))}</td>"
            f"<td class='mono'>{_esc(r['draft_sha256'][:16])}…</td>"
            "</tr>"
        )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>EconLens process archive — {_esc(session['title'])}</title>
<style>
body{{font-family:Georgia,serif;max-width:860px;margin:40px auto;padding:0 20px;color:#1c2422;}}
h1{{font-size:1.5rem}} .sub{{color:#5a6660}}
table{{border-collapse:collapse;width:100%;font-size:.9rem;margin:20px 0}}
th,td{{border:1px solid #cdd5cf;padding:6px 10px;text-align:left}}
th{{background:#e4efe8}}
.mono{{font-family:Consolas,monospace;font-size:.8rem}}
.note{{background:#f6edda;border-left:4px solid #96660f;padding:10px 14px;font-size:.88rem}}
</style></head><body>
<h1>EconLens process archive</h1>
<p class="sub">Commentary: <strong>{_esc(session['title'])}</strong> · session {_esc(session['id'])} · started {_esc(session['created_at'])} · {len(session['rounds'])} round(s)</p>
<table>
<tr><th>Round</th><th>Time (UTC)</th><th>Words</th><th>Revision delta</th><th>Preflight warnings</th><th>Band estimates (coach)</th><th>Draft SHA-256</th></tr>
{''.join(rows)}
</table>
<div class="note"><strong>Scope of this record.</strong> It documents that the drafts above were revised
round by round inside EconLens, with content hashes and similarity deltas. It does not, by itself,
prove authorship of the text. AI feedback in EconLens is criterion-referenced and quote-based; the tool
never generates commentary text. Band estimates are coaching references, not official marks.</div>
</body></html>"""
