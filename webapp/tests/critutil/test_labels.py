"""Row labels, ability names from the table, and the spike_got job (plan 5, W19, R7, D17)."""

import pytest

from app.services import replay_util, util_values
from tests.critutil.test_build import FRIEND, toy_corpus
from tests.critutil.test_values import MODEL


@pytest.mark.parametrize("sig,want", [
    (None, 0.0), ({}, 0.0), ({"got_spike": False, "got_spike_planted": True}, 0.0),
    ({"got_spike": None}, 0.0), ({"got_spike": True, "got_spike_planted": False}, 1.0),
    ({"got_spike": True, "got_spike_planted": None}, 1.0), ({"got_spike": True, "got_spike_planted": True}, 2.0),
])
def test_spike_got_feature(sig, want):
    cast = {} if sig is None else {"signals": sig}
    assert util_values.features(cast)["spike_got"] == want
    assert "spike_got" in util_values.DEFAULT_WEIGHTS


def test_label_of():
    f = replay_util.label_of
    for tag, label in replay_util.LABELS:
        assert f([tag]) == label
    assert [l for _, l in replay_util.LABELS] == ["Blinds", "Smokes", "Damaging", "Status", "Info", "Teleports", "Heals"]
    assert f(["damaging", "status"]) == "Damaging"
    assert f(["status", "blind"]) == "Blinds"
    for kinds in (["wall"], ["device"], ["mobility"], ["ult"], [], None):
        assert f(kinds) is None


def test_ability_name_from_the_table_id():
    cast = {"ability_id": "Raze/Showstopper", "agent": "Raze", "codes": ["Clay_X_Rocket"], "names": ["X_Rocket"]}
    assert replay_util.ability_label(cast) == "Showstopper"
    assert replay_util.ability_label({**cast, "ability_id": None}) == replay_util.ability_label(
        {k: v for k, v in cast.items() if k != "ability_id"})


def test_cast_and_heal_rows_carry_a_label(monkeypatch):
    stored, ctx = toy_corpus()
    real = util_values.value_round

    def with_heal(data, model, weights=None):
        out = real(data, model, weights)
        out["heals"] = [{"by": 1, "t": 12.0, "value": 0.02, "saved": True, "code": "X", "name": "Y", "target": 0}]
        return out

    monkeypatch.setattr(util_values, "value_round", with_heal)
    rows = replay_util._cast_rows(1, ctx[1], stored[1], MODEL, None)
    casts = [r for r in rows if r["kind"] != "heal"]
    heals = [r for r in rows if r["kind"] == "heal"]
    assert casts and all(r["label"] == "Blinds" for r in casts)
    assert heals and all(r["label"] == "Heals" for r in heals)
