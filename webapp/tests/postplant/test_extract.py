"""Post-plant evidence on synthetic replay tracks and control geometry."""

import ast
import json
from pathlib import Path

import pytest

from app.control import engine, stance
from app.postplant import extract
from tests.replays.control_toys import (
    BOX_TOY_RISE_M, TOY_Z0, blob, box_hall_tops, door_hall, height_blob,
    open_hall, three_boxes_tops, tops_hall, toy_ability, track, uv, z_track,
)


PLAYERS = {0: ("A", [(0, 140, 196, 180)]), 5: ("B", [(0, 320, 196, 0)])}
TIMES = [10.0, 10.5, 11.0, 11.5, 12.0]


def bomb(t=10.0, x=212, y=196, by=0):
    return toy_ability("Bomb", "Spike", x, y, by, t=t, t1=20, kind="Bomb")


def round_of(data, geo=None, linked=True):
    sides = {p["slot"]: {"A": "attack", "B": "defense"}[p["side"]] for p in data["players"]}
    link = engine.ControlLink(sides=sides) if linked else None
    return engine.RoundInputs(data, geo or open_hall(), link)


def data_of(**kwargs):
    return blob(PLAYERS, t_end=14, t_decided=12, util=[bomb()], **kwargs)


def result(data, **kwargs):
    return extract.round_postplant(round_of(data, **kwargs), data)


def kill(t, killer=0, victim=5):
    u, v = uv(304, 204)
    return {"i": 0, "t": t, "killer": killer, "victim": victim, "u": u, "v": v}


def test_no_bomb_returns_empty_evidence_with_metadata():
    data = blob(PLAYERS, t_end=14, t_decided=12,
                util=[toy_ability("Bomb", "Spike", 212, 196, 0)])
    data["plant"] = 10  # A legacy field is not the completed-plant evidence.
    data["kills"] = [kill(11)]
    assert result(data) == {
        "v": 1, "plant": None, "end": 12, "step": 0.5,
        "sides": {"0": "attack", "5": "defense"}, "sides_known": True,
        "samples": [], "kills": [],
    }


def test_samples_include_both_ends_and_use_minimap_integers():
    data = data_of()
    out = result(data)
    assert extract.VERSION == out["v"] == 1
    assert extract.STEP_S == out["step"] == 0.5
    assert out["plant"] == {"t": 10, "u": uv(212, 196)[0], "v": uv(212, 196)[1], "by": 0}
    assert len(out["samples"]) == 10
    for slot, x in [(0, 140), (5, 320)]:
        rows = [row for row in out["samples"] if row[1] == slot]
        assert [row[0] for row in rows] == TIMES
        assert all(row[2:5] == [*uv(x, 196), None] for row in rows)
        assert all(type(value) is int for row in rows for value in row[2:4])
        assert all(row[5] == 1 for row in rows)
    json.dumps(out, allow_nan=False)


def test_samples_stop_at_death_and_resume_only_in_a_later_life():
    data = data_of(deaths={5: 11.2})
    assert [r[0] for r in result(data)["samples"] if r[1] == 5] == TIMES[:3]
    data["alive"]["5"].append([12, None, None])
    assert [r[0] for r in result(data)["samples"] if r[1] == 5] == [*TIMES[:3], 12]


def test_unknown_positions_are_not_sampled():
    data = data_of()
    data["tracks"]["5"] = track([(11, 320, 196, 0)], t0=11, t1=14)
    assert [r[0] for r in result(data)["samples"] if r[1] == 5] == TIMES[2:]


@pytest.mark.parametrize("plant_t", [12, 13])
def test_plant_at_or_after_decision_is_kept_without_evidence(plant_t):
    data = data_of()
    data["util"] = [bomb(plant_t)]
    data["kills"] = [kill(plant_t)]
    out = result(data)
    assert out["plant"]["t"] == plant_t
    assert out["samples"] == out["kills"] == []


def test_first_bomb_row_wins_and_fractional_window_stays_on_plant_grid():
    data = data_of()
    data["util"] = [bomb(10.13), bomb(9)]
    data["t_decided"] = 11.14
    out = result(data)
    assert out["plant"]["t"] == 10.13
    assert [r[0] for r in out["samples"] if r[1] == 0] == [10.13, 10.63, 11.13]


@pytest.mark.parametrize("missing", [False, True])
def test_missing_decision_uses_round_end(missing):
    data = data_of()
    if missing:
        del data["t_decided"]
    else:
        data["t_decided"] = None
    out = result(data)
    assert out["end"] == 14
    assert out["samples"][-1][0] == 14


def test_world_z_without_height_assets_and_missing_z_segment():
    data = data_of()
    data["tracks"]["0"] = z_track([(0, 140, 196, 180, 123.4 - TOY_Z0 / 10)], t1=14)
    rnd = round_of(data)
    assert rnd.geo.heights is None and rnd.height(0, 10) is None
    out = extract.round_postplant(rnd, data)
    assert {r[4] for r in out["samples"] if r[1] == 0} == {1234}
    assert {r[4] for r in out["samples"] if r[1] == 5} == {None}
    data["tracks"]["0"] = (
        z_track([(0, 140, 196, 180, 123.4 - TOY_Z0 / 10)], t1=10.5)
        + track([(11, 140, 196, 180)], t0=11, t1=14)
    )
    assert [r[4] for r in result(data)["samples"] if r[1] == 0] == [1234, 1234, None, None, None]


def test_facing_away_does_not_block_a_ten_metre_spike_line():
    data = data_of()
    rnd = round_of(data)
    assert 9.9 < (212 - 140) * rnd.geo.m_per_px < 10.2
    assert rnd.pos(0, 10)[2] == 180
    assert extract.spike_line(rnd, 0, 10, *[v * engine.PX / 10000 for v in uv(212, 196)])
    assert result(data)["samples"][0][5] == 1


def test_full_wall_blocks_the_spike_line():
    assert {r[5] for r in result(data_of(), geo=door_hall())["samples"] if r[1] == 0} == {0}


def test_smoke_blocks_only_while_it_is_up():
    data = data_of()
    data["util"].append(toy_ability("Wraith", "4_Smoke", 180, 196, 5,
                                    t=10, t1=11, kind="Smoke"))
    rnd = round_of(data)
    assert rnd.smokes_at(10) and not rnd.smokes_at(11.5)
    rows = [r for r in extract.round_postplant(rnd, data)["samples"] if r[1] == 0]
    assert [r[5] for r in rows] == [0, 0, 1, 1, 1]


@pytest.mark.parametrize("kind", ["flash", "nearsight"])
def test_flash_and_nearsight_do_not_block_spike_line(kind):
    data = data_of()
    data["util"].append({"k": kind, "t": 9.9, "by": 5, "u": 0, "v": 0,
                         "name": "Blind", "hits": [[0, 10.0, 3.0]]})
    rnd = round_of(data)
    spans = rnd.flashed if kind == "flash" else rnd.nearsight
    assert any(a <= 10 < b for a, b in spans[0])
    assert all(r[5] == 1 for r in extract.round_postplant(rnd, data)["samples"] if r[1] == 0)


def test_kill_has_sides_pre_death_counts_and_both_positions():
    data = data_of(deaths={5: 11})
    data["kills"] = [kill(11)]
    data["tracks"]["0"] = z_track([(0, 140, 196, 180, 2)], t1=14)
    data["tracks"]["5"] = z_track([(0, 320, 196, 0, 3)], t1=14)
    rnd = round_of(data)
    [row] = extract.round_postplant(rnd, data)["kills"]
    assert row == {
        "t": 11, "killer": 0, "victim": 5, "k_side": "attack", "v_side": "defense",
        "before": [1, 1], "ku": uv(140, 196)[0], "kv": uv(140, 196)[1], "kz": TOY_Z0 + 20,
        "vu": uv(304, 204)[0], "vv": uv(304, 204)[1], "vz": TOY_Z0 + 30,
        "k_line": extract.spike_line(rnd, 0, 11, *[v * engine.PX / 10000 for v in uv(212, 196)]),
    }


def test_kill_window_includes_endpoints_and_excludes_outside_kills():
    data = data_of()
    data["kills"] = [kill(t) for t in [9.99, 10, 11, 12, 12.01]]
    assert [k["t"] for k in result(data)["kills"]] == [10, 11, 12]


def test_kill_unknown_track_positions_and_heights_stay_null():
    data = data_of()
    data["kills"] = [kill(11)]
    del data["tracks"]["0"]
    [row] = result(data)["kills"]
    assert [row[k] for k in ("ku", "kv", "kz", "vz", "k_line")] == [None] * 5
    assert [row["vu"], row["vv"]] == list(uv(304, 204))


def test_unlinked_sides_keep_team_letters_and_evidence():
    data = data_of()
    data["kills"] = [kill(11)]
    out = result(data, linked=False)
    assert out["sides"] == {"0": "A", "5": "B"}
    assert out["sides_known"] is False
    assert len(out["samples"]) == 10
    assert out["kills"][0]["before"] == [0, 0]
    assert out["kills"][0]["k_side"] == "A"


@pytest.mark.slow
@pytest.mark.parametrize("three", [False, True], ids=["own-box", "three-boxes"])
def test_box_eye_sees_over_low_cover_while_floor_holder_is_blocked(three):
    geo = three_boxes_tops() if three else box_hall_tops()
    x, y = (156, 205) if three else (260, 196)
    for z, expected in [(BOX_TOY_RISE_M, True), (0, False)]:
        data = height_blob({0: ("A", [(0, x, y, 180, z)])}, t_end=14, util=[bomb(x=290, y=y)])
        rnd = round_of(data, geo)
        assert bool(extract.spike_line(rnd, 0, 10, 290, y)) is expected


@pytest.mark.slow
def test_spike_on_top_piece_is_seen_over_that_piece():
    from tests.replays.test_top_nodes import sight_topped

    # Coarse box_hall_tops has no top nodes; the adaptive toy supplies real top leaves.
    geo = sight_topped(((202, 202, 10),))
    data = blob({0: ("A", [(0, 196, 204, 180)])}, t_end=14)
    rnd = round_of(data, geo)
    assert len(geo.top_nodes)
    boxed = stance.boxed_stance(rnd, 0, 10)
    assert boxed is None or boxed.see_over == ()
    assert not engine.los(geo, (196, 204, None), (212, 204, None), [])
    assert extract.spike_line(rnd, 0, 10, 212, 204)


@pytest.mark.slow
def test_box_eye_cannot_see_through_full_wall():
    geo = tops_hall("PostplantWall", [(144, 184, 168, 208)],
                    walls=[(200, 96, 216, 288)], rises=[BOX_TOY_RISE_M])
    data = height_blob({0: ("A", [(0, 156, 196, 0, BOX_TOY_RISE_M)])}, t_end=14)
    rnd = round_of(data, geo)
    assert stance.boxed_stance(rnd, 0, 10).see_over
    assert not extract.spike_line(rnd, 0, 10, 290, 196)


def test_worker_extractor_has_no_database_or_service_imports():
    tree = ast.parse(Path(extract.__file__).read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
            imports.extend(f"{node.module}.{alias.name}" for alias in node.names)
    forbidden = ("app.services", "app.models", "app.db")
    assert not [name for name in imports if any(name == p or name.startswith(p + ".") for p in forbidden)]
