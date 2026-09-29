"""Stage 0a (docs/replay-map-control-plan.md): the geometry feasibility report, one local page.

Feasibility tooling, not product code. Reads what the other Stage 0a scripts wrote under <out>
and writes `<out>/report.html` with every image embedded, so it opens from disk.

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\report.py <out_dir>
"""

from __future__ import annotations

import base64
import html
import json
import sys
from pathlib import Path

BAR = 0.02


def img(path: Path, alt: str) -> str:
    if not path.is_file():
        return f'<p class="muted">({html.escape(alt)}: not built)</p>'
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<img src="data:image/png;base64,{data}" alt="{html.escape(alt)}" loading="lazy">'


def pct(x: float | None) -> str:
    return "–" if x is None else f"{x * 100:.1f}%"


def load(path: Path):
    return json.loads(path.read_text()) if path.is_file() else None


def main(out: Path) -> None:
    geo = out / "geometry"
    index = load(geo / "index.json") or {}
    risk = (load(out / "risk1.json") or {}).get("maps", {})
    util = load(out / "utility_after_death.json") or {}
    status = load(out / "status_after_death.json")

    rows = []
    for name in sorted(index):
        g, r = index[name], risk.get(name)
        if r and r["qualifying"]:
            occupied = sum(1 for c in r["candidates"].values() if c["occupancy"] > 0)
            verdict = ('<span class="ok">passes</span>' if r["passes_alpha"] else '<span class="bad">over the bar</span>')
            rows.append(f"<tr><td>{name}</td><td>{r['replays']}</td><td>{r['qualifying']}</td>"
                        f"<td>{r['blocked_alpha']} ({pct(r['blocked_alpha_share'])})</td><td>{verdict}</td>"
                        f"<td>{pct(r['blocked_if_every_candidate_is_cover_share'])}</td>"
                        f"<td>{g['closed']} + {g['lines']}</td><td>{occupied}</td></tr>")
        else:
            rows.append(f"<tr><td>{name}</td><td>0</td><td>–</td><td>–</td><td class='muted'>no replay: waits</td>"
                        f"<td>–</td><td>{g['closed']} + {g['lines']}</td><td>–</td></tr>")

    dz_rows = []
    for name, r in sorted(risk.items()):
        for dz in r.get("blocked_alpha_dz", []):
            dz_rows.append(f"<tr><td>{name}</td><td>{dz:+d} cm</td></tr>")

    util_rows = []
    for o in util.get("objects", []):
        placed = any(s in o["object"] for s in ("TripWire", "Camera", "Trap", "Turret", "Alarm", "Sensor", "SeizeTrap"))
        if not placed:
            continue
        util_rows.append(f"<tr><td>{html.escape(o['object'])}</td><td>{o['died_with_owner']}</td>"
                         f"<td>{o['outlived_owner']}</td><td>{', '.join(map(str, o['outlived_seconds'][:8]))}</td></tr>")
    status_rows = []
    if status:
        for o in status:
            if o["after_owner_died"]:
                ex = "; ".join(f"r{e['round']} +{e['s_after_death']}s" for e in o["examples"][:4])
                status_rows.append(f"<tr><td>{html.escape(o['object'])}</td><td>{o['while_owner_alive']}</td>"
                                   f"<td>{o['after_owner_died']}</td><td>{ex}</td></tr>")
    sides = util.get("sides", {})

    per_map = []
    for name in sorted(index):
        per_map.append(f"<details><summary>{name}</summary><div class='pair'>"
                       f"<figure>{img(geo / f'{name}.render.png', name + ' candidates')}<figcaption>Candidates: "
                       f"blue closed shapes, amber drawn lines, purple colour glyphs (void).</figcaption></figure>"
                       f"<figure>{img(out / 'risk1' / f'{name}.png', name + ' kill lines')}<figcaption>Kill lines on "
                       f"walls only: red blocked, green clear.</figcaption></figure></div></details>")

    status_block = ("<p class='muted'>Status check still running or not run.</p>" if status is None else
                    ("<p>No status or reveal was applied by any object more than 1.5 s after its owner died.</p>"
                     if not status_rows else
                     "<table><tr><th>Object → status</th><th>While owner alive</th><th>After owner died</th>"
                     "<th>Examples</th></tr>" + "".join(status_rows) + "</table>"))

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Map Control 0a</title>
<style>
:root {{ --bg:#f7f7f5; --panel:#fff; --text:#1d1f23; --muted:#687080; --line:#dcdde0; --good:#1f8a50; --critical:#c9373c; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#15171b; --panel:#1f2228; --text:#e6e8eb; --muted:#9aa1ab;
  --line:#333842; --good:#3dbb74; --critical:#ef5a5f; }} }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.55 system-ui,sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:18px; margin:32px 0 8px; }}
.muted {{ color:var(--muted); }} .ok {{ color:var(--good); font-weight:600; }} .bad {{ color:var(--critical); font-weight:600; }}
table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; background:var(--panel); }}
th,td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ font-size:13px; color:var(--muted); font-weight:600; }}
.scroll {{ overflow-x:auto; }}
details {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; margin:8px 0; padding:8px 12px; }}
summary {{ cursor:pointer; font-weight:600; }}
.pair {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:12px; margin-top:8px; }}
figure {{ margin:0; }} img {{ width:100%; height:auto; border-radius:6px; background:#000; }}
figcaption {{ font-size:13px; color:var(--muted); }}
.callout {{ background:var(--panel); border-left:4px solid var(--good); padding:10px 14px; border-radius:4px; }}
</style></head><body><main>
<h1>Map control, Stage 0a: geometry</h1>
<p class="muted">Feasibility report for docs/replay-map-control-plan.md. No product code. Built from the local exports and
minimaps by webapp/scripts/control_feasibility/.</p>

<h2>1. Kill-line test on walls only (Risk 1)</h2>
<p>Qualifying kills are lethal bullet hits (<code>MulticastNotifyDamage_Point</code>) with the replay's own
<code>IsWallPenetration</code> false, both positions within 100 ms of the kill. Bar: 2% or less blocked per map.
"Every candidate as cover" is the worst case if every detected shape were tagged cover.</p>
<div class="scroll"><table><tr><th>Map</th><th>Replays</th><th>Qualifying kills</th><th>Blocked by walls</th>
<th>Walls-only verdict</th><th>Every candidate as cover</th><th>Candidates (closed + line)</th><th>Candidates people stood in</th></tr>
{''.join(rows)}</table></div>
<p class="muted">Height difference (killer z − victim z) for each wall-blocked line, since the replay export does carry z:</p>
<div class="scroll"><table><tr><th>Map</th><th>dz</th></tr>{''.join(dz_rows) or '<tr><td colspan=2>none</td></tr>'}</table></div>

<h2>2. Tagging</h2>
<p>Open <code>tagger.html</code> in this folder. Click a shape, press 1 cover, 2 see-over, 3 walkable, 4 glyph. The
blocked share updates live, so a tag that pushes a map over 2% shows at once. Export writes
<code>control-tags.json</code> for Stage 1.</p>

<h2>3. Utility after its owner dies (Q71)</h2>
<p><b>Object lifetimes.</b> How long placed objects stay in the replay after their owner's death (six condensed
bundles, older recipe). Staying in the replay does not mean still working.</p>
<div class="scroll"><table><tr><th>Object</th><th>Ended with owner</th><th>Stayed after</th><th>Seconds after (first 8)</th></tr>
{''.join(util_rows)}</table></div>
<p><b>Still working?</b> Statuses and reveals an object applied more than 1.5 s after its owner died (current
condenser):</p>
{status_block}

<h2>4. Sides</h2>
<p>{sides.get('null', '–')} null sides in {sides.get('players', '–')} player-rounds over {sides.get('rounds', '–')} rounds
(six bundles).</p>

<h2>5. Per map</h2>
{''.join(per_map)}
</main></body></html>"""
    (out / "report.html").write_text(page, encoding="utf-8")
    print(f"wrote {out / 'report.html'} ({len(page) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
