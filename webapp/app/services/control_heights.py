"""A map's height assets in the database (`control_heights`, migration 0019;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 1 and 4).

- `active_digests`: each map's active digest, which is what a round's fingerprint reads
  (app/services/replay_control.py `geometry_inputs`). A row of another HEIGHT_VERSION is left out: this
  deploy's engine can't load it, so the map computes flat until its next build.
- **Trusting a result** (`verify_asset`, `integrity`): before anything is stored, the asset's bytes are opened
  by a child process (app/control/height_verify.py; this process never imports numpy) and must be the asset
  the result names, of this format; the raw arrays and topology must be valid; the report's walkable count and readiness must match the
  child deriving them on this map; and its must-block counts and outcome must match the child rerunning
  this deploy's actual lines. Kill-line counts and shares must add up (the child has no replay blobs). A result that fails
  any of it is the build's failure: nothing is stored.
- **The gate** (`gate`, `store_build`): a trusted build goes live when the map is at the bar and both checks
  pass (`active`; the one before it becomes `superseded`); otherwise it is stored `rejected` with its report
  and the previous asset stays. Either way it is the map's last build, which decides when the next one is due
  (app/services/replay_heights_remote.py).
- `activate` and `deactivate`: the operator's way back (scripts/control_heights.py), and how a map whose
  evidence was deleted is turned off.

Every change to which row is active (a build going live, `activate`, `deactivate`) takes the map's advisory
lock first and writes with SQL statements by row id, never through loaded objects: two writers wait for each
other, and activating the row that is already active changes nothing. The one-active-row index is the
backstop, not the ordering.

Making a row active or not changes the map's fingerprint inputs, so its stored rounds turn stale; they stay
on the page, marked out of date, until the idle queue has recomputed them.

Standard library and the DB only.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from app.models.replay import ControlHeight
from app.replays import control_format as cf
from app.replays import db as replay_db
from app.replays import height_inputs

ACTIVE, REJECTED, SUPERSEDED = "active", "rejected", "superseded"
WEBAPP_ROOT = Path(__file__).resolve().parents[2]


class Busy(Exception):
    """Another change to this map's heights got there first; nothing was written."""


class HasGeneration(Exception):
    """The map has a published feature generation, which names its current heights."""


GENERATION_NOTE = ("{map} has a published feature generation ({generation}); it names the heights it was compiled "
                   "against, so changing them would make every round of the map fail verification. Republish the "
                   "generation against the heights you want first")


def _generation_note(map_name: str, supplied: str | None = None) -> str | None:
    from app.services import replay_control  # lazy: this service is imported by replay_control

    generation = (replay_control.geometry_inputs(map_name, heights=None) or {}).get("features") or supplied
    return GENERATION_NOTE.format(map=map_name, generation=generation) if generation else None


def lock_name(map_name: str) -> str:
    return f"control-heights:{map_name}"


def _usable(rules: dict | None) -> bool:
    return (rules or {}).get("version") == cf.HEIGHT_VERSION


def active_digests(db) -> dict[str, str]:
    """{map: digest} of the active rows this deploy can load. One query."""
    rows_ = db.query(ControlHeight.map_name, ControlHeight.digest, ControlHeight.rules) \
        .filter(ControlHeight.status == ACTIVE)
    return {name: digest for name, digest, rules in rows_ if _usable(rules)}


def rows(db, map_name: str | None = None) -> list[ControlHeight]:
    """Builds, newest first, without their asset bytes."""
    query = db.query(ControlHeight).options(defer(ControlHeight.asset))
    if map_name:
        query = query.filter(ControlHeight.map_name == map_name)
    return query.order_by(ControlHeight.built_at.desc(), ControlHeight.id.desc()).all()


def last_builds(db) -> dict[str, ControlHeight]:
    """Each map's newest build (any status), without its asset and report."""
    out: dict[str, ControlHeight] = {}
    query = db.query(ControlHeight).options(defer(ControlHeight.asset), defer(ControlHeight.report)) \
        .order_by(ControlHeight.built_at.desc(), ControlHeight.id.desc())
    for row in query:
        out.setdefault(row.map_name, row)
    return out


def active_rows(db) -> dict[str, ControlHeight]:
    """Each map's active row (any format), without its asset and report."""
    query = db.query(ControlHeight).options(defer(ControlHeight.asset), defer(ControlHeight.report)) \
        .filter(ControlHeight.status == ACTIVE)
    return {row.map_name: row for row in query}


def asset_bytes(db, map_name: str, digest: str) -> bytes | None:
    row = db.query(ControlHeight.asset).filter(ControlHeight.map_name == map_name, ControlHeight.digest == digest) \
        .order_by(ControlHeight.id.desc()).first()
    return None if row is None else bytes(row[0])


def verify_asset(data: bytes, map_name: str, timeout_s: float = 120.0, *,
                 asset_dir: Path | None = None, must_block: Path | None = None) -> dict:
    """What the bytes are, by app/control/height_verify.py in a child process: its description, or {"error"}."""
    try:
        done = subprocess.run([sys.executable, "-m", "app.control.height_verify", "--map", map_name,
                               "--asset-dir", str(asset_dir or height_inputs.CONTROL_DIR),
                               "--must-block", str(must_block or height_inputs.MUST_BLOCK)],
                              input=data, capture_output=True, timeout=timeout_s, cwd=str(WEBAPP_ROOT), check=False)
        found = json.loads(done.stdout or b"{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {"error": f"{type(error).__name__}: {error}"[:300]}
    return found if isinstance(found, dict) and found else {"error": f"the verifier exited {done.returncode}"}


def _count(value) -> bool:
    return type(value) is int and value >= 0


def _share(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def integrity(result_digest: str, report, verified: dict, must_block: str | None) -> list[str]:
    """Why a build's result can't be trusted; empty when it can. `verified` is `verify_asset`'s answer for the
    result's bytes, `must_block` this deploy's check-set hash (None when its own file is unreadable: then
    nothing is trusted). Whether the build is good enough is `gate`'s question, not this one's."""
    why = []
    if verified.get("error"):
        why.append(f"the asset could not be opened: {verified['error']}")
    else:
        if verified.get("digest") != result_digest:
            why.append(f"the bytes are {verified.get('digest')}: that is not the asset {result_digest}")
        if verified.get("version") != cf.HEIGHT_VERSION:
            why.append(f"the asset is height version {verified.get('version')}, not {cf.HEIGHT_VERSION}")
    if not isinstance(report, dict):
        return [*why, "the result's report is not a report"]
    walkable, supported = report.get("walkable_cells"), report.get("supported_cells")
    if not _count(walkable) or not _count(supported) or walkable == 0 or supported > walkable:
        why.append("the report's cell counts are missing or impossible")
    else:
        if not verified.get("error") and verified.get("supported_cells") != supported:
            why.append(f"the report says {supported} supported cells, the asset has {verified.get('supported_cells')}")
        if not _share(report.get("supported")) or abs(report["supported"] - supported / walkable) > 0.001:
            why.append("the report's supported share isn't its counts'")
    if not verified.get("error"):
        if verified.get("walkable_cells") != walkable:
            why.append("the report's walkable cells aren't this map's walkable cells")
        if report.get("ready") != verified.get("ready") or report.get("not_ready") != verified.get("not_ready"):
            why.append("the report's readiness isn't the asset's readiness on this map")
    ready, not_ready = report.get("ready"), report.get("not_ready")
    if type(ready) is not bool or not isinstance(not_ready, list) or ready == bool(not_ready):
        why.append("the report's ready flag and its reasons disagree")
    kills = report.get("kill_lines")
    if not isinstance(kills, dict) or not _count(kills.get("qualifying")) or not _count(kills.get("blocked")) \
            or kills["qualifying"] == 0 or kills["blocked"] > kills["qualifying"] or not _share(kills.get("share")) \
            or abs(kills["share"] - kills["blocked"] / kills["qualifying"]) > 0.001 \
            or type(kills.get("passes")) is not bool \
            or kills["passes"] != (kills["blocked"] / kills["qualifying"] <= cf.KILL_LINE_BAR):
        why.append("the kill lines' result is missing, checked no kill, or doesn't add up")
    must = report.get("must_block")
    if not isinstance(must, dict) or not all(_count(must.get(k)) for k in ("lines", "checked", "unchecked", "blocked")) \
            or must["checked"] + must["unchecked"] != must["lines"] or must["blocked"] > must["checked"] \
            or type(must.get("passes")) is not bool \
            or must["passes"] != (must["unchecked"] == 0 and must["blocked"] == must["checked"]):
        why.append("the must-block result is missing or doesn't add up")
    if must_block is None or not isinstance(must, dict) or must.get("set") != must_block:
        why.append(f"the must-block check ran against check set {(must or {}).get('set') if isinstance(must, dict) else None}, "
                   f"this deploy's is {must_block}")
    actual_must = verified.get("must_block")
    if not verified.get("error") and (not isinstance(actual_must, dict) or not isinstance(must, dict)
            or any(must.get(k) != actual_must.get(k) for k in
                   ("set", "lines", "checked", "unchecked", "blocked", "passes"))):
        why.append("the must-block evidence isn't the check run on this asset and this map")
    return why


def gate(report: dict) -> list[str]:
    """Why a trusted build may not go live (the bar and the two checks a local build uses); empty when it may."""
    why = []
    if not report.get("ready"):
        why += list(report.get("not_ready") or ["below the bar"])
    if report["supported_cells"] / report["walkable_cells"] < cf.HEIGHT_SUPPORTED_MIN and report.get("ready"):
        why.append(f"supported {report['supported_cells'] / report['walkable_cells']:.1%} is under "
                   f"{cf.HEIGHT_SUPPORTED_MIN:.0%}")
    kills, must = report["kill_lines"], report["must_block"]
    if not kills["passes"]:
        why.append(f"kill lines: {kills['blocked']} of {kills['qualifying']} blocked by the heights")
    if not must["passes"]:
        why.append(f"must-block: {must['blocked']} of {must['lines']} impossible sightlines blocked, "
                   f"{must['unchecked']} could not be checked")
    return why


def _make_active(db, map_name: str, row_id: int | None) -> None:
    """Under the map's lock: every other active row of the map becomes superseded, then row `row_id` (if any)
    is active. SQL by id, so it doesn't matter what any session has loaded, and the row that is already active
    is never retired. The caller commits."""
    others = db.query(ControlHeight).filter(ControlHeight.map_name == map_name, ControlHeight.status == ACTIVE)
    if row_id is not None:
        others = others.filter(ControlHeight.id != row_id)
    others.update({ControlHeight.status: SUPERSEDED}, synchronize_session=False)
    if row_id is not None:
        db.query(ControlHeight).filter(ControlHeight.id == row_id) \
            .update({ControlHeight.status: ACTIVE}, synchronize_session=False)


def store_build(db, *, map_name: str, digest: str, asset: bytes, report: dict, inputs: dict,
                rules: dict) -> tuple[str, list[str]]:
    """Stores a trusted build behind the gate and commits: ("active", []) or ("rejected", why). Raises Busy
    when another change to the map's heights won the race, or HasGeneration when a published feature
    generation is present at the final lookup under the map lock; nothing is written then."""
    why = gate(report)
    try:
        replay_db.advisory_lock(db, lock_name(map_name))
        note = _generation_note(map_name)
        if note:
            db.rollback()
            raise HasGeneration(note)  # even when it was published after planning: no row and no activation
        row = ControlHeight(map_name=map_name, digest=digest, asset=asset, report=report,
                            match_uuids=height_inputs.matches(inputs), rules=rules, inputs=inputs,
                            inputs_sha=height_inputs.digest(inputs), status=REJECTED if why else SUPERSEDED)
        db.add(row)
        db.flush()                       # it has an id, and is not active yet
        if not why:
            _make_active(db, map_name, row.id)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise Busy(map_name) from error
    db.expire_all()
    return (REJECTED, why) if why else (ACTIVE, [])


def activate(db, map_name: str, digest: str, generation: str | None = None) -> str | None:
    """Makes the map's newest build with this digest the active one (it may be already). None when done, else
    why not."""
    replay_db.advisory_lock(db, lock_name(map_name))
    note = _generation_note(map_name, generation)
    if note:
        db.rollback()
        return note
    found = db.query(ControlHeight.id, ControlHeight.rules) \
        .filter(ControlHeight.map_name == map_name, ControlHeight.digest == digest) \
        .order_by(ControlHeight.id.desc()).first()
    if found is None:
        db.rollback()
        return f"no build of {map_name} has digest {digest}"
    row_id, rules = found
    if not _usable(rules):
        db.rollback()
        return (f"{digest} is height version {(rules or {}).get('version')}, this deploy's is {cf.HEIGHT_VERSION}: "
                f"the engine can't load it")
    try:
        _make_active(db, map_name, row_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        return f"another change to {map_name}'s heights got there first; look again and retry"
    db.expire_all()
    return None


def deactivate(db, map_name: str, generation: str | None = None) -> bool:
    """Leaves the map with no active heights (flat). Whether there was one."""
    replay_db.advisory_lock(db, lock_name(map_name))
    note = _generation_note(map_name, generation)
    if note:
        db.rollback()
        raise HasGeneration(note)
    had = db.query(ControlHeight.id).filter(ControlHeight.map_name == map_name,
                                            ControlHeight.status == ACTIVE).first() is not None
    _make_active(db, map_name, None)
    db.commit()
    db.expire_all()
    return had
