"""A suppressed Cypher's devices and an observed Leer (W13; the 2026-10-05 review items 5, 29)."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.control import engine as ce
from app.control import utility as ut
from tests.replays.control_toys import blob, door_hall, open_hall, toy_ability

REPLAY_JS = Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "replay.js"
NODE = shutil.which("node")


def trip(by=1, t=0.0, t1=20.0):
    return toy_ability("Gumshoe", "4_TripWire", 250, 110, by, t=t, t1=t1, kind="GameObject", end=[2441, 2832])


def suppressed(target, t, t1, by=5):
    return {"k": "status", "t": t, "t1": t1, "by": by, "target": target, "status": "suppressed",
            "code": "Grenadier", "name": "E_SuppressionPulse", "from": "object"}


def cypher_round(util, t_end=20.0):
    b = blob({0: ("A", [(0.0, 104, 200, 180)]), 1: ("A", [(0.0, 110, 120, 180)]), 5: ("B", [(0.0, 400, 200, 0)])},
             t_end=t_end, util=util)
    next(p for p in b["players"] if p["slot"] == 1)["agent"] = "Cypher"
    return b


def test_a_suppressed_cyphers_trip_is_off_and_back_on_after():
    rnd = ce.RoundInputs(cypher_round([trip(), suppressed(1, 5.0, 13.0)]), open_hall())
    [w] = [w for w in rnd.watchers if w.kind == "trip"]
    assert w.off == [(5.0, 13.0)]
    assert ce._watching(w, 4.9) and not ce._watching(w, 5.0) and not ce._watching(w, 12.9) and ce._watching(w, 13.0)


def test_a_trip_that_ends_while_he_is_suppressed_doesnt_come_back():
    rnd = ce.RoundInputs(cypher_round([trip(t1=9.0), suppressed(1, 5.0, 13.0)]), open_hall())
    [w] = [w for w in rnd.watchers if w.kind == "trip"]
    assert w.off == [(5.0, 9.0)] and not ce._watching(w, 10.0)


def test_suppression_of_someone_else_or_after_his_death_leaves_it_alone():
    rnd = ce.RoundInputs(cypher_round([trip(), suppressed(0, 5.0, 13.0)]), open_hall())
    assert [w.off for w in rnd.watchers if w.kind == "trip"] == [[]]


def test_two_suppressions_are_two_off_spans_and_his_own_sight_stays():
    b = cypher_round([trip(), suppressed(1, 2.0, 4.0), suppressed(1, 8.0, 10.0)])
    rnd = ce.RoundInputs(b, open_hall())
    [w] = [w for w in rnd.watchers if w.kind == "trip"]
    assert w.off == [(2.0, 4.0), (8.0, 10.0)]
    tick = ce.Tick(rnd, 3.0)
    assert tick.holders[1].active.sum() + tick.holders[1].passive.sum() > 10
    assert not tick.holders[1].watch.any(), "the trip watches nothing while he is suppressed"
    assert ce.Tick(rnd, 5.0).holders[1].watch.any()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_viewer_shows_the_same_off_state():
    script = """
      const R = require(process.argv[1]);
      const statuses = [{t0: 5, t1: 13, target: 1, status: "suppressed"}, {t0: 5, t1: 13, target: 0, status: "concussed"}];
      const wire = {code: "Gumshoe", name: "4_TripWire", slot: 1}, cam = {code: "Gumshoe", name: "E_PossessableCamera", slot: 1};
      const turret = {code: "Killjoy", name: "E_Turret", slot: 1}, other = {code: "Gumshoe", name: "4_TripWire", slot: 0};
      process.stdout.write(JSON.stringify([4, 5, 12, 13.5].map(t => [R.utilSuppressedAt(wire, statuses, t),
        R.utilSuppressedAt(cam, statuses, t), R.utilSuppressedAt(turret, statuses, t), R.utilSuppressedAt(other, statuses, t)])));"""
    done = subprocess.run([NODE, "-e", script, str(REPLAY_JS)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [[False, False, False, False], [True, True, False, False],
                                       [True, True, False, False], [False, False, False, False]]


def eye(x, y, t=10.0, t1=12.0, by=5):
    return toy_ability("Vampire", "4_NearsightAOE_Source", x, y, by, t=t, t1=t1, kind="GameObject")


def leer_round(a_track, util, t_end=20.0, more=None):
    players = {0: ("A", a_track), 5: ("B", [(0.0, 400, 280, 0)]), 6: ("B", [(0.0, 390, 120, 0)])}
    players.update(more or {})
    b = blob(players, t_end=t_end, util=util)
    next(p for p in b["players"] if p["slot"] == 5)["agent"] = "Reyna"
    return b


def test_a_seen_eye_restricts_reynas_region_only_to_its_placement_disk():
    b = leer_round([(0.0, 200, 200, 0)], [eye(300, 200)])             # A looks east, straight at the eye
    [info] = ce.RoundInputs(b, open_hall()).infos
    assert (info.t, info.slot, info.kind, info.reason) == (10.0, 5, "restrict", "leer_seen")
    assert info.radius_m == ut.FIGURES["leer_cast_range_m"] == 10.0 and info.mask is None


def test_an_eye_nobody_saw_tells_nothing():
    b = leer_round([(0.0, 200, 200, 180)], [eye(300, 200)])           # A faces away the whole time
    assert ce.RoundInputs(b, open_hall()).infos == []


def test_a_later_look_is_when_the_team_learns_it():
    b = leer_round([(0.0, 200, 200, 180), (11.0, 200, 200, 0)], [eye(300, 200)])
    [info] = ce.RoundInputs(b, open_hall()).infos
    assert info.t == pytest.approx(11.0)


def test_a_nearsight_hit_is_seeing_it():
    hit = {"k": "nearsight", "t": 9.5, "by": 5, "ability": "reyna_leer", "targets": [0], "hits": [[0, 10.4, None]]}
    b = leer_round([(0.0, 200, 200, 180)], [eye(300, 200), hit])
    [info] = ce.RoundInputs(b, open_hall()).infos
    assert info.t == pytest.approx(10.4)


def test_an_eye_behind_a_wall_is_not_seen_but_its_disk_reaches_through_walls():
    geo = door_hall()
    b = leer_round([(0.0, 150, 150, 0)], [eye(230, 150)])             # the wall x 200-216 is between them
    assert ce.RoundInputs(b, geo).infos == []
    b = leer_round([(0.0, 260, 150, 0), (10.5, 260, 150, 180)], [eye(230, 150)])
    [info] = ce.RoundInputs(b, geo).infos
    disk = ut.footprint(info, geo)
    x = geo.centres[:, 0]
    assert (disk & (x < 200)).any() and (disk & (x > 216)).any(), "candidates on both sides of the wall"


def test_after_the_restriction_her_region_grows_again():
    geo = door_hall()
    # A, east of the wall, looks west at the eye; Reyna is behind the wall within 10 m of it; B6 is behind A
    b = leer_round([(0.0, 260, 150, 180)], [eye(230, 150)],
                   more={5: ("B", [(0.0, 170, 150, 0)]), 6: ("B", [(0.0, 400, 280, 0)])})
    out = {}

    class Watch:
        def on_tick(self, tick, unknown):
            out[round(tick.t, 6)] = {s: np.isfinite(r) for s, r in unknown.reached["A"].items()}

    ce.compute_round(b, geo, knowledge=False, observer=Watch())
    disk = ut.footprint(ce.RoundInputs(b, geo).infos[0], geo)
    p = ce.RoundInputs(b, geo).pos(5, 10.0)
    own = geo.node_at(geo.cell_of_px(p[0], p[1]), None)
    assert not (out[10.0][5] & ~disk & (np.arange(geo.n) != own)).any()
    assert out[14.0][5].sum() > out[10.0][5].sum()
    assert out[10.0][6].sum() == pytest.approx(out[9.5][6].sum(), rel=0.2), "B6's region is not restricted"
