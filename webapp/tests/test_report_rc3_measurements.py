"""The pure parts of the rc3 measurement report: ranking, correlation, alignment.

These decide declared figures (rank changes, top-player changes, per-match
Spearman), so their tie rules are pinned to what the ledger declared.
"""

import pytest

from scripts import report_rc3_measurements as report
from scripts.export_impact_artifact import COMPARISON_HEADER


def test_impact_order_breaks_ties_by_player_id():
    players = [{"player_id": 9, "impact": 100}, {"player_id": 3, "impact": 100},
               {"player_id": 5, "impact": 200}]
    assert report.order_by_impact(players, "impact") == [5, 3, 9]


def test_average_ranks_share_a_tie():
    assert report._rank_average([10, 20, 20, 5]) == [3.0, 1.5, 1.5, 4.0]


def test_spearman_of_identical_orders_is_one_and_of_reversed_is_minus_one():
    assert report.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert report.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_is_undefined_when_every_value_ties():
    assert report.spearman([5, 5, 5], [1, 2, 3]) is None


def _write(path, rows):
    lines = [",".join(COMPARISON_HEADER)]
    for round_id, match_player_id in rows:
        values = ["0"] * len(COMPARISON_HEADER)
        values[0], values[1] = str(round_id), str(match_player_id)
        values[COMPARISON_HEADER.index("trade_detail")] = ""
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_rows_that_differ_refuses_misaligned_artifacts(tmp_path):
    """Keyed, not positional: a shifted row must stop the comparison rather than
    be compared against its neighbour."""
    _write(tmp_path / "a.csv", [(1, 1), (1, 2)])
    _write(tmp_path / "b.csv", [(1, 1), (1, 3)])
    with pytest.raises(report.ArtifactMismatch):
        report.rows_that_differ(tmp_path / "a.csv", tmp_path / "b.csv")


def test_rows_that_differ_counts_nothing_for_identical_artifacts(tmp_path):
    _write(tmp_path / "a.csv", [(1, 1), (1, 2)])
    _write(tmp_path / "b.csv", [(1, 1), (1, 2)])
    assert report.rows_that_differ(tmp_path / "a.csv", tmp_path / "b.csv") == {"rows": 0, "columns": {}}


def test_a_truncated_artifact_stops_the_report(tmp_path):
    """C5: zip() stopped at the shorter file, so a lost tail was measured as a
    smaller corpus and reported without a word."""
    _write(tmp_path / "on.csv", [(1, 1), (1, 2), (1, 3)])
    _write(tmp_path / "off.csv", [(1, 1), (1, 2)])

    with pytest.raises(report.ArtifactMismatch, match="ends before"):
        report.rows_that_differ(tmp_path / "on.csv", tmp_path / "off.csv")
    with pytest.raises(report.ArtifactMismatch, match="ends before"):
        report.rows_that_differ(tmp_path / "off.csv", tmp_path / "on.csv")


def test_an_artifact_carries_its_own_hash_and_what_it_was_exported_from(tmp_path):
    import json

    _write(tmp_path / "on.csv", [(1, 1)])
    (tmp_path / "on.json").write_text(json.dumps({
        "artifact": {"sha256": "recorded"},
        "configuration": {"enable_trade_credit": True},
        "inputs": {"database": "valowithfriendsdb", "counts": {"matches": 3125},
                   "cohort_fingerprint": "abc"},
    }), encoding="utf-8")

    identity = report._artifact_identity(tmp_path / "on.csv")

    assert identity["file"] == "on.csv"
    assert identity["sha256"] != identity["recorded_sha256"] == "recorded", "the file's own bytes"
    assert identity["inputs"] == {"database": "valowithfriendsdb", "counts": {"matches": 3125},
                                  "cohort_fingerprint": "abc"}


def test_measuring_against_a_database_that_has_moved_is_refused():
    """The mappings are read now and joined to scores exported earlier."""
    class _Db:
        def execute(self, _statement):
            class _Result:
                def scalar(self_inner):
                    return 4000
            return _Result()

    identities = {"on": {"inputs": {"counts": {"matches": 3125, "round_player_stats": 659500,
                                               "kill_events": 487993}}}}
    with pytest.raises(report.ArtifactMismatch, match="would not describe the same rows"):
        report._require_matching_inputs(_Db(), identities)
