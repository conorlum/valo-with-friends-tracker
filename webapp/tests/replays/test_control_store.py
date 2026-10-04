"""Map control's storage, freshness, endpoint and command (Stage 3; docs/replay-map-control-plan.md,
"Storage", "Delivery", "Where it runs"). SQLite, no engine: rows are written with made-up bytes, as
compute_control.py would store them. The engine-to-bytes side is tests/replays/test_control_format.py."""

import gzip
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_replay_routes import request  # noqa: E402
from test_replay_store import TABLES, add_match, condensed, pg  # noqa: E402,F401  (fixtures)

import compute_control  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.replay import Replay, ReplayGap, ReplayRoundControl, ReplayRoundGapRun  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays import store  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replays as service  # noqa: E402

CONTROL_TABLES = TABLES + [ReplayRoundGapRun.__table__, ReplayGap.__table__]   # R1: the timing-gaps tables


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=CONTROL_TABLES)
    return sessionmaker(bind=engine)


@pytest.fixture
def db(factory):
    session = factory()
    yield session
    session.close()


@pytest.fixture
def linked(db, condensed):
    add_match(db)
    result = store.store_replay(db, condensed, source="local")
    replay = db.get(Replay, result.replay_id)
    assert replay.link_status == "linked"
    assert rc.map_layer(replay.map_name) is not None, "the synthetic map must have the control layer"
    return replay


def put_row(db, replay, n, status="ok", fingerprint=None, data=b"x", data_version=cf.DATA_VERSION):
    groups = rc.side_groups(db, replay)
    row = ReplayRoundControl(replay_id=replay.id, round_number=n, status=status,
                             fingerprint=fingerprint or rc.round_fingerprint(replay, groups, n),
                             data_version=data_version if status == "ok" else None,
                             data=gzip.compress(data) if status == "ok" else None,
                             summary=cf.pack_summary({}) if status == "ok" else None,
                             error=None if status == "ok" else "ControlError: boom")
    db.merge(row)
    db.commit()


# ---------------------------------------------------------------- the link and the fingerprint


def test_sides_follow_the_attacking_team_and_swap_at_the_half(db, linked):
    groups = rc.side_groups(db, linked)
    first, second = rc.round_link(linked, groups, 1), rc.round_link(linked, groups, 13)
    assert len(first["sides"]) == 10 and sorted(first["sides"].values()).count("attack") == 5
    assert all(second["sides"][s] != side for s, side in first["sides"].items())
    team_of = (linked.link_report or {})["side_to_team"]
    for slot, side in first["sides"].items():
        assert (team_of[groups[int(slot)]] == "team-1") == (side == "attack"), "team-1 attacks first"


def test_db_deaths_move_onto_the_replay_clock(db, linked):
    linked.db_deaths = {"2": [{"slot": 3, "t_db": 50.0}, {"slot": None, "t_db": 51.0}]}
    linked.clock_offset = 7.5
    link = rc.round_link(linked, rc.side_groups(db, linked), 2)
    assert link["db_deaths"] == [[3, 42.5]]


def test_an_unlinked_replay_reads_no_link(db, condensed):
    result = store.store_replay(db, condensed, source="upload")
    replay = db.get(Replay, result.replay_id)
    assert rc.round_link(replay, rc.side_groups(db, replay), 1) == {"linked": False, "sides": {}, "db_deaths": []}


def test_the_fingerprint_moves_with_each_input(db, linked, monkeypatch):
    groups = rc.side_groups(db, linked)
    base = rc.round_fingerprint(linked, groups, 1)
    assert len(base) == 16 and base == rc.round_fingerprint(linked, groups, 1)
    assert rc.round_fingerprint(linked, groups, 13) != base, "the sides swap at the half"
    linked.db_deaths = {"1": [{"slot": 0, "t_db": 30.0}]}
    assert rc.round_fingerprint(linked, groups, 1) != base
    linked.db_deaths = None
    assert rc.round_fingerprint(linked, groups, 1) == base
    geometry = dict(rc.geometry_inputs(linked.map_name), sight="changed")
    monkeypatch.setattr(rc, "geometry_inputs", lambda name: geometry)
    assert rc.round_fingerprint(linked, groups, 1) != base
    monkeypatch.undo()
    monkeypatch.setattr(cf, "CONTROL_REVISION", cf.CONTROL_REVISION + 1)
    assert rc.round_fingerprint(linked, groups, 1) != base


def test_condense_revision_is_read_from_the_recipe():
    assert rc.condense_revision("2b66c65a7b11.c10.f1.a9965d90c") == 10
    assert rc.condense_revision("2b66c65a7b11.c9.f1.a9965d90c") == 9
    assert rc.condense_revision("nothing") is None
    assert rc.condense_revision("c20417733181.c9.f1.a9965d90c") == 9, "a parser commit can look like c<digits>"


def test_the_geometry_inputs_are_what_the_engine_loads():
    inputs = rc.geometry_inputs("Ascent")
    _, tags, maps = rc._assets()
    assert inputs["scale"] == maps["Ascent"]["xMultiplier"]
    assert inputs["specials"] == ((tags.get("Ascent") or {}).get("specials") or [])
    assert inputs["sight"] and inputs["walk"]


def test_only_maps_that_passed_the_kill_line_test_have_the_layer():
    index = rc._assets()[0]
    for name, entry in index.items():
        has = rc.map_layer(name) is not None
        assert has == bool((entry.get("kill_lines") or {}).get("passes")), name
    assert rc.map_layer("Nowhere") is None
    assert rc.map_layer("Bind") is None, "no Bind replay has passed yet"


# ---------------------------------------------------------------- the plan


def test_the_plan_finds_missing_stale_and_failed_rounds(db, linked):
    n_rounds = linked.round_count
    assert [(p.round_number, p.reason) for p in rc.plan(db)] == [(n, "missing") for n in range(1, n_rounds + 1)]
    for n in range(1, n_rounds + 1):
        put_row(db, linked, n)
    assert rc.plan(db) == []
    put_row(db, linked, 2, fingerprint="0" * 16)
    put_row(db, linked, 3, status="failed")
    assert [(p.round_number, p.reason) for p in rc.plan(db)] == [(2, "stale")]
    assert [(p.round_number, p.reason) for p in rc.plan(db, retry_failed=True)] == [(2, "stale"), (3, "retry_failed")]
    put_row(db, linked, 3, status="failed", fingerprint="1" * 16)
    assert [p.round_number for p in rc.plan(db)] == [2, 3], "a failure with old inputs is retried"
    assert [p.round_number for p in rc.plan(db, force=True, rounds={1, 4})] == [1, 4]
    assert rc.plan(db, match_uuid="00000000-0000-4000-8000-000000000000") == []
    [planned] = rc.plan(db, rounds={2})
    assert planned.fingerprint == rc.round_fingerprint(linked, rc.side_groups(db, linked), 2)
    assert planned.link == rc.round_link(linked, rc.side_groups(db, linked), 2)


def test_rounds_that_cannot_be_computed_are_listed_not_planned(db, linked, monkeypatch):
    monkeypatch.setattr(rc, "map_layer", lambda name: None)
    planned = rc.plan(db)
    assert {p.reason for p in planned} == {"no_map"} and not any(p.computable for p in planned)
    assert rc.nudge(db) is None
    monkeypatch.undo()
    linked.recipe = linked.recipe.replace(f".c{rc.condense_revision(linked.recipe)}.", ".c9.")
    db.commit()
    assert {p.reason for p in rc.plan(db)} == {"old_blob"}
    lines = compute_control.describe(rc.plan(db))
    assert lines[0] == "0 round(s) to compute" and "re-ingest" in lines[1]


def test_the_nudge_counts_rounds_and_replays(db, linked):
    assert rc.nudge(db).startswith(f"Map control: {linked.round_count} round(s) of 1 replay(s) need computing")
    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
    assert rc.nudge(db) is None


# ---------------------------------------------------------------- storage


def test_a_replaced_replay_takes_its_control_rows_with_it(db, linked, condensed):
    put_row(db, linked, 1)
    result = store.store_replay(db, condensed, source="local", replace=True)
    assert result.action == "replaced"
    assert db.query(ReplayRoundControl).count() == 0


def test_pg_migration_0014_cascades_and_holds_its_checks(pg, condensed):
    """On the real schema (alembic head, `VALO_TEST_DATABASE_URL`): deleting a replay takes its
    control rows through `replay_rounds`, and store.py's replace path deletes them itself."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    session = pg()
    add_match(session)
    replay = session.get(Replay, store.store_replay(session, condensed, source="local").replay_id)
    put_row(session, replay, 1)
    put_row(session, replay, 2, status="failed")
    assert session.query(ReplayRoundControl).count() == 2
    session.add(ReplayRoundControl(replay_id=replay.id, round_number=3, status="ok", fingerprint="0" * 16))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(ReplayRoundControl(replay_id=replay.id, round_number=99, status="failed", fingerprint="0" * 16))
    with pytest.raises(IntegrityError, match="fk_replay_round_control_round"):
        session.commit()
    session.rollback()
    replay = session.get(Replay, store.store_replay(session, condensed, source="local", replace=True).replay_id)
    assert session.query(ReplayRoundControl).count() == 0
    put_row(session, replay, 1)
    session.execute(text("DELETE FROM replays WHERE id = :r"), {"r": replay.id})
    session.commit()
    assert session.query(ReplayRoundControl).count() == 0
    session.close()


def test_an_ok_row_needs_its_bytes(db, linked):
    from sqlalchemy.exc import IntegrityError

    db.add(ReplayRoundControl(replay_id=linked.id, round_number=1, status="ok", fingerprint="0" * 16))
    with pytest.raises(IntegrityError):
        db.commit()


# ---------------------------------------------------------------- the endpoint


def call(db, n, headers=None, uuid=MATCH_UUID):
    return routes.replay_round_control(request(headers), uuid, n, db)


def test_not_ready_then_the_bytes_with_an_etag_and_a_304(db, linked):
    response = call(db, 1)
    assert response.status_code == 202 and json.loads(response.body) == {"status": "not_ready"}
    assert int(response.headers["retry-after"]) > 0
    put_row(db, linked, 1, data=b"control bytes")
    response = call(db, 1)
    stored = db.get(ReplayRoundControl, (linked.id, 1)).data
    assert response.status_code == 200 and response.body == stored
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["cache-control"] == "private, no-cache"
    assert "x-control-stale" not in response.headers
    again = call(db, 1, {"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304 and again.body == b""


def test_a_stale_row_is_still_served_but_flagged(db, linked):
    put_row(db, linked, 1, fingerprint="0" * 16)
    response = call(db, 1)
    assert response.status_code == 200 and response.headers["x-control-stale"] == "1"


def test_a_row_in_an_older_byte_format_is_not_ready_and_planned(db, linked):
    put_row(db, linked, 1, data_version=cf.DATA_VERSION - 1)
    assert call(db, 1).status_code == 202, "the viewer can't decode it"
    assert (1, "stale") in [(p.round_number, p.reason) for p in rc.plan(db)]


def test_a_blob_before_revision_10_is_a_404_that_says_why(db, linked):
    put_row(db, linked, 1)
    linked.recipe = linked.recipe.replace(f".c{rc.condense_revision(linked.recipe)}.", ".c9.")
    db.commit()
    response = call(db, 1)
    assert response.status_code == 404 and json.loads(response.body) == {"status": "old_blob"}


def test_a_failed_round_says_so_without_a_retry(db, linked):
    put_row(db, linked, 1, status="failed")
    response = call(db, 1)
    assert response.status_code == 422 and json.loads(response.body) == {"status": "failed"}
    assert "retry-after" not in response.headers


def test_a_map_without_the_layer_is_a_404_that_says_why(db, linked, monkeypatch):
    monkeypatch.setattr(rc, "map_layer", lambda name: None)
    response = call(db, 1)
    assert response.status_code == 404 and json.loads(response.body) == {"status": "no_map"}


def test_rounds_out_of_range_other_matches_and_demo_mode_are_plain_404s(db, linked, monkeypatch):
    from test_replay_routes import status_of

    assert status_of(lambda: call(db, 0)) == 404
    assert status_of(lambda: call(db, linked.round_count + 1)) == 404
    assert status_of(lambda: call(db, 1, uuid="00000000-0000-4000-8000-000000000000")) == 404
    assert status_of(lambda: call(db, 1, uuid="not-a-uuid")) == 404
    put_row(db, linked, 1)
    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: call(db, 1)) == 404


def test_the_page_says_up_front_whether_the_map_has_the_layer(db, condensed, monkeypatch):
    # Unlinked: the linked page context needs PostgreSQL (test_replay_routes.py); `match` is the same.
    replay = db.get(Replay, store.store_replay(db, condensed, source="upload").replay_id)
    context = service.page_context(db, replay)
    assert context["match"]["control"] == rc.map_layer(replay.map_name) and context["match"]["control"]
    monkeypatch.setattr(rc, "map_layer", lambda name: None)
    assert service.page_context(db, replay)["match"]["control"] is None


# ---------------------------------------------------------------- the command's pieces


def test_the_pool_leaves_a_core_and_memory_never_sizes_it():
    # Memory decides how many run at once (room_for_one), with the measured peak; sizing the pool
    # from a startup guess once held a 12-core run to 8 workers for its whole length.
    assert compute_control.worker_count(None, 12) == 11
    assert compute_control.worker_count(2, 12) == 2
    assert compute_control.worker_count(None, 1) == 1, "always at least one"


def test_the_eta_leaves_out_the_solo_first_round_and_follows_the_recent_pace():
    eta = compute_control.eta_seconds
    assert eta([], 10) is None and eta([184.0], 10) is None
    assert eta([184.0, 200.0], 10) is None, "one parallel finish gives no rate yet"
    # After a 184 s solo first round, rounds finish every 10 s: 10 left is 100 s, not
    # (elapsed / done) * left, which the solo round would inflate.
    assert eta([184.0, 200.0, 210.0, 220.0], 10) == pytest.approx(100.0)
    # Only the last ETA_WINDOW finishes count: a slow stretch (60 s apart) then a fast one (5 s).
    slow = [184.0 + 60 * i for i in range(1, 30)]
    fast = [slow[-1] + 5 * i for i in range(1, compute_control.ETA_WINDOW + 2)]
    assert eta([184.0, *slow, *fast], 4) == pytest.approx(20.0)


def test_rounds_just_started_count_against_the_headroom():
    gb = compute_control.GB
    assert compute_control.room_for_one(10 * gb, 4 * gb, gb, 0)
    assert compute_control.room_for_one(10 * gb, 4 * gb, gb, 5), "6 GB spare: 5 young rounds plus this one"
    assert not compute_control.room_for_one(10 * gb, 4 * gb, gb, 6)
    assert not compute_control.room_for_one(4.5 * gb, 4 * gb, gb, 0)
    assert compute_control.room_for_one(None, 4 * gb, gb, 9), "no reading: no gate"


def test_memory_probes_read_something_on_this_machine():
    free, peak = compute_control.free_memory(), compute_control.peak_memory()
    if sys.platform == "win32":  # the machine that runs it; None there once hid a truncated handle
        assert free and peak and peak > 1_000_000
    assert free is None or free > 0
    assert peak is None or peak > 0


def test_a_result_is_stored_and_a_rerun_replaces_it(factory, db, linked):
    [planned] = rc.plan(db, rounds={1})
    ok = {"status": "ok", "data": gzip.compress(b"d"), "summary": cf.pack_summary({})}
    assert compute_control.store_result(factory, planned, ok) == "stored"
    assert db.get(ReplayRoundControl, (linked.id, 1)).data_version == cf.DATA_VERSION
    failed = {"status": "failed", "error": "ControlError: boom"}
    assert compute_control.store_result(factory, planned, failed) == "stored"
    db.expire_all()
    row = db.get(ReplayRoundControl, (linked.id, 1))
    assert row.status == "failed" and row.data is None and row.error.startswith("ControlError")
    assert row.fingerprint == planned.fingerprint and row.computed_at is not None


def test_a_result_for_a_replay_that_went_away_is_skipped(factory, db, linked):
    from sqlalchemy import text

    [planned] = rc.plan(db, rounds={1})
    store._delete(db, linked)  # a re-ingest on PostgreSQL: the old id's rounds are gone
    db.commit()

    def with_foreign_keys():  # SQLite enforces them only when asked
        session = factory()
        session.execute(text("PRAGMA foreign_keys=ON"))
        return session

    ok = {"status": "ok", "data": gzip.compress(b"d"), "summary": cf.pack_summary({})}
    assert compute_control.store_result(with_foreign_keys, planned, ok).startswith("skipped")


def test_a_remote_result_is_stored_only_while_its_inputs_are_current(factory, db, linked, monkeypatch):
    from app.services.replay_control_store import store_round

    [planned] = rc.plan(db, rounds={1})
    ok = {"status": "ok", "data": gzip.compress(b"d"), "summary": cf.pack_summary({})}
    # the inputs moved while it computed (a new link, new geometry): nothing is stored
    assert store_round(factory, linked.id, 1, "0" * 16, ok, require_current=True).startswith("skipped: its inputs")
    assert db.get(ReplayRoundControl, (linked.id, 1)) is None
    assert store_round(factory, linked.id, 1, planned.fingerprint, ok, require_current=True) == "stored"
    # a second copy (the local command, or a duplicate job) finds the current row there already
    assert store_round(factory, linked.id, 1, planned.fingerprint, ok, require_current=True) == "skipped: already stored"
    assert store_round(factory, linked.id + 99, 1, planned.fingerprint, ok, require_current=True) == \
        "skipped: the replay is gone"


def test_dry_run_lists_and_writes_nothing(factory, db, linked, capsys):
    assert compute_control.main(["--dry-run"], session_factory=factory) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{linked.round_count} round(s) to compute ({linked.round_count} missing)")
    assert f"{MATCH_UUID} r1 {linked.map_name}: missing" in out
    assert db.query(ReplayRoundControl).count() == 0
    assert compute_control.main(["--dry-run", "--brief"], session_factory=factory) == 0
    assert capsys.readouterr().out.startswith("Map control: ")


def test_the_command_refuses_to_write_in_demo_mode(factory, linked, monkeypatch, capsys):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert compute_control.main([], session_factory=factory) == 3


def test_the_height_build_reads_a_maps_rounds_with_heights_and_skips_older_recipes(factory, db, linked):
    # scripts/build_control_heights.py (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 3):
    # rounds of the map at condenser revision 11 or later, by (match uuid, round number); it only reads.
    import build_control_heights

    rounds, skipped = build_control_heights.db_rounds(linked.map_name, factory)
    assert [(match, n) for match, n, _ in rounds] == [(MATCH_UUID, n) for n in range(1, linked.round_count + 1)]
    assert all("z" in seg for _, _, blob in rounds for segs in blob["tracks"].values() for seg in segs)
    assert skipped == {"old_revision": 0}
    assert build_control_heights.db_rounds("Bind", factory) == ([], {"old_revision": 0})
    linked.recipe = linked.recipe.replace(f".c{fmt.CONDENSE_REVISION}.", ".c10.")
    db.commit()
    assert build_control_heights.db_rounds(linked.map_name, factory) == ([], {"old_revision": 1})


def test_a_maps_heights_join_its_geometry_inputs_and_only_its_rounds_go_stale(factory, db, linked, monkeypatch):
    # docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4, "Freshness": a height asset's
    # digest joins the map's geometry inputs, so committing or rebuilding one map's heights makes only that
    # map's rounds stale; a map without heights keeps exactly the inputs (and fingerprints) it had.
    flat = rc.geometry_inputs(linked.map_name)
    assert "height" not in flat
    for n in (1, 2):
        put_row(db, linked, n)
    assert rc.plan(db) == [p for p in rc.plan(db) if p.round_number > 2]
    index, tags, maps = rc._assets()
    monkeypatch.setattr(rc, "_assets", lambda: ({**index, linked.map_name: {**index[linked.map_name],
                                                                            "height_sha": "0123456789ab"}}, tags, maps))
    with_heights = rc.geometry_inputs(linked.map_name)
    assert with_heights == {**flat, "height": "0123456789ab"}
    assert {p.round_number for p in rc.plan(db) if p.reason == "stale"} == {1, 2}
    assert rc.geometry_inputs("Bind") == {k: v for k, v in rc.geometry_inputs("Bind").items() if k != "height"}


def test_compute_control_can_take_one_map(factory, db, linked, capsys):
    assert compute_control.main(["--dry-run", "--map", linked.map_name], session_factory=factory) == 0
    assert f"{MATCH_UUID} r1 {linked.map_name}: missing" in capsys.readouterr().out
    assert compute_control.main(["--dry-run", "--map", "Bind"], session_factory=factory) == 0
    assert MATCH_UUID not in capsys.readouterr().out
