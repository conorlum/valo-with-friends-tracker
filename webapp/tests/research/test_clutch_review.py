"""Offline CLI contracts: cache-only workers, exhaustive coverage and private output."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import clutch_review as review

from app.control import geometry
from app.services import util_model
from tests.replays import control_toys as toys
from tests.research.test_clutch_control import payload

UUIDS = [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 5)]


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected network access")
    monkeypatch.setattr(review.probe, "_urlopen", forbidden)
    monkeypatch.setenv("CONTROL_TOY_CACHE_DIR", str(tmp_path / "toys"))
    monkeypatch.setattr(geometry, "load_geometry", lambda name: toys.open_hall())
    monkeypatch.setattr(geometry, "visibility", lambda geo, directory: geo)


def context(uuid=UUIDS[0]):
    return {
        "match": {"uuid": uuid, "map": "Toy", "asset": "Toy", "match_id": 42,
                  "rounds": [1], "linked": True, "clock_offset": 2,
                  "side_to_team": {"A": "team-1", "B": "team-2"}},
        "players": {
            "0": {"player_id": 101, "name": "A", "agent": "Jett", "side": "A", "team": "team-1"},
            "7": {"player_id": 107, "name": "InventedStranger#B", "agent": "Sage", "side": "B", "team": "team-2"},
        },
        "rounds": {"1": {"db": {"winner": "team-1", "plant": None, "defuse": None},
                         "alive_steps": [[10, 1, 1], [20, 2, 2]],
                         "db_deaths": [], "kills": [],
                         "stats": "{'0': {'loadout': 3900}, '7': {'loadout': 3900}}"}},
    }


def blob():
    value = toys.blob({0: ("A", [(0, 180, 204, 0)]),
                       7: ("B", [(0, 300, 204, 180)])}, t_end=22, t_decided=20)
    value["player_state"] = {"charges": {"0": {"Q": [{"t": 0, "n": 2}],
                                                          "E": [{"t": 0, "n": 1}]}}}
    return value


def put(cache, endpoint, value, status=200):
    path = review.probe._cache_path(review.probe.SITE + endpoint, cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.with_suffix(".status").write_text(str(status), encoding="ascii")


def seed(cache, ctx=None, blobs=None, controls=None):
    ctx = ctx or context()
    uuid = ctx["match"]["uuid"]
    page = '<script id="replay-data" type="application/json">' + json.dumps(ctx) + '</script>'
    put(cache, f"/replays/{uuid}", page.encode())
    for n, value in (blobs or {1: blob()}).items():
        put(cache, f"/replays/{uuid}/{n}.json", json.dumps(value).encode() if isinstance(value, dict) else value)
    for n, value in (controls or {1: payload()}).items():
        put(cache, f"/replays/{uuid}/{n}/control.bin", b"absent" if isinstance(value, int) else value,
            value if isinstance(value, int) else 200)


def arguments(tmp_path, uuids=(UUIDS[0],)):
    friends = tmp_path / "friends.json"
    friends.write_text('["A"]', encoding="utf-8")
    return ["run", "--uuids", *uuids, "--jobs", "1", "--out", str(tmp_path / "out"),
            "--cache", str(tmp_path / "cache"), "--geometry-cache", str(tmp_path / "geo"),
            "--friends", str(friends)]


def jsonlines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_measures_report_and_privacy(tmp_path):
    seed(tmp_path / "cache")
    assert review.main(arguments(tmp_path)) == 0
    out = tmp_path / "out"
    rows = jsonlines(out / "rows.jsonl")
    assert len(rows) == 2
    own = next(r for r in rows if r["slot"] == 0)
    assert own["label"] == "A"
    assert own["isolation"]["one_at_a_time"] == 1
    assert own["clock"]["time_left_start"] == 90
    assert own["clock"]["time_left_first_engagement"] == 90
    assert own["unknown"]["unknown_share_start"] == 1
    assert own["util"]["held_start"] == 3
    assert own["state"]["team_loadouts"] == {"team-1": 3900, "team-2": 3900}
    assert own["enemy_agents"] == {"7": "Sage"}
    assert next(r for r in rows if r["slot"] == 7)["label"] == "player 1"
    cutoffs = jsonlines(out / "cutoffs.jsonl")
    assert len(cutoffs) == 2
    assert all(r["round"] == 1 and r["uuid"] == UUIDS[0] and r["n_enemies"] == 2 for r in cutoffs)
    for filename in ("rows.jsonl", "coverage.json", "report.md", "clutches.csv"):
        text = (out / filename).read_text(encoding="utf-8")
        assert "InventedStranger" not in text
        assert "player_id" not in text


@pytest.mark.parametrize("death_source", ["replay", "db"])
def test_run_examples_only_name_living_enemies(tmp_path, death_source):
    ctx = context()
    ctx["players"]["6"] = {**ctx["players"]["7"], "agent": "Omen"}
    ctx["players"]["5"] = {**ctx["players"]["7"], "agent": "Cypher"}
    ctx["rounds"]["1"]["alive_steps"] = [[10, 1, 2]]
    if death_source == "db":
        ctx["rounds"]["1"]["db_deaths"] = [{"slot": 5, "t_db": 7}]
    value = toys.blob({0: ("A", [(0, 180, 204, 0)]),
                       5: ("B", [(0, 300, 236, 180)]),
                       6: ("B", [(0, 300, 220, 180)]),
                       7: ("B", [(0, 300, 204, 180)])},
                      t_end=22, t_decided=20,
                      deaths={5: 5} if death_source == "replay" else {})
    seed(tmp_path / "cache", ctx, {1: value})
    assert review.main(arguments(tmp_path)) == 0
    own = next(r for r in jsonlines(tmp_path / "out" / "rows.jsonl") if r["slot"] == 0)
    assert own["group"] == "1v2" and own["n_enemies"] == 2
    assert own["enemy_agents"] == {"6": "Omen", "7": "Sage"}
    text = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "the enemy Cypher" not in text


def test_export_command_documents_required_database():
    assert ("python scripts/with_friends_db.py --expect-database valowithfriendsdb "
            "--read-only scripts/clutch_review.py export-win") in review.__doc__


def test_two_player_situations(tmp_path):
    ctx = context()
    ctx["players"]["1"] = {**ctx["players"]["0"], "name": "C", "player_id": 102}
    ctx["players"]["6"] = {**ctx["players"]["7"], "name": "D", "player_id": 106}
    ctx["rounds"]["1"]["alive_steps"] = [[10, 2, 2]]
    value = toys.blob({0: ("A", [(0, 180, 204, 0)]), 1: ("A", [(0, 180, 220, 0)]),
                       6: ("B", [(0, 300, 220, 180)]), 7: ("B", [(0, 300, 204, 180)])},
                      t_end=22, t_decided=20)
    seed(tmp_path / "cache", ctx, {1: value})
    assert review.main(arguments(tmp_path)) == 0
    rows = jsonlines(tmp_path / "out" / "rows.jsonl")
    assert len(rows) == 4 and {r["group"] for r in rows} == {"2v2"}
    assert len({r["situation_id"] for r in rows}) == 2


def test_exhaustive_coverage_and_safe_decode_error(tmp_path, capsys):
    ctx = context()
    base = ctx["rounds"]["1"]
    ctx["match"]["rounds"] = list(range(1, 6))
    ctx["rounds"] = {str(n): copy.deepcopy(base) for n in range(1, 6)}
    ctx["rounds"]["2"]["db"]["winner"] = None
    ctx["rounds"]["3"]["alive_steps"] = [[0, 5, 5]]
    seed(tmp_path / "cache", ctx, {n: blob() for n in range(1, 6)},
         {1: payload(), 4: payload(unknown=False), 5: payload()})
    put(tmp_path / "cache", f"/replays/{UUIDS[0]}/5.json", b'{"InventedStranger#B": broken}')
    unlinked = {"match": {"uuid": UUIDS[1], "rounds": [1, 2], "linked": False}, "players": {}, "rounds": {}}
    seed(tmp_path / "cache", unlinked)
    assert review.main(arguments(tmp_path, UUIDS[:2])) == 1
    cov = json.loads((tmp_path / "out" / "coverage.json").read_text())
    assert cov["replays"] == {"discovered": 2, "measured": 0, "skipped_unlinked": 1, "failed": 1}
    assert cov["rounds"] == {"discovered": 7, "measured": 2, "no_situation": 1,
                             "skipped_unlinked": 2, "skipped_no_winner": 1, "failed": 1}
    assert cov["without_unknown_streams"] == 1
    records = cov["round_status"]
    assert len(records) == len({(r["uuid"], r["round"]) for r in records}) == 7
    assert cov["rounds"]["discovered"] == sum(v for k, v in cov["rounds"].items() if k != "discovered")
    assert next(r for r in records if r["status"] == "failed")["error"] == "JSONDecodeError"
    assert next(r for r in cov["replay_status"] if r["status"] == "failed")["error"] == "JSONDecodeError"
    assert "InventedStranger" not in capsys.readouterr().err


@pytest.mark.parametrize("endpoint", ["/replays", f"/replays/{UUIDS[0]}"])
def test_page_and_discovery_failure(tmp_path, endpoint):
    put(tmp_path / "cache", endpoint, b"unavailable", 403)
    argv = arguments(tmp_path)
    if endpoint == "/replays":
        del argv[1:3]
    assert review.main(argv) == 1
    cov = json.loads((tmp_path / "out" / "coverage.json").read_text())
    assert cov["replays"]["failed"] == 1
    assert cov["replays"]["discovered"] == 1
    assert cov["rounds"]["discovered"] == 0


@pytest.mark.parametrize("status,exit_code", [(404, 0), (403, 1), (503, 1)])
def test_control_errors_only_404_is_missing(tmp_path, status, exit_code):
    seed(tmp_path / "cache", controls={1: status})
    # A retryable error is supplied by the fetch boundary, since fetch retries old 503s.
    original = review.probe.fetch
    def fetch(url, cache):
        if status == 503 and url.endswith("control.bin"):
            raise review.probe.FetchError(status)
        return original(url, cache)
    review.probe.fetch = fetch
    try:
        assert review.main(arguments(tmp_path)) == exit_code
    finally:
        review.probe.fetch = original
    cov = json.loads((tmp_path / "out" / "coverage.json").read_text())
    assert cov["rounds"]["failed"] == exit_code
    assert cov["without_unknown_streams"] == (status == 404)
    if status == 404:
        rows = jsonlines(tmp_path / "out" / "rows.jsonl")
        assert all(r["unknown"]["unknown_share_start"] is None for r in rows)
        assert all(r["information"]["located_share"] is None for r in rows)


def test_parent_fetches_once_before_cache_only_worker(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    seed(cache)
    calls = []
    original = review.probe.fetch
    worker_started = False
    def fetch(url, directory):
        assert not worker_started
        calls.append((url, threading.current_thread().name))
        return original(url, directory)
    compute = review.compute_match
    def worker(task):
        nonlocal worker_started
        worker_started = True
        return compute(task)
    monkeypatch.setattr(review.probe, "fetch", fetch)
    monkeypatch.setattr(review, "compute_match", worker)
    assert review.main(arguments(tmp_path)) == 0
    assert len(calls) == 3
    assert all(name != "MainThread" for _, name in calls)


def stub_export(monkeypatch):
    model = {**util_model.fit([(1, 1, False, 12, None, True)]), "excluded": [42]}
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setitem(sys.modules, "app.db", types.SimpleNamespace(SessionLocal=Session))
    monkeypatch.setattr(util_model, "load", lambda db: model)
    monkeypatch.setattr(util_model, "replay_match_ids", lambda db: {42})
    return model


def test_export_then_report_preserves_inputs(tmp_path, monkeypatch, capsys):
    seed(tmp_path / "cache")
    assert review.main(arguments(tmp_path)) == 0
    out = tmp_path / "out"
    before = {name: (out / name).read_bytes() for name in ("coverage.json", "cutoffs.jsonl", "rows.jsonl")}
    model = stub_export(monkeypatch)
    assert review.main(["export-win", "--out", str(out)]) == 0
    assert json.loads((out / "win_model.json").read_text()) == model
    output = capsys.readouterr().out
    assert "version=1" in output and "n=1" in output and "cells=3" in output
    assert "every replay match excluded: True" in output
    assert review.main(["report", "--out", str(out)]) == 0
    assert all((out / name).read_bytes() == value for name, value in before.items())
    assert "Residual difference" in (out / "report.md").read_text(encoding="utf-8")
    assert review.main(arguments(tmp_path)) == 0
    assert all(r["p_stored"] is not None for r in jsonlines(out / "rows.jsonl"))


@pytest.mark.parametrize("option", ["--out", "--cache", "--geometry-cache"])
def test_writable_paths_outside_temp_refused(tmp_path, option):
    outside = Path(__file__).resolve().parents[2] / "clutch-forbidden-output"
    argv = arguments(tmp_path)
    argv[argv.index(option) + 1] = str(outside)
    with pytest.raises(SystemExit) as exc:
        review.main(argv)
    assert exc.value.code == 2
    assert not outside.exists() and not (tmp_path / "out").exists()


def test_default_repo_roster_is_valid_input(tmp_path):
    put(tmp_path / "cache", "/replays", b"")
    argv = arguments(tmp_path)
    del argv[1:3]
    del argv[-2:]
    assert review.main(argv) == 0


def test_db_free_import_run_and_report_in_fresh_process(tmp_path):
    seed(tmp_path / "cache")
    argv = arguments(tmp_path)
    code = r'''
import importlib.abc, json, socket, sys
class NoDB(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname in {"app.db", "app.models", "app.scoring.plant_window", "preview_control_live"}:
            raise AssertionError("forbidden import: " + fullname)
sys.meta_path.insert(0, NoDB())
def forbidden(*a, **k):
    raise AssertionError("network forbidden")
socket.create_connection = forbidden
sys.path.insert(0, "scripts")
import clutch_review as review
review.probe._urlopen = forbidden
from app.control import geometry
from tests.replays import control_toys as toys
geometry.load_geometry = lambda name: toys.open_hall()
geometry.visibility = lambda geo, directory: geo
argv = json.loads(sys.argv[1])
assert review.main(argv) == 0
assert review.main(["report", "--out", argv[argv.index("--out") + 1]]) == 0
assert "app.db" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", code, json.dumps(argv)], cwd=Path(__file__).resolve().parents[2],
                            env=os.environ.copy(), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
