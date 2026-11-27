"""Aim events end to end, offline: a toy blob -> the worker's moments (`compute_task`) -> the site's validation
(`replay_control_remote._trusted_gaps`) -> `store_gaps` (SQLite) -> `replay_aim.current_events`."""

import copy
import gzip
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from control_toys import blob  # noqa: E402
from test_gaps_store import session_factory, stored_round  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed, pg  # noqa: E402,F401  (fixtures)

from app.control.task import compute_task  # noqa: E402
from app.models.replay import ReplayAimEvent  # noqa: E402
from app.replays import choke_assets  # noqa: E402
from app.services import replay_aim, replay_gaps  # noqa: E402
from app.services import replay_control_remote as remote  # noqa: E402
from app.services.replay_gaps_store import store_gaps  # noqa: E402

A_SPOT, B_SPOT = (292, 836), (356, 132)     # test_gaps_task.py's two walkable Ascent points
FP = "f" * 16
FLIGHT_FP = "c" * 16
WEBAPP = HERE.parents[1]


def _task(tmp_path, monkeypatch):
    from app.gaps import cache
    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path)
    data = blob({0: ("A", [(0.0, *A_SPOT, 180)]), 5: ("B", [(0.0, *B_SPOT, 180)])}, t_end=40.0,
                util=[{"k": "damage", "t": 20.0, "t1": 20.0, "by": 0, "target": 5, "src": "gun", "wall": False,
                       "n": 1}],
                deaths={5: 20.5})
    data["kills"] = [{"i": 0, "t": 20.5, "killer": 0, "victim": 5, "u": 0, "v": 0}]
    return {"key": 0, "map": "Ascent", "blob": gzip.compress(json.dumps(data).encode("utf-8")),
            "link": {"sides": {}, "db_deaths": []},
            "gaps": {"replay_id": 1, "round": 1, "fingerprint": FLIGHT_FP}}


def _flight():
    return SimpleNamespace(fingerprint=FLIGHT_FP, map_name="Ascent", asset_key=None)


def _real_events():
    """Real aim events from the moments suite's toy round (an Ascent toy has no clear line to open a fight)."""
    from tests.moments.test_aim import SMOKE_TO_2, events_of, players
    events, *_ = events_of(players(), [SMOKE_TO_2])
    assert events
    return events


def _gaps(tmp_path, monkeypatch):
    """The worker's gaps result, with the keys the site expects of this flight. The Ascent toy's own aim call
    is replaced by the real events above; what is under test is the path they take from there."""
    from app.moments import aim
    events = _real_events()
    monkeypatch.setattr(aim, "round_aim", lambda *a, **k: events)
    gaps = compute_task(_task(tmp_path, monkeypatch))["gaps"]
    gaps["run"]["fingerprint"] = replay_gaps.gap_fingerprint(FLIGHT_FP, "Ascent")
    gaps["run"]["chokes_hash"] = choke_assets.asset_hash("Ascent")
    return gaps


@pytest.fixture(autouse=True)
def fresh_is_fp(monkeypatch):
    monkeypatch.setattr(replay_gaps.Freshness, "expected", lambda self, replay, n: FP)


def test_the_worker_moments_carry_aim_events(tmp_path, monkeypatch):
    moments = _gaps(tmp_path, monkeypatch)["moments"]
    assert moments["status"] == "ok" and moments["aim"] == _real_events()
    assert json.loads(json.dumps(moments)) == moments, "plain JSON"
    assert set(moments) == {"status", "v", "sight", "fights", "aim", "error"}
    assert moments["v"] == 2


def test_round_moments_builds_aim_from_its_own_fights_and_none_without_sight():
    from app.moments import aim, fights
    from tests.moments.test_aim import SMOKE_TO_2, players
    from tests.moments.helpers import damage, kill, round_of
    rnd, data = round_of(players(), kills=[kill(4.0, 0, 5)], util=[SMOKE_TO_2, damage(3.5, 0, 5)], t_end=8.0)
    made = fights.round_moments(rnd, data)
    assert made["aim"] and made["aim"] == aim.round_aim(rnd, data, made["fights"])
    assert set(made) == {"v", "sight", "fights", "aim"}
    assert fights.round_moments(rnd, data, sight=False)["aim"] == []


def test_the_site_accepts_it_and_the_events_come_back_from_the_store(tmp_path, monkeypatch, session_factory,
                                                                    stored_round):
    gaps = _gaps(tmp_path, monkeypatch)
    assert remote._trusted_gaps({"gaps": gaps}, _flight()) is gaps
    events = gaps["moments"]["aim"]
    replay_id, n = stored_round
    run = {**gaps["run"], "fingerprint": FP}
    assert store_gaps(session_factory, replay_id, n, run, [], moments=gaps["moments"], util=gaps["util"],
                      postplant=gaps["postplant"]) == "stored"
    rows = replay_aim.current_events(session_factory())
    assert len(rows) == len(events) > 0
    for row, e in zip(rows, events):
        assert (row.slot, row.fight_i, row.opponent, row.t, row.cls) == (e["slot"], e["i"], e["opponent"], e["t"],
                                                                          e["cls"])
        assert (row.err, row.err_125, row.err_250, row.d_m) == (e["err"], e["err_125"], e["err_250"], e["d_m"])
        assert (row.fine, row.hold, row.opener, row.gun_class) == (e["fine"], e["hold"], e["opener"], e["gun_class"])
        assert (row.hp, row.enemy_hp, row.outcome, row.t_end) == (e["hp"], e["enemy_hp"], e["outcome"], e["t_end"])
        assert (row.on_times, row.shot, row.dmg) == (e["on"], e["shot"], e["dmg"])


def test_an_aim_that_is_not_a_list_is_malformed(tmp_path, monkeypatch):
    gaps = _gaps(tmp_path, monkeypatch)
    bad = copy.deepcopy(gaps)
    bad["moments"]["aim"] = {"slot": 0}
    assert remote._trusted_gaps({"gaps": bad}, _flight()) is None
    assert remote._trusted_gaps({"gaps": gaps}, _flight()) is gaps


def test_a_failed_moments_with_a_stray_aim_is_not_malformed(tmp_path, monkeypatch):
    gaps = _gaps(tmp_path, monkeypatch)
    gaps["moments"] = {"status": "failed", "fights": [], "error": "x"}
    assert remote._trusted_gaps({"gaps": gaps}, _flight()) is gaps


def test_a_result_without_aim_stores_none(tmp_path, monkeypatch, session_factory, stored_round):
    gaps = _gaps(tmp_path, monkeypatch)
    del gaps["moments"]["aim"]
    assert remote._trusted_gaps({"gaps": gaps}, _flight()) is gaps
    replay_id, n = stored_round
    run = {**gaps["run"], "fingerprint": FP}
    assert store_gaps(session_factory, replay_id, n, run, [], moments=gaps["moments"]) == "stored"
    assert session_factory().query(ReplayAimEvent).count() == 0


def test_an_aim_error_leaves_the_fights_and_no_aim(tmp_path, monkeypatch):
    from app.moments import aim
    good = _gaps(tmp_path, monkeypatch)["moments"]
    monkeypatch.setattr(aim, "round_aim", lambda *a, **k: (_ for _ in ()).throw(ValueError("no aim")))
    moments = compute_task(_task(tmp_path, monkeypatch))["gaps"]["moments"]
    assert moments["status"] == "ok" and "aim" not in moments
    assert moments["fights"] == good["fights"]


@pytest.mark.parametrize("first", ["app.moments.fights", "app.moments.peeks"])
def test_either_module_imports_first_in_a_fresh_interpreter(first):
    done = subprocess.run([sys.executable, "-c", f"import {first}"], cwd=str(WEBAPP), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
