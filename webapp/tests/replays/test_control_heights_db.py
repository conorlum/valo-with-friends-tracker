"""Heights in the database (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md): what a rebuild
costs, the table's reads and writes, the active digest in a round's inputs, the gate, and the operator's
commands. SQLite, and no engine in this process."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WEBAPP / "scripts"))

from test_control_store import db, factory, linked, put_row  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed, pg  # noqa: E402,F401  (fixtures)


def test_sizes_and_the_cycle_estimate(tmp_path):
    import measure_height_rebuild as measure

    for match, n, size in (("m1", 1, 1000), ("m1", 2, 3000), ("m2", 1, 2000)):
        (tmp_path / match).mkdir(exist_ok=True)
        (tmp_path / match / f"{n}.json.gz").write_bytes(b"x" * size)
    got = measure.sizes(tmp_path)
    assert got == {"matches": 2, "rounds": 3, "bytes": 6000, "largest": 3000}
    est = measure.estimate(got, build_s=30.0, warm_s=120.0, round_s=40.0, workers=2)
    assert est == {"sent_mb": 0.008, "batches": 1, "build_min": 0.5, "warm_min": 2.0, "rounds_min": 1.0,
                   "cycle_min": 3.5}


def test_cost_probe_uses_only_the_requested_maps_rounds_and_can_read_them_twice(tmp_path):
    import measure_height_rebuild as measure
    from app.control.height_job import BlobDir
    from app.replays import format as fmt

    for match, map_name in (("a-bind", "Bind"), ("z-ascent", "Ascent")):
        (tmp_path / match).mkdir()
        (tmp_path / match / "1.json.gz").write_bytes(fmt.encode_blob({"v": 1, "map": map_name}))
    rounds = BlobDir(tmp_path, "Ascent")
    for _ in range(2):
        assert [(m, n, b["map"]) for m, n, b in rounds] == [("z-ascent", 1, "Ascent")]
        assert [p.parent.name for p in rounds.read[:3]] == ["z-ascent"]  # the timing loop's exact selection
    assert measure.sizes(tmp_path, "Ascent")["rounds"] == 1


import ast  # noqa: E402

from app.models.replay import ControlHeight  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.replays import height_inputs as hi  # noqa: E402
from app.services import control_heights as ch  # noqa: E402
from app.services import replay_control as rc  # noqa: E402

RULES = {"version": cf.HEIGHT_VERSION, "revision": cf.HEIGHT_RULES_REVISION, "constants": "c0ffee"}
CHECKS = "k" * 12


def report(**changes) -> dict:
    """A build's report as the worker returns it, whole and consistent: 6,000 walkable cells, 4,200 supported."""
    out = {"walkable_cells": 6000, "supported_cells": 4200, "supported": 0.7, "ready": True, "not_ready": [],
           "kill_lines": {"qualifying": 400, "blocked": 2, "share": 0.005, "passes": True},
           "must_block": {"set": CHECKS, "lines": 3, "checked": 3, "unchecked": 0, "blocked": 3, "passes": True}}
    for key, value in changes.items():
        out[key] = {**out[key], **value} if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def inputs(*uuids) -> dict:
    return hi.manifest("Toy", [[u, "p.c11.f1.a1", u * 8, 20] for u in uuids or ("m1", "m2")],
                       {"sight": "s", "walk": "w", "scale": 7e-5}, CHECKS)


def build(db, name, digest, rep=None, uuids=("m1", "m2"), rules=RULES):
    return ch.store_build(db, map_name=name, digest=digest, asset=b"npz-" + digest.encode(),
                          report=report() if rep is None else rep, inputs=inputs(*uuids), rules=dict(rules))


def verified(digest, **changes) -> dict:
    return {"digest": digest, "version": cf.HEIGHT_VERSION, "walkable_cells": 6000,
            "supported_cells": 4200, "floor_cells": 5000, "ready": True, "not_ready": [],
            "must_block": report()["must_block"], **changes}


def test_a_build_at_the_bar_goes_live_and_the_one_before_it_is_superseded(db, linked):
    name = linked.map_name
    assert ch.active_digests(db) == {} and "height" not in rc.geometry_inputs(name, ch.active_digests(db))
    assert build(db, name, "aaaaaaaaaaaa") == ("active", [])
    assert ch.active_digests(db) == {name: "aaaaaaaaaaaa"}
    assert rc.geometry_inputs(name, ch.active_digests(db))["height"] == "aaaaaaaaaaaa"
    assert build(db, name, "bbbbbbbbbbbb", uuids=("m1", "m2", "m3")) == ("active", [])
    assert ch.active_digests(db) == {name: "bbbbbbbbbbbb"}
    assert [(r.digest, r.status) for r in ch.rows(db, name)] == [("bbbbbbbbbbbb", "active"), ("aaaaaaaaaaaa", "superseded")]
    assert ch.asset_bytes(db, name, "aaaaaaaaaaaa") == b"npz-aaaaaaaaaaaa" and ch.asset_bytes(db, name, "nope") is None
    last = ch.last_builds(db)[name]
    assert last.match_uuids == ["m1", "m2", "m3"] and last.inputs_sha == hi.digest(inputs("m1", "m2", "m3"))
    assert ch.active_rows(db)[name].digest == "bbbbbbbbbbbb"


@pytest.mark.parametrize("rep, why", [
    (report(ready=False, not_ready=["supported 41.0% is under 60%"], supported_cells=2460, supported=0.41), "under 60%"),
    (report(ready=False, not_ready=["1 unresolved area(s) larger than 12 cells touch a cell with two floors"]), "unresolved"),
    (report(kill_lines={"blocked": 12, "share": 0.03, "passes": False}), "kill lines"),
    (report(must_block={"blocked": 2, "passes": False}), "must-block"),
    (report(must_block={"checked": 2, "unchecked": 1, "blocked": 2, "passes": False}), "must-block"),
])
def test_a_build_below_the_bar_or_failing_a_check_is_rejected_and_the_old_heights_stay(db, linked, rep, why):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    assert ch.integrity("cccccccccccc", rep, verified("cccccccccccc", supported_cells=rep["supported_cells"], ready=rep["ready"],
                                 not_ready=rep["not_ready"], must_block=rep["must_block"]), CHECKS) == [], "an honest failing build is still a whole result"
    status, reasons = build(db, name, "cccccccccccc", rep=rep)
    assert status == "rejected" and any(why in r for r in reasons)
    assert ch.active_digests(db) == {name: "aaaaaaaaaaaa"}
    assert ch.last_builds(db)[name].status == "rejected", "the last build is the rejected one: it sets the next due"


@pytest.mark.parametrize("name, rep, seen, why", [
    ("the asset is other bytes", report(), verified("dddddddddddd"), "is not the asset"),
    ("the asset can't be opened", report(), {"error": "BadZipFile"}, "could not be opened"),
    ("another format", report(), verified("cccccccccccc", version=cf.HEIGHT_VERSION + 1), "height version"),
    ("its count isn't the asset's", report(), verified("cccccccccccc", supported_cells=4100), "supported cells"),
    ("the share isn't the counts'", report(supported=0.9), verified("cccccccccccc"), "share"),
    ("invented map size", report(walkable_cells=6000), verified("cccccccccccc", walkable_cells=8000), "walkable"),
    ("hidden unresolved area", report(), verified("cccccccccccc", ready=False, not_ready=["unresolved"]), "readiness"),
    ("empty evidence for a nonempty set", report(must_block={"lines": 0, "checked": 0, "blocked": 0}),
     verified("cccccccccccc"), "must-block"),
    ("ready, with reasons", report(not_ready=["thin"]), verified("cccccccccccc"), "ready"),
    ("more blocked than qualifying", report(kill_lines={"blocked": 500}), verified("cccccccccccc"), "kill lines"),
    ("no kill qualified", report(kill_lines={"qualifying": 0, "blocked": 0, "share": 0.0}), verified("cccccccccccc"), "kill lines"),
    ("passes, over the bar", report(kill_lines={"blocked": 40, "share": 0.1}), verified("cccccccccccc"), "kill lines"),
    ("a share that isn't a number", report(kill_lines={"share": float("nan")}), verified("cccccccccccc"), "kill lines"),
    ("another check set", report(must_block={"set": "o" * 12}), verified("cccccccccccc"), "check set"),
    ("no check set", report(must_block={"set": None}), verified("cccccccccccc"), "check set"),
    ("passes, with a line unchecked", report(must_block={"checked": 2, "unchecked": 1}), verified("cccccccccccc"), "must-block"),
    ("no checks at all", {"walkable_cells": 6000, "supported_cells": 4200, "supported": 0.7, "ready": True,
                          "not_ready": []}, verified("cccccccccccc"), "kill lines"),
    ("not a report", ["ready"], verified("cccccccccccc"), "not a report"),
])
def test_a_result_that_is_not_what_it_says_is_never_trusted(name, rep, seen, why):
    reasons = ch.integrity("cccccccccccc", rep, seen, CHECKS)
    assert reasons and any(why in r for r in reasons), (name, reasons)


def test_a_missing_check_set_on_this_side_trusts_nothing():
    assert any("check set" in r for r in ch.integrity("cccccccccccc", report(), verified("cccccccccccc"), None))


def test_store_build_rechecks_a_generation_without_relying_on_the_planners_lookup(db, linked, monkeypatch):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    before = ch.active_digests(db)
    real = rc.geometry_inputs
    monkeypatch.setattr(rc, "geometry_inputs", lambda m, heights=None: {**(real(m, heights) or {}), "features": "new-generation"})
    with pytest.raises(ch.HasGeneration):
        build(db, name, "bbbbbbbbbbbb")
    assert ch.active_digests(db) == before and len(ch.rows(db, name)) == 1
    assert "generation" in ch.activate(db, name, "aaaaaaaaaaaa")
    with pytest.raises(ch.HasGeneration):
        ch.deactivate(db, name)
    assert ch.active_digests(db) == before


def test_a_row_from_another_format_is_not_active(db, linked):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa", rules={**RULES, "version": cf.HEIGHT_VERSION + 1})
    assert db.query(ControlHeight).one().status == "active"
    assert ch.active_digests(db) == {}, "an asset this deploy can't load is never a round's input"


def test_new_heights_make_a_maps_rounds_stale_and_they_are_still_served(db, linked):
    put_row(db, linked, 1)
    assert (rc.round_control(db, linked, 1).status, rc.round_control(db, linked, 1).stale) == ("ok", False)
    build(db, linked.map_name, "aaaaaaaaaaaa")
    answer = rc.round_control(db, linked, 1)
    assert (answer.status, answer.stale) == ("ok", True), "the page keeps the old round until its recompute lands"
    assert [p.reason for p in rc.plan(db, rounds={1})] == ["stale"]
    ch.deactivate(db, linked.map_name)
    assert rc.round_control(db, linked, 1).stale is False, "and going back makes it current again"


def test_activate_puts_an_earlier_asset_back_and_off_leaves_none(db, linked):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    build(db, name, "bbbbbbbbbbbb")
    assert ch.activate(db, name, "aaaaaaaaaaaa") is None and ch.active_digests(db) == {name: "aaaaaaaaaaaa"}
    assert "no build" in ch.activate(db, name, "zzzzzzzzzzzz")
    build(db, name, "dddddddddddd", rules={**RULES, "version": cf.HEIGHT_VERSION + 1})
    assert "can't load it" in ch.activate(db, name, "dddddddddddd")
    assert ch.deactivate(db, name) is True and ch.active_digests(db) == {} and ch.deactivate(db, name) is False
    assert {r.status for r in ch.rows(db, name)} == {"superseded"}


def test_activating_the_digest_that_is_already_active_leaves_it_active(db, linked, factory):
    # The row is loaded as active; retiring "the active row" in SQL and then setting the loaded one active
    # again changes nothing the ORM can see, and the map would be left with no heights.
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    for _ in range(2):
        assert ch.activate(db, name, "aaaaaaaaaaaa") is None
        other = factory()                                   # read with a session that loaded nothing before
        assert ch.active_digests(other) == {name: "aaaaaaaaaaaa"}
        assert [r.status for r in ch.rows(other, name)] == ["active"]
        other.close()
    stale = factory()
    row = stale.query(ControlHeight).one()                  # a session holding the row from before a change
    ch.deactivate(db, name)
    assert ch.activate(stale, name, "aaaaaaaaaaaa") is None and row.status == "active"
    assert ch.active_digests(factory()) == {name: "aaaaaaaaaaaa"}
    stale.close()


def test_pg_two_changes_to_one_map_wait_for_each_other(pg):
    # The one-active-row index refuses a second active row but doesn't order two writers. The map's advisory
    # lock does: the second waits, then acts on what the first left.
    import threading

    from sqlalchemy import text

    from app.replays import db as replay_db

    first, second = pg(), pg()
    first.execute(text("DELETE FROM control_heights"))
    first.commit()
    try:
        build(first, "Toy", "aaaaaaaaaaaa")
        build(first, "Toy", "bbbbbbbbbbbb")
        replay_db.advisory_lock(first, ch.lock_name("Toy"))          # hold the map's lock in an open transaction
        done = {}
        worker = threading.Thread(target=lambda: done.update(result=ch.activate(second, "Toy", "aaaaaaaaaaaa")))
        worker.start()
        worker.join(timeout=1.0)
        assert worker.is_alive(), "the second change waits for the map's lock"
        first.rollback()
        worker.join(timeout=30)
        assert done == {"result": None}
        assert ch.active_digests(first) == {"Toy": "aaaaaaaaaaaa"}
        assert [r.status for r in ch.rows(first, "Toy")].count("active") == 1
        racing = [threading.Thread(target=lambda s=s, d=d: ch.activate(s, "Toy", d))
                  for s, d in ((first, "bbbbbbbbbbbb"), (second, "aaaaaaaaaaaa"))]
        for t in racing:
            t.start()
        for t in racing:
            t.join(timeout=30)
        first.expire_all()
        assert [r.status for r in ch.rows(first, "Toy")].count("active") == 1, "whichever won, exactly one is active"
    finally:
        first.rollback()
        first.execute(text("DELETE FROM control_heights"))
        first.commit()
        first.close()
        second.close()


def _calls_without_heights(path: Path) -> list[int]:
    """Line numbers of geometry_inputs(...) / round_fingerprint(...) calls that don't pass `heights`."""
    need = {"geometry_inputs": 2, "round_fingerprint": 4}        # how many positional arguments reach `heights`
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name in need and len(node.args) < need[name] and not any(k.arg == "heights" for k in node.keywords):
                out.append(node.lineno)
    return out


def test_every_caller_passes_the_active_heights():
    # A call that leaves `heights` out falls back to the committed files: its fingerprint would never match a
    # row computed with the database's heights, and the round would read as stale forever.
    files = [*(WEBAPP / "app").rglob("*.py"), *(WEBAPP / "scripts").glob("*.py")]
    missing = {str(p.relative_to(WEBAPP)): lines for p in files if (lines := _calls_without_heights(p))}
    assert missing == {}, missing


def test_a_map_with_a_published_feature_generation_cant_have_its_heights_changed_by_hand(db, linked):
    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa")
    build(db, name, "bbbbbbbbbbbb")
    refused = ch.activate(db, name, "aaaaaaaaaaaa", generation="feat0000feat0000")
    assert "feature generation" in refused and ch.active_digests(db) == {name: "bbbbbbbbbbbb"}
    with pytest.raises(ch.HasGeneration):
        ch.deactivate(db, name, generation="feat0000feat0000")
    assert ch.active_digests(db) == {name: "bbbbbbbbbbbb"}


def test_the_operator_lists_activates_exports_and_turns_off(factory, db, linked, capsys, tmp_path, monkeypatch):
    import control_heights as command

    name = linked.map_name
    build(db, name, "aaaaaaaaaaaa", rep=report(seconds=310.0))
    build(db, name, "bbbbbbbbbbbb", rep=report(ready=False, not_ready=["thin"]))
    assert command.main(["list"], session_factory=factory) == 0
    out = capsys.readouterr().out
    assert f"{name} aaaaaaaaaaaa active" in out and "supported 70.0%" in out and "310 s" in out
    assert f"{name} bbbbbbbbbbbb rejected" in out and "rejected: thin" in out
    assert command.main(["activate", "--map", name, "--digest", "aaaaaaaaaaaa"], session_factory=factory) == 0
    assert "recomputed" in capsys.readouterr().out, "activating what is active is done, not an error"
    db.expire_all()
    assert ch.active_digests(db) == {name: "aaaaaaaaaaaa"}
    assert command.main(["activate", "--map", name, "--digest", "000000000000"], session_factory=factory) == 2
    out_dir = tmp_path / "export"
    assert command.main(["export", "--map", name, "--out", str(out_dir)], session_factory=factory) == 0
    assert (out_dir / f"{name}.height.npz").read_bytes() == b"npz-aaaaaaaaaaaa"
    wrapper = json.loads((out_dir / f"{name}.height.json").read_text(encoding="utf-8"))
    assert wrapper["height_sha"] == "aaaaaaaaaaaa" and wrapper["height"]["supported_cells"] == 4200
    assert command.main(["export", "--map", name, "--out", str(WEBAPP / "here")], session_factory=factory) == 2
    monkeypatch.setattr(command, "generation_of", lambda map_name: "feat0000feat0000")
    assert command.main(["off", "--map", name], session_factory=factory) == 2
    assert command.main(["activate", "--map", name, "--digest", "aaaaaaaaaaaa"], session_factory=factory) == 2
    assert "feature generation" in capsys.readouterr().err
    monkeypatch.setattr(command, "generation_of", lambda map_name: None)
    assert command.main(["off", "--map", name], session_factory=factory) == 0
    db.expire_all()
    assert ch.active_digests(db) == {}
    assert command.main(["off", "--map", name], session_factory=factory) == 0 and "no active heights" in capsys.readouterr().out
    assert command.main(["export", "--map", name, "--out", str(out_dir)], session_factory=factory) == 2
