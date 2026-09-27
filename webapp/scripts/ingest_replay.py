"""Condenses a parser export, and previews it or dry-runs its link. Stage 1c: no writes.

    .\\.venv313\\Scripts\\python.exe scripts\\ingest_replay.py --export-dir %TEMP%\\valo-replay\\<uuid> --preview
    .\\.venv313\\Scripts\\python.exe scripts\\ingest_replay.py --export-dir <dir> --preview --map Ascent
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\ingest_replay.py --export-dir <dir> --dry-run

The parser is never called from here: `scripts/export_replay.ps1` (run by the user) writes
the export, and this reads it.

- `--preview` condenses (showing, not refusing, blocking diagnostics), writes a
  self-contained page to `%TEMP%\\valo-replay\\<match uuid>\\preview.html`, and runs the
  scripted checks (tracks for every player present in each round, coverage and gaps, spawn
  clusters, map discovery, size budget, diagnostics, blob round trip, and the pass-6 checks:
  the phase cycle, kills outside rounds, ownership, lifecycle, link eligibility). Exit 1 if
  any fail. The phase-cycle, ownership and lifecycle rules refuse at condense (exit 3).
  The map comes from the export's actor paths, else from the `.vrf` itself, else `--map`.
  The page is local only; open it in a browser to look at the tracks once.
- `--dry-run` condenses strictly (the `.vrf`'s hash and the parser's BUILD.json must
  match), checks the replay's link eligibility (and refuses before any DB read when it
  fails), loads the matching `matches` rows read-only and runs the linker, and prints its
  report. It never writes; run it through `with_friends_db.py --read-only`.
- Without either flag it stops: storing replays arrives in Stage 2.

Options default from the environment: `VALO_REPLAY_ARCHIVE` (the archive holding the
`.vrf`) and `REPLAY_PARSER_DIR` (the parser build holding `bin/BUILD.json`).
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import sys
import tempfile
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.replays import format as fmt  # noqa: E402
from app.replays import link as lk  # noqa: E402
from app.replays.condense import (  # noqa: E402
    MAX_TRACK_GAP_S,
    MIN_ALIVE_COVERAGE,
    CondensedReplay,
    condense_export_dir,
)
from app.replays.contract import ContractError, load_manifest, sha256_file  # noqa: E402


def default_archive() -> Path:
    configured = os.environ.get("VALO_REPLAY_ARCHIVE")
    return Path(configured) if configured else Path.home() / "ValorantReplayArchive"


def default_parser_dir() -> Path:
    configured = os.environ.get("REPLAY_PARSER_DIR")
    return Path(configured) if configured else Path.home() / "rp" / "parser"


def default_preview_root() -> Path:
    return Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-replay"


# ---------------------------------------------------------------- checks


def preview_checks(replay: CondensedReplay) -> list[tuple[str, bool, str]]:
    report = replay.report
    checks = []
    known = report["map"]["known"]
    checks.append(("map discovered from the export or the .vrf", len(known) == 1,
                   f"{report['map']['source']}: known codes {known}"))
    # A player who left (no pawn left in the round) has no alive interval and needs no track.
    missing = {n: sorted((s for s, iv in blob["alive"].items() if iv and s not in blob["tracks"]), key=int)
               for n, blob in replay.rounds.items()}
    missing = {n: slots for n, slots in missing.items() if slots}
    left = report["players"]["left"]
    checks.append(("every present player has a track in every round", not missing,
                   f"missing {missing}" if missing else (f"left mid-match: slots {sorted(left, key=int)}" if left else "")))
    low = {s: c["min"] for s, c in report["coverage"].items() if c["min"] < MIN_ALIVE_COVERAGE}
    checks.append((f"alive-time coverage >= {MIN_ALIVE_COVERAGE:.0%} per player", not low, f"low {low}" if low else ""))
    gaps = {s: c["max_gap_s"] for s, c in report["coverage"].items() if c["max_gap_s"] > MAX_TRACK_GAP_S}
    checks.append((f"no track gap > {MAX_TRACK_GAP_S:g} s", not gaps, f"gaps {gaps}" if gaps else ""))
    # Round-start positions proved loose in the first real export (one side roamed through the
    # buy phase), so the gate is the match-start spawn lines; per-round clusters are reported.
    spawn = report["match_spawn_check"]
    loose = sorted((n for n, c in report["spawn_checks"].items() if not c["ok"]), key=int)
    checks.append(("two 5/5 spawn lines at match start", spawn["ok"],
                   f"sizes {spawn['sizes']}, radius {spawn['max_radius']}"
                   + (f"; loose round-start clusters in rounds {loose}" if loose else "")))
    sizes = report["sizes"]
    checks.append(("size budget (p95 per round, total per match)", sizes["fits_budget"],
                   f"p95 {sizes['p95_bytes']} B, total {sizes['total_bytes']} B"))
    blocking = report["diagnostics"]["blocking"]
    checks.append(("no blocking diagnostics", not blocking, "; ".join(blocking)))
    round_trip = all(fmt.decode_blob(data) == replay.rounds[n] for n, data in replay.encoded_rounds().items())
    checks.append(("every blob decodes back to itself", round_trip, ""))
    # Pass 6. The phase cycle, ownership and lifecycle refuse at condense, so reaching here
    # means they held; the lines show what was checked.
    numbers = report["round_number_at_start"]
    checks.append(("phase cycle validated, rounds numbered in order (P-a)", True,
                   f"ClientGamePhaseEnded cross-check {'ran' if report['phase_ended_checked'] else 'NOT run'}; "
                   f"decoded RoundNumber {'none' if all(n is None for n in numbers) else numbers}"))
    checks.append(("kills_outside_rounds == 0", report["kills_outside_rounds"] == 0,
                   f"{report['kills']} kills; excluded {report['kills_excluded']}"))
    ownership = report["ownership"]
    checks.append(("pawn ownership validated (P-b)", True,
                   f"{ownership['claims']} claims on {ownership['pawns']} pawns, "
                   f"{ownership['possession_intervals']} possession intervals"))
    life = report["lifecycle"]
    checks.append(("lifecycle: no contradictions (P-c)", life["contradictions"] == 0,
                   f"closes {life['closes_by_reason']}; left {life['left']}; unobserved spans "
                   f"{life['unobserved_spans']}; uncertain lives {life['uncertain_lives']}"))
    eligibility = replay.link_inputs["eligibility"]
    checks.append(("link eligibility (P-f)", eligibility["eligible"], "; ".join(eligibility["reasons"])))
    return checks


# ---------------------------------------------------------------- preview page


def preview_html(replay: CondensedReplay, checks: list[tuple[str, bool, str]]) -> str:
    image = (fmt.STATIC_DIR / "img" / "maps" / f"{replay.map_name}.png").read_bytes()
    data = {
        "map": replay.map_name,
        "image": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        "rounds": {str(n): blob for n, blob in replay.rounds.items()},
    }
    rows = "".join(
        f"<tr><td>{'PASS' if ok else 'FAIL'}</td><td>{html.escape(name)}</td><td>{html.escape(detail)}</td></tr>"
        for name, ok, detail in checks)
    report = html.escape(json.dumps({k: v for k, v in replay.report.items() if k != "coverage"}, indent=1))
    # The page shows slots and agents only: no names, Subjects or match identifiers.
    return PAGE.replace("__ROWS__", rows).replace("__REPORT__", report).replace(
        "__DATA__", json.dumps(data).replace("</", "<\\/"))


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Replay preview</title>
<style>
:root { --bg:#f6f6f4; --fg:#1d1d1f; --muted:#666; --a:#0f766e; --b:#b42318; --line:#ddd; }
@media (prefers-color-scheme: dark) { :root { --bg:#161618; --fg:#ededed; --muted:#9a9a9a; --a:#2dd4bf; --b:#f87171; --line:#333; } }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg); font:14px/1.4 system-ui, sans-serif; }
main { max-width:900px; margin:0 auto; }
canvas { width:100%; max-width:720px; aspect-ratio:1; display:block; background:#000; border-radius:6px; }
.controls { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:10px 0; }
button { font:inherit; padding:4px 10px; }
input[type=range] { flex:1; min-width:160px; }
table { border-collapse:collapse; width:100%; margin:12px 0; }
td { border-top:1px solid var(--line); padding:4px 6px; vertical-align:top; }
pre { overflow:auto; font-size:12px; color:var(--muted); }
.legend span { margin-right:12px; } .a { color:var(--a); } .b { color:var(--b); }
</style></head><body><main>
<h1>Replay preview</h1>
<p class="legend">Local scratch page. <span class="a">&#9679; side A</span><span class="b">&#9679; side B</span>
Hollow ring: last known position (2 s). &#215;: a kill, at the victim.</p>
<div class="controls"><label>Round <select id="round"></select></label>
<button id="play">Play</button><select id="speed"><option>1</option><option>2</option><option selected>4</option><option>8</option></select>&#215;
<input id="scrub" type="range" min="0" max="1000" value="0"><span id="clock">0.0 s</span></div>
<canvas id="map" width="1024" height="1024"></canvas>
<h2>Scripted checks</h2><table>__ROWS__</table>
<h2>Report</h2><pre>__REPORT__</pre>
</main>
<script>
const DATA = __DATA__;
const img = new Image(); img.src = DATA.image;
const cv = document.getElementById('map'), cx = cv.getContext('2d');
const sel = document.getElementById('round'), scrub = document.getElementById('scrub'), clock = document.getElementById('clock');
const css = getComputedStyle(document.documentElement);
function decode(seg, hz) {
  const out = []; let u = 0, v = 0, y = 0;
  for (let i = 0; i < seg.u.length; i++) {
    u = i ? u + seg.u[i] : seg.u[0]; v = i ? v + seg.v[i] : seg.v[0]; y = i ? y + seg.yaw[i] : seg.yaw[0];
    out.push([seg.t0 + i / hz, u, v, ((y % 360) + 360) % 360]);
  }
  return out;
}
let round, tracks, t = 0, playing = false, last = 0;
function load(n) {
  round = DATA.rounds[n]; t = 0; tracks = {};
  for (const [slot, segs] of Object.entries(round.tracks)) tracks[slot] = segs.map(s => decode(s, round.hz));
  draw();
}
function at(slot) {
  let lastSeen = null;
  for (const seg of tracks[slot] || []) {
    const a = seg[0][0], b = seg[seg.length - 1][0];
    if (t >= a && t <= b) { const i = Math.min(seg.length - 1, Math.round((t - a) * round.hz)); return {p: seg[i], live: true}; }
    if (b < t) lastSeen = seg[seg.length - 1];
  }
  return lastSeen && t - lastSeen[0] <= 2 ? {p: lastSeen, live: false} : null;
}
function draw() {
  cx.clearRect(0, 0, 1024, 1024);
  if (img.complete) cx.drawImage(img, 0, 0, 1024, 1024);
  const s = 1024 / 10000;
  for (const k of round.kills) if (k.t <= t && k.u !== null) {
    cx.strokeStyle = '#bbb'; cx.lineWidth = 3; const x = k.u * s, y = k.v * s;
    cx.beginPath(); cx.moveTo(x - 7, y - 7); cx.lineTo(x + 7, y + 7); cx.moveTo(x + 7, y - 7); cx.lineTo(x - 7, y + 7); cx.stroke();
  }
  for (const p of round.players) {
    const hit = at(String(p.slot)); if (!hit) continue;
    const [, u, v, yaw] = hit.p, x = u * s, y = v * s;
    const color = css.getPropertyValue(p.side === 'A' ? '--a' : '--b');
    cx.fillStyle = color; cx.strokeStyle = color; cx.lineWidth = 3;
    if (hit.live) {
      const r = yaw * Math.PI / 180;
      cx.beginPath(); cx.moveTo(x, y); cx.arc(x, y, 26, r - 0.35, r + 0.35); cx.closePath(); cx.globalAlpha = 0.35; cx.fill(); cx.globalAlpha = 1;
      cx.beginPath(); cx.arc(x, y, 12, 0, 2 * Math.PI); cx.fill();
      cx.fillStyle = '#fff'; cx.font = 'bold 13px system-ui'; cx.textAlign = 'center'; cx.textBaseline = 'middle';
      cx.fillText(p.agent.slice(0, 2), x, y);
    } else { cx.beginPath(); cx.arc(x, y, 12, 0, 2 * Math.PI); cx.stroke(); }
  }
  scrub.value = Math.round(1000 * t / round.t_end); clock.textContent = t.toFixed(1) + ' s';
}
function tick(now) {
  if (playing) { t = Math.min(round.t_end, t + (now - last) / 1000 * Number(document.getElementById('speed').value)); if (t >= round.t_end) playing = false; draw(); }
  last = now; requestAnimationFrame(tick);
}
for (const n of Object.keys(DATA.rounds)) sel.add(new Option(n, n));
sel.onchange = () => load(sel.value);
document.getElementById('play').onclick = () => { playing = !playing; };
scrub.oninput = () => { t = round.t_end * scrub.value / 1000; draw(); };
img.onload = draw; load(sel.value); requestAnimationFrame(tick);
</script></body></html>
"""


# ---------------------------------------------------------------- dry run


def load_link_candidates(session, match_uuid: str) -> tuple[list[lk.DbMatch], dict[str, int]]:
    """Reads, never writes: the `matches` rows for this UUID and everything the linker compares."""
    from sqlalchemy import func

    from app.models import KillEvent, Match, MatchPlayer, Player, Round

    candidates = []
    for match in session.query(Match).filter(func.lower(Match.external_id) == match_uuid.lower()).all():
        players = []
        for mp, player in (session.query(MatchPlayer, Player).join(Player, MatchPlayer.player_id == Player.id)
                           .filter(MatchPlayer.match_id == match.id).all()):
            team = mp.team.value if hasattr(mp.team, "value") else str(mp.team)
            # players.riot_subject arrives with migration 0012 (Stage 2); until then there are no anchors.
            players.append(lk.DbPlayer(mp.id, player.id, mp.agent, team, getattr(player, "riot_subject", None)))
        rounds = session.query(Round).filter(Round.match_id == match.id).order_by(Round.round_number).all()
        number_of = {r.id: r.round_number for r in rounds}
        kills = []
        if rounds:
            for kill in session.query(KillEvent).filter(KillEvent.round_id.in_(list(number_of))).all():
                kills.append(lk.DbKill(kill.id, number_of[kill.round_id], kill.event_time_seconds,
                                       kill.killer_match_player_id, kill.death_match_player_id))
        candidates.append(lk.DbMatch(
            match.id, match.external_id, match.team1_rounds_won, match.team2_rounds_won,
            [lk.DbRound(r.round_number, r.outcome, r.plant_time, r.defuse_time) for r in rounds], players, kills))
    owners: dict[str, int] = {}
    if hasattr(Player, "riot_subject"):
        owners = {str(s).lower(): pid for pid, s in session.query(Player.id, Player.riot_subject)
                  .filter(Player.riot_subject.isnot(None)).all()}
    return candidates, owners


def dry_run(replay: CondensedReplay, loader) -> lk.LinkResult:
    view = lk.ReplayLinkView(replay.match_uuid, replay.round_count, replay.report["dropped_final_round"],
                             replay.players, replay.link_inputs)
    candidates, owners = loader(replay.match_uuid)
    return lk.link(view, candidates, owners)


def _db_loader():
    from sqlalchemy import text

    from app.config import settings
    from app.db import SessionLocal

    if settings.demo_mode:
        raise SystemExit("REFUSED: demo mode; the ValoMaths demo has no replays")
    session = SessionLocal()
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))
        if session.execute(text("SELECT current_database()")).scalar() == "valomaths_demo":
            raise SystemExit("REFUSED: connected to the demo database")

    def load(match_uuid):
        try:
            return load_link_candidates(session, match_uuid)
        finally:
            session.rollback()
            session.close()

    return load


# ---------------------------------------------------------------- main


def _source(args, manifest: dict, strict: bool) -> tuple[str | None, Path | None]:
    """(the .vrf's hash, its path), or (None, None) for a preview without the file."""
    vrf = args.vrf or default_archive() / str(manifest.get("source_file") or "")
    if vrf.is_file():
        return sha256_file(vrf), vrf
    if strict:
        raise SystemExit(f"REFUSED: the exported .vrf is not at {vrf}; pass --vrf")
    print(f"WARNING: no .vrf at {vrf}; the source hash is NOT checked and the map can't be read "
          f"from it (preview only)")
    return None, None


def _build(args, strict: bool) -> dict | None:
    path = (args.parser_dir or default_parser_dir()) / "bin" / "BUILD.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if strict:
        raise SystemExit(f"REFUSED: no {path}; build the parser with scripts\\build_replay_parser.ps1")
    print(f"WARNING: no {path}; the parser build is NOT checked (preview only)")
    return None


def main(argv: list[str] | None = None, loader_factory=_db_loader) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--map", help="the map, when neither the export nor the .vrf names it (preview only)")
    parser.add_argument("--vrf", type=Path, help="the exported .vrf (default: the archive's copy)")
    parser.add_argument("--parser-dir", type=Path, help="the parser build (default $REPLAY_PARSER_DIR)")
    parser.add_argument("--out", type=Path, help="preview folder (default %%TEMP%%\\valo-replay\\<match uuid>)")
    args = parser.parse_args(argv)
    if not (args.preview or args.dry_run):
        raise SystemExit("Storing replays arrives in Stage 2 (docs/replay-viewer-plan.md); use --preview or --dry-run.")
    if args.map and not args.preview:
        parser.error("--map is for --preview only")

    try:
        manifest = load_manifest(args.export_dir)  # not the 1.5 GB of rows: condense streams them
    except ContractError as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return 3
    strict = args.dry_run
    source_sha256, vrf_path = _source(args, manifest, strict)
    try:
        replay = condense_export_dir(args.export_dir, source_sha256=source_sha256, build=_build(args, strict),
                                     map_override=args.map, allow_blocking=args.preview, vrf_path=vrf_path)
    except ContractError as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return 3

    print(f"{replay.map_name}, {replay.round_count} rounds, {replay.hz} Hz, recipe {replay.recipe}")
    if args.preview:
        checks = preview_checks(replay)
        out = args.out or default_preview_root() / replay.match_uuid
        out.mkdir(parents=True, exist_ok=True)
        page = out / "preview.html"
        page.write_text(preview_html(replay, checks), encoding="utf-8")
        for name, ok, detail in checks:
            print(f"{'PASS' if ok else 'FAIL'}  {name}{'  (' + detail + ')' if detail else ''}")
        print(f"preview: {page}")
        return 0 if all(ok for _, ok, _ in checks) else 1

    eligibility = replay.link_inputs["eligibility"]
    if not eligibility["eligible"]:
        # P-f: the same check the linker makes, before any DB read.
        print(json.dumps({"status": "refused", "report": {"check": "eligibility",
                                                          "reasons": eligibility["reasons"]}}, indent=2))
        return 1
    result = dry_run(replay, loader_factory())
    print(json.dumps({"status": result.status, "report": result.report}, indent=2, default=str))
    return 0 if result.status == "linked" else 1


if __name__ == "__main__":
    sys.exit(main())
