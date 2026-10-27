"""Miks' Harmonize, read from the buff effects on the players' characters (app/replays/extras.find_buffs).
Shapes from the export of 47a5f123 (2026-10-24): Miks' own buff names his pawn and carries FXC.Buff.Duration 8;
the teammate's starts in the same ms, in the same container, naming Miks' pawn."""

from app.replays.condense import PlayerTable
from app.replays.extras import _Effect, find_buffs

MIKS, SOVA, ENEMY = 1001, 1002, 1006
CODES = {"Miks": "Iris", "Sova": "Hunter", "Phoenix": "Phoenix"}


def players():
    agents = ["Phoenix", "Miks", "Sova"] + ["Phoenix"] * 7
    return PlayerTable(subjects=[None] * 10, agents=agents, pawn_slot={1000 + s: s for s in range(10)},
                       other_slot={}, pawn_changes={}, gone_ms={})


def eff(t, actor, ctx, stop, buff=8.0, container=5197):
    return _Effect(t, actor, 93, container, ctx, stop_ms=stop, buff=buff)


TEAMS = {s: ("A" if s < 5 else "B") for s in range(10)}


def test_harmonize_on_a_teammate_is_a_row_for_miks_and_one_for_the_teammate():
    effects = [eff(310650, MIKS, (MIKS,), 321661), eff(310650, SOVA, (MIKS,), 318262)]
    got = find_buffs(effects, players(), CODES, TEAMS)
    assert [(b["by"], b["target"], b["t_ms"], b["t1_ms"]) for b in got] == [(1, 1, 310650, 321661),
                                                                              (1, 2, 310650, 318262)]
    assert {b["name"] for b in got} == {"Q_Harmonize"} and {b["code"] for b in got} == {"Iris"}


def test_what_is_not_harmonize():
    p = players()
    assert find_buffs([eff(84180, MIKS, (MIKS,), 92180, buff=-1.0)], p, CODES, TEAMS) == []      # not a buff
    assert find_buffs([eff(84180, MIKS, (SOVA,), 92180)], p, CODES, TEAMS) == []                # not his own
    assert find_buffs([eff(84180, SOVA, (SOVA,), 92180)], p, CODES, TEAMS) == []                # not Miks
    bassquake = [eff(84180, MIKS, (MIKS,), 92180), eff(84180, ENEMY, (MIKS,), 92180)]           # an enemy: no
    assert [b["target"] for b in find_buffs(bassquake, p, CODES, TEAMS)] == [1]
    late = [eff(84180, MIKS, (MIKS,), 92180), eff(85000, SOVA, (MIKS,), 93000)]                  # not together
    assert [b["target"] for b in find_buffs(late, p, CODES, TEAMS)] == [1]
