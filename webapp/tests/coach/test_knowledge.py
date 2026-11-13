import pytest

from app.coach import knowledge
from app.coach.roundview import RoundView
from tests.coach import toys


def view(blob):
    return RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)


KNEW = {"A": {"5": [[5.0, 8.0, 8000, 8000], [20.0, 40.0, 5000, 5000]], "6": [[30.0, 31.0, 8000, 8000]]},
        "B": {}}


def test_nothing_is_known_before_the_first_sighting():
    assert knowledge.known_enemies(KNEW, "A", 4.0, view(toys.round_blob(toys.ten()))) == []


def test_a_past_sighting_keeps_its_last_place_and_time():
    (e,) = knowledge.known_enemies(KNEW, "A", 15.0, view(toys.round_blob(toys.ten())))
    assert (e["slot"], e["name"], e["first_seen"], e["last_seen"], e["in_view"]) == (5, "P5", 5.0, 8.0, False)
    assert e["area"]["name"] == "B Site" and e["alive"] is True


def test_an_enemy_in_view_is_placed_where_they_are_now():
    v = view(toys.round_blob(toys.ten()))          # slot 5 really stands on B Site the whole round
    (e,) = knowledge.known_enemies(KNEW, "A", 25.0, v)
    assert e["in_view"] is True and e["last_seen"] == 25.0 and e["area"]["name"] == "B Site"


def test_a_dead_enemy_is_marked_dead():
    v = view(toys.round_blob(toys.ten(), kills=[{"t": 10.0, "killer": 0, "victim": 5}]))
    (e,) = knowledge.known_enemies(KNEW, "A", 15.0, v)
    assert e["alive"] is False


def test_the_other_side_knows_only_its_own_sightings():
    assert knowledge.known_enemies(KNEW, "B", 50.0, view(toys.round_blob(toys.ten()))) == []


EVENTS = {"A": [[3.0, 6, "footsteps"], [12.5, 7, "gunfire"], [40.0, 6, "seen"]], "B": []}


def test_signals_are_the_sides_locating_events_so_far():
    assert knowledge.signals(EVENTS, "A", 20.0, toys.NAMES) == {
        "signals": [{"t": 3.0, "name": "P6", "reason": "footsteps", "class": "found"},
                    {"t": 12.5, "name": "P7", "reason": "gunfire", "class": "found"}],
        "ruled_out": [], "lost_track": []}
    assert knowledge.signals(EVENTS, "B", 20.0, toys.NAMES) == {"signals": [], "ruled_out": [], "lost_track": []}


def test_a_signal_places_the_enemy_only_roughly_from_the_nearest_teammate():
    # slot 6 stands at (8000, 8000); teammate 1 is 3000 uv straight above (42.9 m), the rest 121 m away
    v = view(toys.round_blob({**toys.ten(), 1: (8000, 5000)}))
    first = knowledge.signals(EVENTS, "A", 20.0, toys.NAMES, v)["signals"][0]
    assert first["rough_place"] == {"from": "P1", "direction": "down", "metres_min": 40.0, "metres_max": 50.0}
    assert not {"u", "v", "area"} & set(first) and not {"u", "v", "area"} & set(first["rough_place"])


@pytest.mark.parametrize("mate, direction", [((5000, 8000), "right"), ((9500, 8000), "left"), ((8000, 9500), "up"),
                                             ((7000, 6500), "down"), ((6500, 7000), "right")])
def test_the_direction_is_one_of_four_minimap_quarters(mate, direction):
    v = view(toys.round_blob({**toys.ten(), 1: mate}))
    assert knowledge.signals(EVENTS, "A", 5.0, toys.NAMES, v)["signals"][0]["rough_place"]["direction"] == direction


def test_the_distance_band_is_fixed_so_it_does_not_give_the_distance_away():
    for u in (7300, 7000, 6610):                    # 10.0 m, 14.3 m and 19.9 m to the left of the enemy
        v = view(toys.round_blob({**toys.ten(), 1: (u, 8000)}))
        rough = knowledge.signals(EVENTS, "A", 5.0, toys.NAMES, v)["signals"][0]["rough_place"]
        assert (rough["metres_min"], rough["metres_max"]) == (10.0, 20.0)


def test_a_signal_with_no_living_teammate_to_place_it_from_has_no_rough_place():
    kills = [{"t": 1.0, "killer": 5, "victim": s} for s in range(5)]
    v = view(toys.round_blob(toys.ten(), kills=kills))
    assert knowledge.signals(EVENTS, "A", 5.0, toys.NAMES, v) == {
        "signals": [{"t": 3.0, "name": "P6", "reason": "footsteps", "class": "found"}],
        "ruled_out": [], "lost_track": []}


def test_a_round_that_stores_no_events_has_no_signals_answer():
    assert knowledge.signals(None, "A", 20.0, toys.NAMES) is None


@pytest.mark.parametrize("reason", ["trip_destroyed", "camera_destroyed"])
@pytest.mark.parametrize("gun, mate, heard", [
    ("Phantom", (8000, 5900), True),   # 30 m from the shooter
    ("Phantom", (8000, 4500), False),  # 50 m: beyond the silenced gun's range
    ("Vandal", (8000, 4500), True),
    ("unknown", (8000, 4500), True),
    (None, (8000, 4500), True),
])
def test_destroyed_signal_has_a_rough_place_only_if_the_shot_was_heard(reason, gun, mate, heard):
    shot = {"k": "shot", "t": 10.0, "by": 5, "gun": gun}
    v = view(toys.round_blob({**toys.ten(), 1: mate}, util=[shot]))
    (signal,) = knowledge.signals({"A": [[10.0, 5, reason, "locate"]]}, "A", 10.0,
                                 toys.NAMES, v)["signals"]
    assert signal["class"] == "found" and signal["reason"] == reason
    assert ("rough_place" in signal) is heard


@pytest.mark.parametrize("change, heard", [
    ({"t": 9.5}, True), ({"t": 10.05}, True),
    ({"t": 9.49}, False), ({"t": 10.06}, False),
    ({"by": 6}, False), ({"k": "cast"}, False),
])
def test_destroyed_signal_uses_only_the_destroyers_shot_in_the_window(change, heard):
    shot = {"k": "shot", "t": 10.0, "by": 5, "gun": "Vandal", **change}
    v = view(toys.round_blob(toys.ten(), util=[shot]))
    (signal,) = knowledge.signals({"A": [[10.0, 5, "camera_destroyed", "locate"]]}, "A", 10.0,
                                 toys.NAMES, v)["signals"]
    assert ("rough_place" in signal) is heard


@pytest.mark.parametrize("dead", [False, True])
def test_destroyed_signal_without_a_hearing_living_teammate_never_requests_a_place(dead, monkeypatch):
    shot = {"k": "shot", "t": 10.0, "by": 5, "gun": "Phantom"}
    kills = [{"t": 9.0, "killer": 5, "victim": 1}] if dead else []
    v = view(toys.round_blob({**toys.ten(), 1: (8000, 5900)}, kills=kills, util=[shot] if dead else []))

    def no_rough_place(*args):
        pytest.fail("an unheard destroying shot must not request a rough place")

    monkeypatch.setattr(knowledge, "rough_place", no_rough_place)
    (signal,) = knowledge.signals({"A": [[10.0, 5, "trip_destroyed", "locate"]]}, "A", 10.0,
                                 toys.NAMES, v)["signals"]
    assert "rough_place" not in signal


def test_destroyed_signal_checks_hearing_at_the_shot_time():
    shot = {"k": "shot", "t": 9.5, "by": 5, "gun": "Phantom"}
    # The only listener within range dies between the shot and the destroyed-device event.
    v = view(toys.round_blob({**toys.ten(), 1: (8000, 5900)},
                            kills=[{"t": 9.75, "killer": 5, "victim": 1}], util=[shot]))
    (signal,) = knowledge.signals({"A": [[10.0, 5, "camera_destroyed", "locate"]]}, "A", 10.0,
                                 toys.NAMES, v)["signals"]
    assert "rough_place" in signal


@pytest.mark.parametrize("with_kind", [False, True])
def test_round_6_haunt_exclusions_never_request_or_return_a_place(with_kind, monkeypatch):
    events = {"A": [[10.0, s, "haunt", "exclude"] if with_kind else [10.0, s, "haunt"]
                    for s in range(5, 10)]}

    def no_place(*args):
        pytest.fail("an exclusion must not request the enemy's position")

    monkeypatch.setattr(knowledge, "rough_place", no_place)
    out = knowledge.signals(events, "A", 10.0, toys.NAMES, view(toys.round_blob(toys.ten())))
    assert out == {"signals": [], "ruled_out": [
        {"t": 10.0, "name": toys.NAMES[s], "reason": "haunt"} for s in range(5, 10)], "lost_track": []}


def test_round_21_haunt_locates_have_places_but_the_exclusion_does_not():
    events = {"A": [[10.0, s, "haunt", "locate"] for s in (5, 6, 7, 9)] + [[10.0, 8, "haunt", "exclude"]]}
    out = knowledge.signals(events, "A", 10.0, toys.NAMES, view(toys.round_blob(toys.ten())))
    assert [e["name"] for e in out["signals"]] == [toys.NAMES[s] for s in (5, 6, 7, 9)]
    assert all(e["class"] == "found" and "rough_place" in e for e in out["signals"])
    assert out["ruled_out"] == [{"t": 10.0, "name": toys.NAMES[8], "reason": "haunt"}]
    assert out["lost_track"] == []


@pytest.mark.parametrize("reason, code, device", [
    ("haunt", "BountyHunter", "E_LoSReveal_Source_Reactivate"),
    ("recon", "Hunter", "Q_SonarPing"),
])
@pytest.mark.parametrize("with_kind", [False, True])
def test_ruled_out_pulse_area_comes_from_its_emitting_row(reason, code, device, with_kind, monkeypatch):
    pulse = {"k": "ability", "t": 10.0, "t1": 12.0, "by": 0, "code": code, "name": device,
             "u": 2000, "v": 2000}
    # Enemy 5 is on B Site; expired, future and enemy-side devices are there too.
    util = [{**pulse, "t": 1.0, "t1": 7.0, "u": 8000, "v": 8000}, pulse,
            {**pulse, "t": 11.0, "t1": 20.0, "u": 8000, "v": 8000},
            {**pulse, "by": 5, "u": 8000, "v": 8000}]
    v = view(toys.round_blob(toys.ten(), util=util))
    enemy = v.position(5, 10.0)
    enemy_area = v.area(enemy["u"], enemy["v"])

    def no_enemy_position(*args):
        pytest.fail("a ruled-out pulse must not look up any player's position")

    monkeypatch.setattr(v, "position", no_enemy_position)
    row = [10.0, 5, reason] + (["exclude"] if with_kind else [])
    out = knowledge.signals({"A": [row]}, "A", 20.0, toys.NAMES, v)
    assert out == {"signals": [], "lost_track": [], "ruled_out": [
        {"t": 10.0, "name": toys.NAMES[5], "reason": reason, "area": v.area(2000, 2000)}]}
    assert out["ruled_out"][0]["area"]["name"] == "A Site"
    assert out["ruled_out"][0]["area"] != enemy_area


@pytest.mark.parametrize("change", [None, {"t": 11.0}, {"t": 9.0}, {"t": None},
                                    {"by": 5}, {"by": None}, {"u": None}, {"v": None},
                                    {"k": "cast"}, {"name": "Q_TetherGrenade"}])
def test_ruled_out_pulse_without_a_usable_source_omits_area(change):
    pulse = {"k": "ability", "t": 10.0, "t1": 12.0, "by": 0, "code": "BountyHunter",
             "name": "E_LoSReveal_Source_Reactivate", "u": 2000, "v": 2000}
    util = [] if change is None else [{**pulse, **change}]
    out = knowledge.signals({"A": [[10.0, 5, "haunt", "exclude"]]}, "A", 10.0, toys.NAMES,
                            view(toys.round_blob(toys.ten(), util=util)))
    assert out["ruled_out"] == [{"t": 10.0, "name": toys.NAMES[5], "reason": "haunt"}]


def test_ruled_out_pulse_with_ambiguous_sources_omits_area():
    pulse = {"k": "ability", "t": 10.0, "t1": 12.0, "by": 0, "code": "BountyHunter",
             "name": "E_LoSReveal_Source_Reactivate", "u": 2000, "v": 2000}
    v = view(toys.round_blob(toys.ten(), util=[pulse, {**pulse, "by": 1, "u": 8000, "v": 8000}]))
    out = knowledge.signals({"A": [[10.0, 5, "haunt", "exclude"]]}, "A", 10.0, toys.NAMES, v)
    assert "area" not in out["ruled_out"][0]


def test_ruled_out_pulses_in_the_same_area_agree():
    pulse = {"k": "ability", "t": 10.0, "by": 0, "code": "Hunter", "name": "Q_SonarPing",
             "u": 2000, "v": 2000}
    v = view(toys.round_blob(toys.ten(), util=[pulse, {**pulse, "by": 1, "u": 2100}]))
    (signal,) = knowledge.signals({"A": [[10.0, 5, "recon", "exclude"]]}, "A", 10.0,
                                 toys.NAMES, v)["ruled_out"]
    assert signal["area"]["name"] == "A Site"


@pytest.mark.parametrize("reason, code, source, device", [
    ("recon", "Hunter", "Q_SonarPing", "Q_SonarBolt"),
    ("haunt", "BountyHunter", "E_LoSReveal_Source_Reactivate", "E_Divebomb"),
])
@pytest.mark.parametrize("with_kind", [False, True])
@pytest.mark.parametrize("pulse_time", [None, 9.99, 9.999, 10.001, 10.01])
def test_pulse_area_uses_the_ping_instead_of_a_live_device(reason, code, source, device, with_kind, pulse_time):
    bolt = {"k": "ability", "t": 8.0, "t1": 12.0, "by": 0, "code": code, "name": device,
            "u": 8000, "v": 8000}
    ping = {"k": "ability", "t": pulse_time, "t1": 10.5, "by": 0, "code": code, "name": source,
            "u": 2000, "v": 2000}
    v = view(toys.round_blob(toys.ten(), util=[bolt] + ([ping] if pulse_time is not None else [])))
    row = [10.0, 5, reason] + (["exclude"] if with_kind else [])
    (signal,) = knowledge.signals({"A": [row]}, "A", 10.0, toys.NAMES, v)["ruled_out"]
    if pulse_time in (9.999, 10.001):
        assert signal["area"]["name"] == "A Site"
    else:
        assert "area" not in signal


def test_restricted_knife_is_roughly_placed_and_other_and_future_events_are_dropped():
    events = {"A": [[10.0, 5, "knife_hit", "restrict"], [10.0, 6, "died"],
                    [11.0, 7, "haunt", "locate"], [11.0, 8, "haunt", "exclude"],
                    [11.0, 9, "omen_unheard"], [10.0, 7, "unknown_reason"]]}
    out = knowledge.signals(events, "A", 10.0, toys.NAMES, view(toys.round_blob(toys.ten())))
    (signal,) = out["signals"]
    assert (signal["name"], signal["reason"], signal["class"]) == (toys.NAMES[5], "knife_hit", "roughly_placed")
    assert "rough_place" in signal
    assert out["ruled_out"] == out["lost_track"] == []


def test_lost_track_never_requests_or_returns_a_place(monkeypatch):
    def no_place(*args):
        pytest.fail("losing track must not request the enemy's position")

    monkeypatch.setattr(knowledge, "rough_place", no_place)
    assert knowledge.signals({"A": [[10.0, 5, "omen_unheard"]]}, "A", 10.0, toys.NAMES, object()) == {
        "signals": [], "ruled_out": [],
        "lost_track": [{"t": 10.0, "name": toys.NAMES[5], "reason": "omen_unheard"}]}
