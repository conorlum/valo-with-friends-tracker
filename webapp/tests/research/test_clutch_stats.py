"""Offline contracts for the clutch research statistics and private output."""

import copy
import csv
import math
import re
import socket
import subprocess
import sys
from pathlib import Path
from statistics import mean

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import clutch_stats as stats
from app.services import stats_math, util_model


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected network access")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def row(i=0, *, won=True, value=1, **changes):
    """The flat W1 situation plus W2/W3 nested measure results, with toy metadata."""
    result = {
        "situation_id": f"situation-{i}", "kind": "1vN", "team": "team-1",
        "side": "attack", "slot": 0, "n_enemies": 1, "own_alive": 1,
        "t_s": 30, "t_end_window": 80, "won": won, "alive_at_end": won,
        "group": "1v1", "n_at_start": 1,
        "uuid": f"00000000-0000-0000-0000-{i:012d}", "round": 2,
        "match_id": f"match-{i % 10}", "clock_offset": 5, "t_plant": None,
        "label": "player 1", "agent": "Jett", "enemy_agents": {5: "Omen"},
        "clock": {"time_left_start": 70, "time_left_first_engagement": 65,
                  "still_share": .2, "plant_attempts": [
                      {"t": 60, "t1": 64, "by": 0, "enough_time": True, "finished": True}],
                  "defuse_attempts": []},
        "state": {"gun_tier": 3, "enemy_gun_tiers": {5: 1}, "hp_shield": 125,
                  "team_loadouts": {"team-1": 12000, "team-2": 10000}, "spike_state": None},
        "isolation": {"one_at_a_time": value, "engagements": 2, "isolated": 1, "unseen": 1},
        "engagement_rows": [{"t": 35, "enemies": [5], "kind": "fight", "in_view": 1,
                             "clear_line": 2}],
        "unknown": {"unknown_share_start": .8, "unknown_share_end": .3, "into_unknown_share": .4},
        "backshots": 1, "information": {"located_share": .5},
        "fakes": {"fakes": 2, "fake_followed": 1},
        "util": {"held_start": 3, "used": 1, "held_at_death": 2, "ult_ready": True},
        "p_stored": .5, "p_fit": .4,
    }
    result.update(changes)
    return result


def measure(name):
    return next(m for m in stats.MEASURES if m["name"] == name)


def results(rows, names=("one_at_a_time",)):
    return [stats.compare(rows, measure(name), group) for name in names for group in stats.GROUPS]


def report(tmp_path, rows, comparisons=None, **kwargs):
    path = stats.write_report(tmp_path, rows, comparisons if comparisons is not None else results(rows),
                              kwargs.get("coverage", {"replays": {"ok": 10, "missing": 1}}),
                              kwargs.get("cutoffs", []), "toy model")
    return path.read_text(encoding="utf-8")


def test_import_is_database_free():
    code = """import sys
sys.path.insert(0, 'scripts')
import clutch_stats
assert not {'app.db', 'app.models', 'app.scoring.plant_window', 'preview_control_live'} & sys.modules.keys()
"""
    check = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


@pytest.mark.parametrize("side,own,enemies,plant", [
    ("defense", 1, 1, None), ("attack", 1, 2, None), ("defense", 2, 3, 20)])
def test_expected_side_and_database_clock(side, own, enemies, plant):
    rows = [row(side=side, own_alive=own, n_enemies=enemies, t_plant=plant, clock_offset=20)]
    a, d = (own, enemies) if side == "attack" else (enemies, own)
    model = util_model.fit([(a, d, plant is not None, 50, None if plant is None else 40, True)] * 12 +
                           [(a, d, plant is not None, 30, None if plant is None else 0, False)] * 4)
    assert stats.expected(rows, model) is rows
    assert rows[0]["p_stored"] == util_model.p_side(
        model, side, a, d, plant is not None, 50, None if plant is None else plant + 20)


def test_leave_one_match_out_and_kind_isolation():
    a = row(match_id="A", won=True)
    b = row(1, match_id="B", won=False, side="defense")
    other = row(2, match_id="B", kind="2vN", own_alive=2, won=False)
    rows = [a, b, other]
    assert stats.fit_leave_one_match_out(rows) is rows
    fitted_b = util_model.fit([(1, 1, False, 35, None, True)])
    fitted_both = util_model.fit([(1, 1, False, 35, None, True)] * 2)
    assert a["p_fit"] == util_model.p_side(fitted_b, "attack", 1, 1, False, 35, None)
    assert a["p_fit"] != util_model.p_side(fitted_both, "attack", 1, 1, False, 35, None)
    assert other["p_fit"] is None  # no other match of this kind


def test_calibration_uses_stored_fold_predictions_in_side_frame(monkeypatch):
    monkeypatch.setattr(util_model, "calibration", lambda *a: pytest.fail("re-fitted calibration"))
    rows = [row(won=True, side="defense", p_fit=.2), row(1, won=False, p_fit=.8),
            row(2, p_fit=None)]
    assert stats.calibration(rows, "p_fit") == [
        {"bin": 2, "n": 1, "predicted": .2, "actual": 1.0},
        {"bin": 8, "n": 1, "predicted": .8, "actual": 0.0}]


def test_comparison_cluster_intervals_nulls_and_constants():
    rows = [row(i, won=w, value=int(w)) for i in range(10) for w in (True, False)]
    got = stats.compare(rows, measure("one_at_a_time"), "1v1")
    assert (got["n"], got["won_median"], got["lost_median"], got["difference"]) == (20, 1, 0, 1)
    assert got["interval"][0] > 0
    identical = [row(i, won=w, value=i % 2) for i in range(10) for w in (True, False)]
    interval = stats.compare(identical, "one_at_a_time", "1v1")["interval"]
    assert interval[0] <= 0 <= interval[1]
    assert stats.compare([], "one_at_a_time", "1v1")["status"] == "not estimable"
    constant = stats.compare([row(i) for i in range(4)], "one_at_a_time", "1v1")
    assert constant["status"] == "constant" and constant["interval"] is None
    rows.append(row(50, value=None))
    assert stats.compare(rows, "one_at_a_time", "1v1")["n"] == 20


def test_residual_uses_raw_rows_paired_match_bootstrap_and_median_ties():
    rows = [row(3*i+j, value=v, won=(i+j) % 3 != 0, p_stored=.1*(j+1))
            for i in range(10) for j, v in enumerate((0, 1, 2))]
    got = stats.compare(rows, "one_at_a_time", "1v1")["residual"]
    lower = [r for r in rows if r["isolation"]["one_at_a_time"] <= 1]
    upper = [r for r in rows if r["isolation"]["one_at_a_time"] > 1]
    residual = lambda r: int(r["won"]) - r["p_stored"]
    assert (got["lower_n"], got["upper_n"], got["split"]) == (20, 10, 1)
    assert got["difference"] == pytest.approx(mean(map(residual, upper)) - mean(map(residual, lower)))
    groups = {key: [r for r in rows if r["match_id"] == key]
              for key in dict.fromkeys(r["match_id"] for r in rows)}
    def average(clusters, high):
        values = [residual(r) for cluster in clusters for r in cluster
                  if (r["isolation"]["one_at_a_time"] > 1) == high]
        return mean(values) if values else None
    assert got["interval"] == stats_math.paired_bootstrap_delta(
        lambda cs: average(cs, True), lambda cs: average(cs, False), groups, draws=1000, seed=0)
    bools = [row(i, util={"ult_ready": i < 3}) for i in range(10)]
    halves = stats.compare(bools, "ult_ready", "1v1")["residual"]
    assert (halves["lower_n"], halves["upper_n"]) == (7, 3)


def test_win_rate_difference_alone_is_not_a_candidate():
    rows = [row(i, won=high, value=int(high), p_stored=float(high))
            for i in range(25) for high in (True, False)]
    got = stats.compare(rows, "one_at_a_time", "1v1")
    assert got["difference"] == 1 and got["residual"]["difference"] == 0
    assert got["residual"]["interval"][0] <= 0 <= got["residual"]["interval"][1]
    assert stats.candidates([got], rows) == []


def test_empty_or_nan_interval_is_not_estimable(monkeypatch):
    monkeypatch.setattr(stats_math, "paired_bootstrap_delta", lambda *a, **k: (math.nan, math.nan))
    got = stats.compare([row(), row(1, value=0, won=False)], "one_at_a_time", "1v1")
    assert got["status"] == got["residual"]["status"] == "not estimable"


@pytest.mark.parametrize("size,expected_count", [(25, 1), (15, 0)])
def test_candidate_thresholds_seeded_examples_and_wording(tmp_path, size, expected_count):
    rows = [row(i*2 + int(w), won=w, value=int(w)) for i in range(size) for w in (True, False)]
    comparisons = results(rows)
    chosen = stats.candidates(comparisons, rows, seed=0)
    assert len(chosen) == expected_count
    assert chosen == stats.candidates(comparisons, rows, seed=0)
    if chosen:
        assert len(chosen[0]["followed"]) == len(chosen[0]["did_not"]) == 5
    text = report(tmp_path, rows, comparisons)
    section = text.split("Candidate patterns for the owner", 1)[1]
    assert not re.search(r"\b(should|good|bad|better|worse|avoid|mistake)\b", section, re.I)
    assert re.search(r"/replays/[\da-f-]+\?round=2&t=30", section)
    assert "the enemy Omen" in section


def test_registry_scalars_and_conditional_attempts():
    r = row()
    values = {m["name"]: m["from"](r) for m in stats.MEASURES}
    assert values == dict(one_at_a_time=1, engagements=2, unseen_engagements=1,
                         time_left_start=70, time_left_first_engagement=65, still_share=.2,
                         planted_in_time=True, defuse_in_time=None, defuse_half=None,
                         unknown_share_start=.8, unknown_share_end=.3, into_unknown_share=.4,
                         backshots=1, located_share=.5, fakes=2, fake_followed=1,
                         held_start=3, used=1, held_at_death=2, ult_ready=True,
                         own_tier=3, enemy_best_tier=1, tier_gap=2, hp_start=125, loadout_gap=2000)
    r.update(side="defense", t_plant=20)
    r["clock"]["defuse_attempts"] = [{"enough_time": True, "reached_half": True}]
    assert measure("planted_in_time")["from"](r) is None
    assert measure("defuse_in_time")["from"](r) is True
    assert measure("defuse_half")["from"](r) is True
    r["fakes"] = {"fakes": 0, "fake_followed": 0}
    assert measure("fake_followed")["from"](r) is None
    r["state"]["enemy_gun_tiers"][6] = None
    assert measure("tier_gap")["from"](r) is None


def test_every_measure_group_table_and_opening_sight_note(tmp_path):
    rows = [row(i, won=i % 2 == 0, value=i % 2) for i in range(10)]
    comparisons = results(rows, [m["name"] for m in stats.MEASURES])
    text = report(tmp_path, rows, comparisons)
    assert text.startswith("The sight lines")
    for fragment in ("walls and smokes", "box heights", "doors", "every row"):
        assert fragment in text.split("\n\n", 1)[0]
    for m in stats.MEASURES:
        for group in stats.GROUPS:
            assert f"### {m['name']} / {group}" in text
    assert "clear_line_isolation /" not in text
    assert text.index("Coverage") < text.index("Situation counts") < text.index("Cutoff reconciliation")
    assert text.index("Cutoff reconciliation") < text.index("### one_at_a_time") < text.index("Calibration")


@pytest.mark.parametrize("model_note", [stats.NO_EXPORT, "toy model"])
def test_report_without_stored_predictions_has_one_no_export_note(tmp_path, model_note):
    rows = [row(p_stored=None)]
    path = stats.write_report(tmp_path, rows, [], {}, [], model_note)
    text = path.read_text(encoding="utf-8")
    assert text.splitlines().count(stats.NO_EXPORT) == 1


def test_no_export_still_has_fit_calibration_and_isolation_examples(tmp_path):
    rows = [row(i, won=i % 2 == 0, value=i % 2) for i in range(20)]
    stats.expected(rows, None)
    text = report(tmp_path, rows)
    assert "stored model was not exported" in text
    assert "residual" not in text.lower()
    assert "### p_fit" in text
    assert "Candidate patterns for the owner" not in text
    isolation = text.split("Isolation examples", 1)[1]
    assert len(re.findall(r"outcome won\b", isolation)) == 5
    assert len(re.findall(r"outcome lost\b", isolation)) == 5
    small = report(tmp_path, rows[:3]).split("Isolation examples", 1)[1]
    assert small.count("/replays/") == 3


def test_isolation_examples_include_rows_without_observed_sight(tmp_path):
    rows = [row(i, won=i % 2 == 0, value=None) for i in range(3)]
    text = report(tmp_path, rows).split("Isolation examples", 1)[1]
    assert text.count("/replays/") == 3


def test_attempts_keep_missing_timing_unknown_and_observed_absence_false():
    r = row(side="defense", t_plant=20)
    r["clock"]["defuse_attempts"] = [{"enough_time": None, "reached_half": False}]
    assert measure("defuse_in_time")["from"](r) is None
    assert measure("defuse_half")["from"](r) is False
    r["clock"]["defuse_attempts"] = []
    assert measure("defuse_in_time")["from"](r) is False
    r["t_plant"] = 60  # an eventual plant does not make entry post-plant
    assert measure("defuse_in_time")["from"](r) is None


def test_privacy_stable_labels_and_csv_allowlist(tmp_path):
    contexts = [
        {"match": {"uuid": "replay-a"}, "players": {
            "0": {"player_id": 30, "name": "A", "agent": "Jett"},
            "1": {"player_id": 20, "name": "B", "agent": "Omen"},
            "2": {"player_id": 10, "name": "C", "agent": "Sage"}}},
        {"match": {"uuid": "replay-b"}, "players": {
            "2": {"player_id": 20, "name": "B", "agent": "Omen"},
            "0": {"player_id": 10, "name": "C", "agent": "Sage"}}},
    ]
    original = copy.deepcopy(contexts)
    labels = stats.labels(contexts, {"a"})
    assert contexts == original
    assert labels == {"replay-a": {0: "A", 1: "player 2", 2: "player 1"},
                      "replay-b": {2: "player 2", 0: "player 1"}}
    rows = [row(name="B", player_id=20, label=labels["replay-a"][1])]
    text = report(tmp_path, rows)
    path = stats.write_csv(tmp_path, rows)
    csv_text = path.read_text(encoding="utf-8")
    assert not re.search(r"\bB\b", text + csv_text)
    data = list(csv.DictReader(csv_text.splitlines()))
    assert "name" not in data[0] and "player_id" not in data[0]
    assert data[0]["label"] == "player 2"
    assert float(data[0]["clear_line_isolation"]) == 0


def test_situation_counts_deduplicate_pairs_and_cutoffs_keep_each_n(tmp_path):
    rows = [row(i, kind="2vN", own_alive=2, n_enemies=3, group="2v3",
                situation_id="pair", slot=i) for i in range(2)]
    cutoffs = [{"situation_id": f"cut-{n}", "team": "team-1", "kind": "1vN",
                "n_enemies": n, "group": "1v3+", "won": False, "t": 40} for n in (3, 4, 5)]
    text = report(tmp_path, rows, cutoffs=cutoffs)
    counts = text.split("Situation counts", 1)[1].split("Cutoff reconciliation", 1)[0]
    assert "| 2v3 | 1 | 2 |" in counts
    reconciled = text.split("Cutoff reconciliation", 1)[1].split("###", 1)[0]
    for n in (3, 4, 5):
        assert f"| 1v{n} | 0 | 1 | 1 |" in reconciled


def test_outputs_refuse_non_temp_paths():
    out = Path(__file__).resolve().parents[2] / "forbidden-clutch-output"
    with pytest.raises(ValueError, match="system temp"):
        stats.write_csv(out, [])
    with pytest.raises(ValueError, match="system temp"):
        stats.write_report(out, [], [], {}, [], "")
    assert not out.exists()
