"""app/services/replay_impact.py: the per-kill split reconciles with the stored rows or refuses
(decision 5 as amended 2026-09-27). Pure: fabricated observer events and rows."""

from types import SimpleNamespace

import pytest

from app.services.replay_impact import SplitRefused, reconcile

FIELDS = ("kill_impact", "death_impact", "damage", "trade_credit")
LEVERAGE = 2.5


def event(kill_id, round_number, killer, victim, bonus, death_bonus):
    return {"round_number": round_number,
            "kill": {"id": kill_id, "killer_match_player_id": killer, "death_match_player_id": victim,
                     "kill_order_bonus_x_time": bonus, "death_order_bonus_x_time": death_bonus}}


def row(round_id, mp, kill_impact, death_impact, damage=0, trade_credit=0, assists=0.0):
    return SimpleNamespace(round_id=round_id, match_player_id=mp, kill_impact=kill_impact, death_impact=death_impact,
                           damage=damage, trade_credit=trade_credit, assists_component=assists)


def case():
    # Round 1 (id 10): player 1 kills 2 (bonus 4 -> +10), then 3 kills 1 (bonus 2 -> +5; death -3 -> -7.5).
    observed = [event(100, 1, 1, 2, 4.0, -2.0), event(101, 1, 3, 1, 2.0, -3.0)]
    rows = [row(10, 1, 12, -8, damage=2), row(10, 2, 0, -5), row(10, 3, 5, 0)]
    stored = {(r.round_id, r.match_player_id): SimpleNamespace(**vars(r)) for r in rows}
    return observed, rows, stored, {10: 1}


def test_the_split_reconciles_and_keeps_full_precision():
    observed, rows, stored, number_of = case()
    split = reconcile(observed, rows, stored, number_of, FIELDS, LEVERAGE)
    assert split == {100: [10.0, -5.0], 101: [5.0, -7.5]}


def test_a_recomputed_row_that_differs_from_the_stored_one_refuses():
    observed, rows, stored, number_of = case()
    stored[(10, 3)].kill_impact = 6
    with pytest.raises(SplitRefused, match="differs"):
        reconcile(observed, rows, stored, number_of, FIELDS, LEVERAGE)


def test_death_shares_that_dont_add_up_refuse():
    observed, rows, stored, number_of = case()
    for r in (rows[1], stored[(10, 2)]):
        r.death_impact = -6
    with pytest.raises(SplitRefused, match="death shares"):
        reconcile(observed, rows, stored, number_of, FIELDS, LEVERAGE)


def test_kill_shares_that_dont_add_up_refuse():
    observed, rows, stored, number_of = case()
    for r in (rows[2], stored[(10, 3)]):
        r.kill_impact = 9
    with pytest.raises(SplitRefused, match="kill shares"):
        reconcile(observed, rows, stored, number_of, FIELDS, LEVERAGE)


def test_a_missing_stored_row_refuses():
    observed, rows, stored, number_of = case()
    del stored[(10, 3)]
    with pytest.raises(SplitRefused):
        reconcile(observed, rows, stored, number_of, FIELDS, LEVERAGE)
