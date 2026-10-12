from app.coach import detectors
from app.coach.roundview import RoundView
from tests.coach import toys


def run(blob, slot=0, knew=None, role=None):
    view = RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)
    return detectors.flags(view, slot, knew, role)


def kinds(found):
    return [f["kind"] for f in found]


def test_a_death_far_from_everyone_and_never_avenged_is_flagged():
    pos = {**toys.ten(), 0: (5000, 5000)}                       # slot 0 alone in mid
    found = run(toys.round_blob(pos, kills=[{"t": 20.0, "killer": 5, "victim": 0}]))
    (flag,) = [f for f in found if f["kind"] == "death_untraded_alone"]
    assert flag["t"] == 20.0 and flag["values"]["killer"] == "P5"
    assert flag["values"]["nearest_teammate_m"] > detectors.TRADE_RANGE_M


def test_a_death_beside_a_teammate_is_not_flagged_as_alone():
    found = run(toys.round_blob(toys.ten(), kills=[{"t": 20.0, "killer": 5, "victim": 0}]))
    assert "death_untraded_alone" not in kinds(found)


def test_a_death_avenged_inside_the_window_is_a_trade():
    kills = [{"t": 20.0, "killer": 5, "victim": 0}, {"t": 23.0, "killer": 1, "victim": 5}]
    found = run(toys.round_blob({**toys.ten(), 0: (5000, 5000)}, kills=kills))
    assert "death_traded" in kinds(found) and "death_untraded_alone" not in kinds(found)


def test_a_kill_on_the_killer_after_the_window_is_not_a_trade():
    kills = [{"t": 20.0, "killer": 5, "victim": 0}, {"t": 27.0, "killer": 1, "victim": 5}]
    assert "death_traded" not in kinds(run(toys.round_blob(toys.ten(), kills=kills)))


def test_a_kill_on_the_killer_after_the_round_was_decided_is_not_a_trade():
    kills = [{"t": 20.0, "killer": 5, "victim": 0}, {"t": 23.0, "killer": 1, "victim": 5}]
    assert "death_traded" not in kinds(run(toys.round_blob(toys.ten(), kills=kills, t_decided=22.0)))


def test_the_last_player_alive_is_not_flagged_as_alone():
    kills = [{"t": 5.0 + s, "killer": 5, "victim": s} for s in (1, 2, 3, 4)] + [{"t": 20.0, "killer": 5, "victim": 0}]
    assert "death_untraded_alone" not in kinds(run(toys.round_blob({**toys.ten(), 0: (5000, 5000)}, kills=kills)))


def test_a_death_after_the_round_was_decided_is_not_flagged():
    blob = toys.round_blob({**toys.ten(), 0: (5000, 5000)}, kills=[{"t": 50.0, "killer": 5, "victim": 0}],
                           t_decided=40.0)
    assert kinds(run(blob)) == []                          # no death flag and no first-contact flag


def test_dying_while_ahead_is_flagged_with_the_count():
    kills = [{"t": 10.0, "killer": 1, "victim": 5}, {"t": 20.0, "killer": 6, "victim": 0}]
    (flag,) = [f for f in run(toys.round_blob(toys.ten(), kills=kills)) if f["kind"] == "death_with_advantage"]
    assert flag["values"]["alive_before"] == {"own": 5, "enemy": 4}


def test_first_contact_names_the_players_part():
    kills = [{"t": 10.0, "killer": 0, "victim": 5}, {"t": 20.0, "killer": 6, "victim": 1}]
    assert "first_kill" in kinds(run(toys.round_blob(toys.ten(), kills=kills), slot=0))
    assert "first_death" in kinds(run(toys.round_blob(toys.ten(), kills=kills), slot=5))
    assert not {"first_kill", "first_death"} & set(kinds(run(toys.round_blob(toys.ten(), kills=kills), slot=1)))


def flash(by, hits, t=10.0, complete=True):
    return {"k": "flash", "t": t, "by": by, "u": 0, "v": 0, "hits": hits,
            "activation": {"state": "completed" if complete else "unknown", "targets_complete": complete}}


def test_a_flash_that_provably_hit_no_one_is_flagged():
    assert "flash_hit_no_enemy" in kinds(run(toys.round_blob(toys.ten(), util=[flash(0, [])])))


def test_an_unproven_empty_flash_is_not_flagged():
    assert kinds(run(toys.round_blob(toys.ten(), util=[flash(0, [], complete=False)]))) == []


def test_a_flash_followed_by_a_kill_on_its_target_is_credited():
    blob = toys.round_blob(toys.ten(), util=[flash(0, [[5, 10.5, 1.5]])],
                           kills=[{"t": 12.0, "killer": 1, "victim": 5}])
    (flag,) = [f for f in run(blob) if f["kind"] == "flash_led_to_kill"]
    assert flag["values"] == {"hit": ["P5"], "killed": ["P5"], "killers": ["P1"]}


def test_a_flash_on_a_teammate_is_flagged():
    assert "flash_hit_teammate" in kinds(run(toys.round_blob(toys.ten(), util=[flash(0, [[1, 10.5, 1.0]])])))


def test_a_zero_second_hit_is_not_a_hit():
    blob = toys.round_blob(toys.ten(), util=[flash(0, [[5, 10.5, 0], [1, 10.5, 0.0]])],
                           kills=[{"t": 12.0, "killer": 1, "victim": 5}])
    assert kinds(run(blob)) == ["flash_hit_no_enemy"]      # not credited with the kill, not blamed for the teammate


def test_a_hit_of_unknown_length_still_counts():
    blob = toys.round_blob(toys.ten(), util=[flash(0, [[5, 10.5, None]])],
                           kills=[{"t": 12.0, "killer": 1, "victim": 5}])
    assert "flash_led_to_kill" in kinds(run(blob))


STACK = {"A": {str(s): [[10.0, 40.0, 2000, 2000]] for s in (5, 6, 7, 8)}, "B": {}}


def stack_blob(mover=None):
    pos = {**{s: (8000, 8000) for s in range(5)}, **{s: (2000, 2000) for s in range(5, 10)}}
    tracks = {0: mover} if mover else None
    return toys.round_blob(pos, tracks=tracks)


def test_four_enemies_seen_on_the_other_site_while_the_player_stays_is_flagged():
    (flag,) = [f for f in run(stack_blob(), knew=STACK, role="defense") if f["kind"] == "stack_seen_elsewhere"]
    assert flag["t"] == 10.0
    assert flag["values"]["site"] == "A" and flag["values"]["enemies_seen"] == 4
    assert flag["values"]["player_area_then"] == "B Site" and flag["values"]["player_area_after"] == "B Site"
    assert flag["values"]["role"] == "defense"


def test_a_stack_flag_without_a_known_role_says_so():
    (flag,) = [f for f in run(stack_blob(), knew=STACK) if f["kind"] == "stack_seen_elsewhere"]
    assert flag["values"]["role"] is None


def test_a_player_who_leaves_within_the_hold_is_not_flagged():
    # On B Site until 12 s, so the stack (known from 10 s) is seen from B; in Mid by 17 s, inside the 8 s hold.
    mover = (toys.stand(8000, 8000, 0.0, 12.0) + toys.walk(8000, 8000, 5000, 5000, 12.25, 17.0)
             + toys.stand(5000, 5000, 17.25, 60.0))
    view = RoundView(stack_blob(mover), toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)
    assert view.position(0, 10.0)["area"]["name"] == "B Site"        # the fixture's own claim, checked
    assert view.position(0, 18.0)["area"]["name"] == "Mid Courtyard"
    assert "stack_seen_elsewhere" not in kinds(run(stack_blob(mover), knew=STACK))


def test_a_player_who_leaves_after_the_hold_is_flagged():
    mover = (toys.stand(8000, 8000, 0.0, 30.0) + toys.walk(8000, 8000, 5000, 5000, 30.25, 35.0)
             + toys.stand(5000, 5000, 35.25, 60.0))
    assert "stack_seen_elsewhere" in kinds(run(stack_blob(mover), knew=STACK))


def test_three_enemies_are_not_a_stack():
    knew = {"A": {str(s): [[10.0, 40.0, 2000, 2000]] for s in (5, 6, 7)}, "B": {}}
    assert "stack_seen_elsewhere" not in kinds(run(stack_blob(), knew=knew))


def test_without_team_knowledge_the_stack_check_is_skipped():
    assert "stack_seen_elsewhere" not in kinds(run(stack_blob(), knew=None))


def test_a_life_that_starts_late_is_still_checked_for_a_stack():
    blob = stack_blob()
    blob["alive"]["0"] = [[5.0, None, "round_end"]]           # not alive for the first five seconds
    knew = {"A": {str(s): [[20.0, 50.0, 2000, 2000]] for s in (5, 6, 7, 8)}, "B": {}}
    (flag,) = [f for f in run(blob, knew=knew) if f["kind"] == "stack_seen_elsewhere"]
    assert flag["t"] == 20.0
