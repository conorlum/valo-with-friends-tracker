"""Stage 0b (docs/replay-map-control-plan.md): the engine feasibility report, one local page.

Feasibility tooling, not product code. Reads what engine_proto.py and encode_control.py wrote under
`<out>/engine/` and writes `<out>/report_0b.html`, images embedded, so it opens from disk.

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\report_0b.py <out_dir> <main stem> <blob.json>
"""

from __future__ import annotations

import base64
import html
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine_proto as E  # noqa: E402

MAPS_DIR = Path(__file__).resolve().parents[2] / "app" / "static" / "img" / "maps"
TEAM = {"A": (57, 135, 229), "B": (217, 89, 38)}  # --team-1 / --team-2
ALPHA = {1: 0.22, 2: 0.40, 3: 0.70}
SNAP_TIMES = (8, 20, 32, 44, 56, 70)
AVG_ROUND_S = 70  # mean t_end over the 67 rounds of the three current-recipe blobs (69.9 s; max 125 s)

# Written after reading the numbers (the run's JSON backs each one).
FINDINGS: list[str] = [
    "<b>16 Hz holds (Q69).</b> The incremental counterfactual costs 0.22–0.24x the base engine with frontier Safe, "
    "0.47–0.74x with boundary Safe, on all three rounds (Ascent r4, Ascent r23 with Killjoy, Summit r19). The full "
    "recompute is 1.0–1.2x and 2.3–4.3x. Worst single player-tick, incremental: 41–300 ms.",
    "<b>Per round:</b> about 5 minutes of one core for the 101 s main round at 16 Hz (boundary Safe), about 3.5 minutes "
    "for an average 70 s round. So ~13 core-hours for ~230 rounds: under 2 hours on 8 cores, and a new replay (~23 rounds) "
    "in about 10 minutes on 8 cores. On Render (not measured; assuming a vCPU is about one core here), the 4-CPU worker would take "
    "~20 minutes per replay and the 1-CPU web service ~80. It runs locally anyway (Q37).",
    "<b>Smokes (Q70):</b> the frontier alone misses up to 2.6% of cells (138 on Ascent r4), over the 1% line; "
    "sight also leaves the free space across wall corners (Q72's tolerance), not only through the team's vision. "
    "Seeing from the whole boundary of the free space stays under 1% on every sampled tick (max 0.85%), for "
    "about 20–60 ms more per tick. Taken literally (rechecking the frontier and leaving the interior unchecked), Q70 "
    "misses up to 16%: interior cells see straight through the smoke.",
    "<b>Incremental = full:</b> 95–98.5% of player-ticks match cell for cell. The rest differ by at most 102 cells, "
    "because the incremental method keeps real sight lines from cells that stopped being on the frontier. The "
    "comparison found one real bug, now fixed: a player standing inside enemy vision can hold their team's free "
    "space together across it, so removing them must refill.",
    "<b>Storage is over budget at full rate:</b> about 610 KB per 100 s with everything at 16 Hz. States at 16 Hz with "
    "the click-to-highlight masks at 2 Hz is about 390 KB per 100 s, about 270 KB for an average round. The active cones "
    "sweeping are most of the bytes; other encodings didn't help.",
    "<b>Browser:</b> the gzip plus a plain JS loop decodes all 1,624 ticks of states in 18 ms warm and the masks in about "
    "130 ms, with bytes matching the engine exactly. The viewer would decode only from the nearest 2 s checkpoint.",
    "<b>Memory:</b> 520 MB peak for the 16 Hz main run, mostly holding every tick's arrays; about 250 MB at 4 Hz.",
    "<b>Vision:</b> building views from the cell bitsets is 5x faster but changes about 11% of the cells a player sees. "
    "The raycast stays.",
    "<b>The rules on real rounds:</b> about 5–6% of cells are contested. Holders under fire add 37–99 contested cells a "
    "tick beyond both-claim, the entry's way back adds 1–3, and cells lost to a status add under 6.",
]
DECISIONS: list[str] = [
    "<b>Q73, where Safe is seen from.</b> Use the whole boundary of the enemy's free space (next to vision, walls or "
    "other pockets) instead of Q70's frontier. <i>Recommended:</i> yes. It's the only method under the 1% line, and "
    "16 Hz still holds with it. <i>Otherwise:</i> the frontier misses up to 2.6% of cells, mostly along walls.",
    "<b>Q74, stored rates.</b> Keep the layer's states at 16 Hz but store each player's highlight masks (control and "
    "coverage) at 2 Hz. The per-player numbers are still computed every tick. <i>Recommended:</i> yes, about "
    "270 KB for an average round. <i>Otherwise:</i> masks at 4 Hz reach ~410 KB per 100 s (over the target for long "
    "rounds), and everything at 16 Hz is ~610 KB.",
    "<b>Still open from 0a:</b> cover tags. This report is walls only, as recommended. Tagging boxes later shrinks "
    "both-claim contest and changes Safe near each box.",
]


def load(p: Path):
    return json.loads(p.read_text()) if p.is_file() else None


def pct(x) -> str:
    return "–" if x is None else f"{x * 100:.1f}%"


def png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def snapshot(name: str, state_flat: np.ndarray, rnd: E.Round, t: float) -> Image.Image:
    rgba = Image.open(MAPS_DIR / f"{name}.png").convert("RGBA")
    base = Image.alpha_composite(Image.new("RGBA", rgba.size, (22, 24, 28, 255)), rgba)
    base = Image.blend(Image.new("RGBA", rgba.size, (22, 24, 28, 255)), base, 0.55)
    over = np.zeros((E.PX, E.PX, 4), np.float32)
    st = state_flat.reshape(E.G, E.G)
    yy, xx = np.mgrid[0:E.PX, 0:E.PX]
    stripe = ((xx + yy) // 6) % 2 == 0
    for code in range(1, 9):
        cells = np.kron(st == code, np.ones((E.CELL, E.CELL), bool))
        if not cells.any():
            continue
        if code in (E.CONTESTED, E.CONTESTED_ACTIVE):
            a = 0.70 if code == E.CONTESTED_ACTIVE else 0.35
            for side, sel in (("A", stripe), ("B", ~stripe)):
                m = cells & sel
                over[m] = (*TEAM[side], a * 255)
        else:
            side, lvl = ("A", code) if code <= 3 else ("B", code - 3)
            over[cells] = (*TEAM[side], ALPHA[lvl] * 255)
    img = Image.alpha_composite(base, Image.fromarray(over.astype(np.uint8), "RGBA"))
    d = ImageDraw.Draw(img)
    for x, y, r, solid in rnd.smokes_at(t):
        d.ellipse([x - r, y - r, x + r, y + r], outline=(235, 235, 235, 255), width=3 if solid else 2)
    for s, side in rnd.side.items():
        if not rnd.alive(s, t):
            continue
        p = rnd.pos(s, t)
        if p is None:
            continue
        x, y, yaw = p
        a = np.deg2rad(yaw)
        d.line([x, y, x + 34 * np.cos(a), y + 34 * np.sin(a)], fill=(255, 255, 255, 255), width=3)
        d.ellipse([x - 11, y - 11, x + 11, y + 11], fill=(*TEAM[side], 255), outline=(255, 255, 255, 255), width=3)
    return img.convert("RGB").resize((640, 640), Image.LANCZOS)


def player_table(z, blob: dict, rnd: E.Round, cell_m2: float) -> str:
    ticks, slots, ctl = z["ticks"], list(z["slots"]), z["control"].astype(np.float64)
    states = z["states"]
    live = (ticks >= blob["t_start"]) & (ticks < (blob["t_decided"] or blob["t_end"]))
    rows = []
    team_sum = {"A": np.zeros(len(ticks)), "B": np.zeros(len(ticks))}
    for i, s in enumerate(slots):
        s = int(s)
        v = ctl[:, i]
        alive = live & ~np.isnan(v[:, 1])
        side = rnd.side[s]
        team_sum[side] += np.nan_to_num(v[:, 1])
        if not alive.any():
            continue
        c, act, psv = v[alive, 1], v[alive, 2], v[alive, 3]
        ratio = act.sum() / (act.sum() + psv.sum()) if (act.sum() + psv.sum()) else None
        died = [b for a, b in rnd.lives.get(s, []) if b != np.inf and blob["t_start"] <= b < (blob["t_decided"] or 1e9)]
        lost = "–"
        if died:
            before = np.flatnonzero((ticks < died[0]) & ~np.isnan(v[:, 1]))
            if len(before):
                j = before[-1]
                lost = f"{v[j, 1] * cell_m2:.0f} m² at {died[0]:.1f} s"
        rows.append(f"<tr><td><span class='dot' style='background:rgb{TEAM[side]}'></span>{s} {html.escape(rnd.agent[s])}</td>"
                    f"<td>{side}</td><td>{alive.sum() / E.HZ:.1f} s</td><td>{c.mean() * cell_m2:.0f}</td>"
                    f"<td>{act.mean() * cell_m2:.0f}</td><td>{psv.mean() * cell_m2:.0f}</td>"
                    f"<td>{'—' if ratio is None else f'{ratio:.0%}'}</td><td>{lost}</td></tr>")
    red = []
    for side, lo, hi in (("A", 1, 3), ("B", 4, 6)):
        ours = ((states >= lo) & (states <= hi)).sum(1).astype(np.float64)
        diff = (ours - team_sum[side])[live]
        red.append(f"{side}: {diff.mean() * cell_m2:.0f} m²")
    return ("<div class='scroll'><table><tr><th>Player</th><th>Side</th><th>Alive (live round)</th>"
            "<th>Control, avg m²</th><th>Active coverage, avg m²</th><th>Passive coverage, avg m²</th>"
            "<th>Active ratio</th><th>Lost control at death</th></tr>" + "".join(rows) + "</table></div>"
            f"<p class='muted'>Team's own cells minus the sum of its players' control, averaged over the live round "
            f"(the \"redundant control\" line): {', '.join(red)}. It can be negative, because control also counts "
            f"denied enemy space (signed scoring, Q62).</p>")


def bars(items: list[tuple[str, float]], unit: str) -> str:
    top = max(v for _, v in items) or 1
    rows = "".join(f"<div class='bar' title='{html.escape(k)}: {v:.1f} {unit}'><span class='lbl'>{html.escape(k)}</span>"
                   f"<span class='track'><span class='fill' style='width:{v / top * 100:.1f}%'></span></span>"
                   f"<span class='val'>{v:.1f}</span></div>" for k, v in items)
    return f"<div class='bars'>{rows}</div>"


def main(out: Path, stem: str, blob_path: Path) -> None:
    eng = out / "engine"
    main_run = load(eng / f"{stem}.json")
    sizes = load(eng / f"{stem}.sizes.json")
    others = [load(p) for p in sorted(eng.glob("*_every*.json")) if p.stem != stem and "sizes" not in p.stem]
    data = json.loads(blob_path.read_text())
    blob = data["rounds"][main_run["round"]]
    geo = E.load_geometry(out, main_run["map"])
    rnd = E.Round(blob, geo)
    z = np.load(eng / f"{stem}.npz")
    cell_m2 = main_run["cell_m"] ** 2

    ms = main_run["ms_per_tick"]
    base_ms = ms["vision"] + ms["base"] + ms["coverage"]
    timing_rows = []
    for r in [main_run] + others:
        m = r["ms_per_tick"]
        b = m["vision"] + m["base"] + m["coverage"]
        live = r["round_seconds"]
        cost = {hz: (b * E.HZ * live + m["cf_incremental"] * hz * live) / 1000 for hz in (16, 2, 1)}
        cost_full = (b * E.HZ * live + m["cf_full"] * E.HZ * live) / 1000
        timing_rows.append(
            f"<tr><td>{r['map']} r{r['round']}</td><td>{r['safe_sources']}</td><td>{r['hz']:g} Hz, {r['ticks']}</td>"
            f"<td>{m['vision']:.0f}</td><td>{m['base'] + m['coverage']:.0f}</td><td>{m['cf_full']:.0f}</td>"
            f"<td>{m['cf_incremental']:.0f}</td><td>{m['cf_full'] / b:.2f}x / <b>{m['cf_incremental'] / b:.2f}x</b></td>"
            f"<td>{r['cf_per_player_ms']['incremental_max']:.0f} / {r['cf_per_player_ms']['full_max']:.0f}</td>"
            f"<td>{cost[16] / 60:.1f} / {cost[2] / 60:.1f} / {cost[1] / 60:.1f} min (full at 16 Hz {cost_full / 60:.1f})</td>"
            f"<td>{r['peak_memory_mb']:.0f} MB</td></tr>")
    worst = "".join(f"<tr><td>{w['t']}</td><td>{w['slot']}</td><td>{w['incremental_ms']}</td><td>{w['full_ms']}</td>"
                    f"<td>{html.escape(w['branch'])}</td></tr>" for w in main_run["cf_worst"])
    br = main_run["cf_branches"]

    smoke_rows, agg = [], {k: [] for k in ("frontier", "boundary", "literal", "static")}
    checked = [r for r in [main_run] + others if r["safe_sources"] == "frontier"]  # one sample set per round
    for r in checked:
        for row in r["smoke_check"]:
            for k in agg:
                if f"{k}_state_diff" in row:
                    agg[k].append(row[f"{k}_state_diff"] / (r["walkable_cells"]))
    for k, name in (("frontier", "Frontier only (Q70: free cells next to the team's vision)"),
                    ("boundary", "Boundary (free cells next to anything not free)"),
                    ("literal", "Frontier rechecked, interior unchecked"),
                    ("static", "No smoke recheck at all")):
        v = np.array(agg[k]) if agg[k] else None
        if v is None:
            continue
        smoke_rows.append(f"<tr><td>{name}</td><td>{len(v)}</td><td>{pct(v.mean())}</td><td>{pct(np.percentile(v, 95))}</td>"
                          f"<td>{pct(v.max())}</td><td>{(v >= 0.01).mean():.0%}</td></tr>")
    smoke_ms = {k: np.mean([row[f'{k}_ms'] for r in checked for row in r['smoke_check']])
                for k in ("frontier", "boundary", "exact", "literal", "static")
                if any(f"{k}_ms" in row for r in checked for row in r["smoke_check"])}

    size_rows = []
    if sizes:
        for cp, row in sizes["by_checkpoint"].items():
            size_rows.append(f"<tr><td>{cp}</td><td>{row['states'] / 1e3:.0f}</td><td>{row['coverage_16hz'] / 1e3:.0f}</td>"
                             f"<td>{row['control_16hz'] / 1e3:.0f}</td><td>{row['control_2hz'] / 1e3:.0f}</td>"
                             f"<td><b>{row['total_cf_16hz'] / 1e3:.0f}</b></td><td>{row['total_cf_2hz'] / 1e3:.0f}</td></tr>")
    rate_rows = []
    for hz, row in ((sizes or {}).get("by_rate_kb_per_100s") or {}).items():
        rate_rows.append(f"<tr><td>{hz} Hz</td><td>{row['states']}</td><td>{row['coverage']}</td><td>{row['control']}</td></tr>")
    rates = (sizes or {}).get("by_rate_kb_per_100s") or {}
    combos = []
    for label, s_hz, m_hz in (("Everything at 16 Hz", "16", "16"), ("States 16 Hz, highlight masks 2 Hz", "16", "2"),
                              ("States 16 Hz, masks 4 Hz", "16", "4"), ("States 8 Hz, masks 2 Hz", "8", "2")):
        if rates:
            total = rates[s_hz]["states"] + rates[m_hz]["coverage"] + rates[m_hz]["control"]
            combos.append(f"<tr><td>{label}</td><td><b>{total}</b></td><td>{total * AVG_ROUND_S / 100:.0f}</td></tr>")
    browser = (sizes or {}).get("browser", {})
    warm = browser.get("run3", {})

    snaps = []
    ticks = z["ticks"]
    for t in SNAP_TIMES:
        if t >= ticks[-1]:
            continue
        i = int(np.argmin(np.abs(ticks - t)))
        full = np.zeros(E.G * E.G, np.uint8)
        full[z["walk"].ravel()] = z["states"][i]
        snaps.append(f"<figure><img src='{png(snapshot(main_run['map'], full, rnd, float(ticks[i])))}' "
                     f"alt='control at {ticks[i]:.1f} s'><figcaption>{ticks[i]:.1f} s</figcaption></figure>")
    share = main_run["state_share"]
    extra = main_run["contest_extra_cells_per_tick"]

    missing = [
        ("Flash hit time and per-target blind time", "The blob's flash row carries the <b>cast</b> time and the "
         "targets; the hit time and <code>initial_duration_seconds</code> are dropped. 0b used the pop time of the "
         "thrown object (or cast + 0.5 s) and the ability's full blind."),
        ("Nearsight hit time and duration", "Same: cast time only. 0b used 2 s for Paranoia, 1 s otherwise."),
        ("Camera and drone in-use intervals (<code>PlayerTable.possession</code>)", "Cypher's camera exists all round "
         "in the blob, so 0b doesn't model it. Drones are only alive while flown, so 0b used their lifetime."),
        ("Camera and turret yaw over time; drone yaw", "Pawn paths are x/y at 10 Hz. 0b took a drone's heading from "
         "its path direction."),
        ("Wallbang hits (<code>IsWallPenetration</code>)", "Not in the blob; the 2 s wallbang contest isn't applied."),
        ("General damage and molly hit times", "0b contests a holder standing in an enemy damage area's radius instead."),
        ("Viper's wall points and Harbor", "Not in this round; the viewer's `points` exist for Viper and need no "
         "condenser change. Harbor still unparsed."),
        ("Status durations for one-shots", "Concusses from trips arrive as 1 s pings (STATUS_PING_MS); Riot's values "
         "aren't applied yet."),
        ("Player height (z)", "In the exports, not the blob. Would help the elevation cases (0a's three over-edge shots)."),
    ]
    run_missing = main_run.get("missing_inputs", {})
    missing_html = "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in missing)

    timing_chart = bars([("Vision (raycast, 10 views)", ms["vision"]), ("Base: fills, Safe, contests", ms["base"]),
                         ("Coverage", ms["coverage"]), ("Counterfactual, full", ms["cf_full"]),
                         ("Counterfactual, incremental", ms["cf_incremental"])], "ms/tick")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Map Control 0b</title>
<style>
:root {{ --bg:#f7f7f5; --panel:#fff; --text:#1d1f23; --muted:#687080; --line:#dcdde0; --good:#1f8a50; --critical:#c9373c;
  --bar:#3a6fb0; --track:#eceef1; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#15171b; --panel:#1f2228; --text:#e6e8eb; --muted:#9aa1ab;
  --line:#333842; --good:#3dbb74; --critical:#ef5a5f; --bar:#6d9be0; --track:#2a2e36; }} }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.55 system-ui,sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:18px; margin:32px 0 8px; }}
.muted {{ color:var(--muted); }}
table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; background:var(--panel); }}
th,td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ font-size:13px; color:var(--muted); font-weight:600; }}
.scroll {{ overflow-x:auto; }} code {{ overflow-wrap:anywhere; }}
.callout {{ background:var(--panel); border-left:4px solid var(--good); padding:10px 14px; border-radius:4px; margin:12px 0; }}
.callout.ask {{ border-left-color:var(--bar); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:12px; }}
figure {{ margin:0; }} img {{ width:100%; height:auto; border-radius:6px; }}
figcaption {{ font-size:13px; color:var(--muted); }}
.dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }}
.bars {{ background:var(--panel); padding:10px 12px; border-radius:6px; }}
.bar {{ display:grid; grid-template-columns:minmax(120px,240px) 1fr 56px; gap:10px; align-items:center; margin:6px 0; }}
.track {{ background:var(--track); height:14px; border-radius:4px; overflow:hidden; display:block; }}
.fill {{ background:var(--bar); height:100%; display:block; border-radius:0 4px 4px 0; }}
.val {{ text-align:right; font-variant-numeric:tabular-nums; }} .lbl {{ font-size:14px; }}
.key span {{ display:inline-block; margin-right:14px; font-size:13px; }}
</style></head><body><main>
<h1>Map control, Stage 0b: engine</h1>
<p class="muted">Feasibility report for docs/replay-map-control-plan.md. No product code. Main round: {main_run['map']}
r{main_run['round']} ({main_run['uuid'][:8]}), {main_run['round_seconds']} s, {main_run['ticks']} ticks at
{main_run['hz']:g} Hz, walls only (no cover tags), one CPU core. Built by webapp/scripts/control_feasibility/
(engine_proto.py, encode_control.py, report_0b.py).</p>

<div class="callout"><b>Findings</b><ul>{''.join(f'<li>{f}</li>' for f in FINDINGS)}</ul></div>
<div class="callout ask"><b>For you to decide</b><ol>{''.join(f'<li>{d}</li>' for d in DECISIONS)}</ol></div>

<h2>1. Compute (Q69)</h2>
<p>Per tick on the main round, one core. "Incremental" reuses the base fills: an enemy fill is kept when the cells
only the removed player watched don't touch it, else extended from it; the player's own fill drops their component
only if no teammate shares it.</p>
{timing_chart}
<div class="scroll"><table><tr><th>Round</th><th>Safe from</th><th>Rate, ticks</th><th>Vision ms</th><th>Base ms</th>
<th>Counterfactual full ms</th><th>Incremental ms</th><th>vs base (full / incr.)</th><th>Worst player ms (incr. / full)</th>
<th>Per round, cf at 16 / 2 / 1 Hz</th><th>Peak memory</th></tr>{''.join(timing_rows)}</table></div>
<p class="muted">Per-round cost = vision + base + coverage at 16 Hz, plus the incremental counterfactual at the given
rate, over the whole round (t_end). Incremental branches on the main round: {html.escape(json.dumps(br))}.
The incremental counterfactual matched the full one exactly on {main_run['cf_incremental_vs_full']['identical']} of
{main_run['cf_incremental_vs_full']['player_ticks']} player-ticks (mean {main_run['cf_incremental_vs_full']['mean_cells_differing']}
cells differing, max {main_run['cf_incremental_vs_full']['max_cells_differing']}; control differs by
{main_run['cf_incremental_vs_full']['control_abs_diff_mean']} cells on average). Where they differ, the incremental one keeps
sight from old frontier cells that are now interior: real sight lines the frontier method skips.</p>
<p><b>Worst player-ticks</b> (incremental):</p>
<div class="scroll"><table><tr><th>t</th><th>Slot</th><th>Incremental ms</th><th>Full ms</th><th>Branch</th></tr>{worst}</table></div>

<h2>2. Smokes and which free cells Safe is seen from (Q70)</h2>
<p>Each method against the exact recheck (every free cell, every smoke-crossing pair), on sampled smoked ticks.
Share of walkable cells whose state differs. The 1% line is Q70's.</p>
<div class="scroll"><table><tr><th>Method</th><th>Ticks</th><th>Mean</th><th>p95</th><th>Max</th><th>Ticks at 1% or more</th></tr>
{''.join(smoke_rows)}</table></div>
<p class="muted">Average ms for one base compose: {', '.join(f'{k} {v:.0f}' for k, v in smoke_ms.items())}.</p>

<h2>3. Storage and delivery</h2>
<p>Gzipped (level 9) as the endpoint would serve it, for the {sizes['seconds'] if sizes else '–'} s main round,
{sizes['cells'] if sizes else '–'} walkable cells, 10 players. KB by checkpoint interval:</p>
<div class="scroll"><table><tr><th>Checkpoint</th><th>States</th><th>Coverage masks</th><th>Control masks 16 Hz</th>
<th>Control masks 2 Hz</th><th>Total, cf 16 Hz</th><th>Total, cf 2 Hz</th></tr>{''.join(size_rows)}</table></div>
<p>Each stream at a lower rate (2 s checkpoints), KB per 100 s of round:</p>
<div class="scroll"><table><tr><th>Rate</th><th>States</th><th>Coverage masks</th><th>Control masks</th></tr>
{''.join(rate_rows)}</table></div>
<p>Combinations against the 150–400 KB target (Q36). The average round in the three current-recipe replays runs about
{AVG_ROUND_S} s, blob start to end (longest 125 s):</p>
<div class="scroll"><table><tr><th>Choice</th><th>KB per 100 s</th><th>KB for an average round</th></tr>
{''.join(combos)}</table></div>
<p class="muted">Other encodings tried and dropped: XOR frames (4-bit or byte) gzip to 5–10% more than the change list;
xz saves 3% but browsers can't decompress it natively. Most of the bytes are the active cones sweeping.</p>
<p>Browser (headless Chromium, this machine): decoding every tick of the round, states + coverage + control:
<b>{warm.get('states_ms', '–')} + {warm.get('coverage_ms', '–')} + {warm.get('control_ms', '–')} ms</b> warm,
{browser.get('run1', {}).get('states_ms', '–')} ms for the states on a cold first call. JS heap after three full decodes:
{browser.get('js_heap_mb', '–')} MB. Decoded bytes match the engine's exactly: {browser.get('sha_matches', '–')}.</p>

<h2>4. Vision: raycast or cell bitsets</h2>
<p>The same views built from the static bitsets (the player's cell's 360-degree row, cut to the 103-degree sector and the
smokes' shadows), against the raycast from the exact position: {pct(main_run['vision_bitset_vs_ray']['mean_share_differing'])}
of the union differs on average (p95 {pct(main_run['vision_bitset_vs_ray']['p95_share_differing'])}), for
{main_run['ms_per_tick']['vision_other'] * 4:.0f} ms per tick of 10 views instead of {ms['vision']:.0f} ms.
The difference is where the player's exact position and their cell's centre see past different corners.</p>

<h2>5. The rules on one round</h2>
<p class="key"><span><span class="dot" style="background:rgb{TEAM['A']}"></span>Side A (team 1 colour)</span>
<span><span class="dot" style="background:rgb{TEAM['B']}"></span>Side B</span>
<span>Strong: active · medium: safe · light: passive · stripes: contested (strong when a cone is on it)</span>
<span>White rings: smokes (thick: solid)</span></p>
<div class="grid">{''.join(snaps)}</div>
<p>Share of cell-ticks over the round: A {pct(share['A'])}, B {pct(share['B'])}, contested at active intensity
{pct(share['contested_active'])}, contested light {pct(share['contested'])}, nobody {pct(share['none'])}. Cells the extra
contest rules add each tick on top of both-claim: holders under fire {extra.get('holder', 0)}, the entry's way back
{extra.get('way_back', 0)}, lost to a status {extra.get('loss', 0)}.</p>
<h3>Per-player (live round only, Q66)</h3>
{player_table(z, blob, rnd, cell_m2)}

<h2>6. Inputs the condenser doesn't extract yet</h2>
<div class="scroll"><table><tr><th>Input</th><th>Why, and what 0b did instead</th></tr>{missing_html}</table></div>
<p class="muted">Counted on the main round: {html.escape(json.dumps(run_missing))}. Killjoy's and Chamber's pieces leave no
status rows (0a), so their trips can't be checked against statuses.</p>
</main></body></html>"""
    (out / "report_0b.html").write_text(page, encoding="utf-8")
    print(f"wrote {out / 'report_0b.html'} ({len(page) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
