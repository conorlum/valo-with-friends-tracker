"""The status interval normalizer (app/replays/status_intervals.py; the replay player-state plan P02 step 3):
flash, nearsight and status rows become per-slot `[t0, t1)` conditions. Synthetic, anonymized rows with
times between 1/16 s samples."""

import pytest

from app.control import engine as ce
from app.replays import status_intervals as si

ALIVE = {str(s): [[0.0, None, None]] for s in range(10)}


def _flash(hits, by=8, ability="skye_guiding_light", t=17.398, **extra):
    return {"k": "flash", "t": t, "by": by, "ability": ability, "targets": [h[0] for h in hits], "hits": hits,
            **extra}


def _only(conds, slot):
    assert list(conds) == [slot] or slot in conds
    return conds[slot]


def test_a_positive_duration_is_a_confirmed_end_exclusive_interval():
    conds = si.normalize_conditions([_flash([[5, 18.626, 1.615]])], ALIVE, 100.0)
    [c] = conds[5]
    assert (c["c"], c["t0"], c["t1"], c["est"]) == ("blinded", 18.626, 18.626 + 1.615, False)
    assert si.conditions_at(conds, 5, 18.625) == []
    assert [x["c"] for x in si.conditions_at(conds, 5, 18.626)] == ["blinded"]
    assert si.conditions_at(conds, 5, 18.626 + 1.615) == [], "end-exclusive"
    assert c["src"] == [{"k": "flash", "by": 8, "ability": "skye_guiding_light", "t0": 18.626,
                         "t1": 18.626 + 1.615, "est": False}]


def test_a_zero_duration_gives_no_interval():
    conds = si.normalize_conditions([_flash([[7, 18.634, 0.0], [6, 18.634, -1.0]])], ALIVE, 100.0)
    assert conds == {}


def test_a_null_duration_uses_the_shared_fallback_marked_estimated():
    conds = si.normalize_conditions([_flash([[7, 18.634, None]])], ALIVE, 100.0)
    [c] = conds[7]
    assert c["est"] is True and c["t1"] == 18.634 + si.POLICY["flash_full_s"]["skye"]
    near = si.normalize_conditions([{"k": "nearsight", "t": 9.9, "by": 1, "ability": "reyna_leer",
                                     "targets": [7], "hits": [[7, 9.928, None]]}], ALIVE, 100.0)
    [n] = near[7]
    assert (n["c"], n["est"], n["t1"]) == ("nearsighted", True, 9.928 + si.POLICY["nearsight_default_s"])


def test_a_legacy_row_without_hits_is_estimated_from_its_pop():
    pop = {"k": "ability", "t": 18.323, "by": 8, "code": "Guide", "name": "Q", "thrown": {"t0": 17.398}}
    legacy = {"k": "flash", "t": 17.398, "by": 8, "ability": "skye_guiding_light", "targets": [5]}
    conds = si.normalize_conditions([legacy, pop], ALIVE, 100.0)
    assert si.normalize_conditions([legacy | {"hits": None}, pop], ALIVE, 100.0) == conds
    [c] = conds[5]
    assert c["est"] is True and c["t0"] == 18.323 and c["t1"] == 18.323 + si.POLICY["flash_full_s"]["skye"]
    unpopped = si.normalize_conditions([{"k": "flash", "t": 4.0625, "by": 2, "ability": "breach_flashpoint",
                                         "targets": [6]}], ALIVE, 100.0)
    [u] = unpopped[6]
    assert u["t0"] == 4.0625 + si.POLICY["flash_fuse_s"]


def test_overlapping_same_condition_intervals_merge_keeping_every_source():
    rows = [_flash([[5, 18.626, 1.615]]), _flash([[5, 19.5625, 2.0]], by=6, ability="yoru_blindside", t=19.0),
            _flash([[5, 30.0, 1.0]], by=6, ability="yoru_blindside", t=29.5)]
    conds = si.normalize_conditions(rows, ALIVE, 100.0)
    first, second = conds[5]
    assert (first["t0"], first["t1"], first["est"]) == (18.626, 21.5625, False)
    assert [s["by"] for s in first["src"]] == [8, 6]
    assert (second["t0"], second["t1"]) == (30.0, 31.0) and len(second["src"]) == 1


def test_touching_same_condition_intervals_merge():
    rows = [{"k": "status", "t": 12.0625, "t1": 14.5, "by": 3, "target": 1, "status": "slowed"},
            {"k": "status", "t": 14.5, "t1": 15.0, "by": 4, "target": 1, "status": "slowed"}]
    [c] = si.normalize_conditions(rows, ALIVE, 100.0)[1]
    assert (c["t0"], c["t1"], len(c["src"])) == (12.0625, 15.0, 2)


def test_a_legacy_flash_pops_only_at_its_own_throwers_ability_row():
    other = {"k": "ability", "t": 18.323, "by": 9, "code": "Guide", "name": "Q", "thrown": {"t0": 17.398}}
    legacy = {"k": "flash", "t": 17.398, "by": 8, "ability": "skye_guiding_light", "targets": [5]}
    [c] = si.normalize_conditions([other, legacy], ALIVE, 100.0)[5]
    assert c["t0"] == 17.398 + si.POLICY["flash_fuse_s"], "another player's throw at the same instant isn't it"


def test_different_conditions_do_not_merge():
    rows = [_flash([[5, 18.626, 1.615]]),
            {"k": "status", "t": 18.7, "t1": 20.0, "by": 1, "target": 5, "code": "Sarge", "name": "X",
             "status": "concussed", "from": "object"},
            {"k": "nearsight", "t": 18.0, "by": 1, "ability": "omen_paranoia", "targets": [5],
             "hits": [[5, 18.6875, 2.0]]}]
    conds = si.normalize_conditions(rows, ALIVE, 100.0)
    assert sorted(c["c"] for c in conds[5]) == ["blinded", "concussed", "nearsighted"]
    assert [c["c"] for c in si.conditions_at(conds, 5, 19.0)] == ["blinded", "nearsighted", "concussed"]


def test_a_merge_with_an_estimated_part_is_estimated():
    rows = [_flash([[5, 18.626, 1.615]]), _flash([[5, 19.0, None]], by=6, ability="yoru_blindside")]
    [c] = si.normalize_conditions(rows, ALIVE, 100.0)[5]
    assert c["est"] is True and [s["est"] for s in c["src"]] == [False, True]


def test_status_rows_use_their_own_end_and_zero_length_gives_none():
    rows = [{"k": "status", "t": 12.0625, "t1": 14.5, "by": 3, "target": 1, "code": "Sarge", "name": "X",
             "status": "slowed", "from": "object"},
            {"k": "status", "t": 20.0, "t1": 20.0, "by": 3, "target": 1, "status": "slowed"},
            {"k": "status", "t": 30.0, "by": 3, "target": 1, "status": "hindered"}]
    conds = si.normalize_conditions(rows, ALIVE, 100.0)
    slowed, hindered = conds[1]
    assert (slowed["c"], slowed["t0"], slowed["t1"], slowed["est"]) == ("slowed", 12.0625, 14.5, False)
    assert slowed["src"][0]["ability"] == "Sarge_X"
    assert (hindered["c"], hindered["t1"], hindered["est"]) == ("hindered", 30.0 + si.POLICY["status_default_s"],
                                                                True)


def test_intervals_are_cut_at_death_and_round_end():
    alive = {"5": [[0.0, 19.3125, "kill"], [40.0, None, None]], "7": [[0.0, None, None]]}
    rows = [_flash([[5, 18.626, 1.615], [7, 99.5, 2.0]]),
            _flash([[5, 25.0, 1.0]], t=24.5), _flash([[5, 39.5, 1.0]], t=39.0), _flash([[7, 101.0, 1.0]], t=100.5)]
    conds = si.normalize_conditions(rows, alive, 100.0)
    [cut] = conds[5]
    assert (cut["t0"], cut["t1"]) == (18.626, 19.3125), "cut at the death; dead at 25.0; 39.5 was the earlier death"
    assert cut["src"][0]["t1"] == 19.3125
    [end] = conds[7]
    assert (end["t0"], end["t1"]) == (99.5, 100.0), "cut at round end; a hit after it gives nothing"


def test_a_hit_at_the_round_end_gives_nothing():
    assert si.normalize_conditions([_flash([[7, 100.0, 2.0]])], ALIVE, 100.0) == {}


def test_a_revived_player_does_not_inherit_an_earlier_lifes_flash():
    alive = {"5": [[0.0, 19.0, "kill"], [19.0, None, None]]}
    [c] = si.normalize_conditions([_flash([[5, 18.626, 1.615]])], alive, 100.0)[5]
    assert c["t1"] == 19.0
    assert si.conditions_at({5: [c]}, 5, 19.0) == []


@pytest.mark.parametrize("hit", [[10, 18.626, 1.0], [-1, 18.626, 1.0], ["5", 18.626, 1.0], [5, float("nan"), 1.0],
                                 [5, 18.626, float("nan")], [5, 18.626], "x"])
def test_bad_hits_are_skipped(hit):
    assert si.normalize_conditions([_flash([hit])], ALIVE, 100.0) == {}


def test_the_engine_reads_the_same_fallback_constants():
    assert ce.FLASH_FULL_S is si.POLICY["flash_full_s"] and ce.FLASH_DEFAULT_S == si.POLICY["flash_default_s"]
    assert ce.FLASH_FUSE_S == si.POLICY["flash_fuse_s"] and ce.NEARSIGHT_S is si.POLICY["nearsight_s"]
    assert ce.NEARSIGHT_DEFAULT_S == si.POLICY["nearsight_default_s"]
    # the values the engine shipped with (CONTROL 8): moving them changed nothing
    assert si.POLICY == {"flash_full_s": {"phoenix": 1.5, "yoru": 1.5, "breach": 2.25, "kayo": 2.25, "skye": 2.25},
                         "flash_default_s": 1.5, "flash_fuse_s": 0.5, "nearsight_s": {"omen_paranoia": 2.0},
                         "nearsight_default_s": 1.0, "status_default_s": 1.0}
