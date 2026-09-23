"""The declaration 13 equivalence checker must be able to FAIL.

External review of the v4 branch, findings 1 and 2: the checker read the
reference CSV into a dict (silently collapsing a duplicate key), took the
reference hash from the sidecar instead of hashing the file it compared, and
exited 0 whatever it found. A corrupted reference could therefore produce an
entirely clean report, and a command chain relying on the exit status would
have carried on past declaration 13's stop rule. These tests corrupt the
reference and the implementation in each of those ways.
"""
import dataclasses
import hashlib
import json
from collections import Counter

import pytest

from app.scoring.impact import CalculatedImpact
from scripts import compare_v4_reference as checker

FIELDS = [f.name for f in dataclasses.fields(CalculatedImpact)]
IDS = [1, 2]


def _line(round_id, match_player_id, impact=100, scoring_version=3):
    row = CalculatedImpact(round_id=round_id, match_player_id=match_player_id, kill_impact=impact,
                           death_impact=0, impact=impact, damage=impact, econ_impact=0,
                           time_impact=0, swing_impact=0, econ_kill=0, econ_death=0, clutch_kill=0,
                           clutch_death=0, post_plant_kill=0, post_plant_death=0, traded_teammate=0,
                           traded_by_teammate=0, trade_detail=None, scoring_version=scoring_version)
    return (round_id, match_player_id), checker._line(row, FIELDS)


GOOD = [_line(10, 1), _line(10, 2), _line(11, 1)]


def _write_reference(tmp_path, lines, *, recorded_rows=None, recorded_sha=None):
    data = (",".join(FIELDS) + "\n" + "".join(line for _, line in lines)).encode("utf-8")
    path = tmp_path / "ref_P0_exante.csv"
    path.write_bytes(data)
    sidecar = {
        "revision": "c470670", "fields": FIELDS,
        "match_id_list_sha256": hashlib.sha256(checker._canonical_json(IDS).encode()).hexdigest(),
        "artifacts": {"ref_P0_exante.csv": {
            "rows": len(lines) if recorded_rows is None else recorded_rows,
            "sha256": recorded_sha or hashlib.sha256(data).hexdigest()}},
    }
    (tmp_path / "reference_sidecar.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return path, sidecar["artifacts"]["ref_P0_exante.csv"]


# -- the reference file is proved, not trusted ------------------------------------------

def test_a_reference_whose_bytes_are_not_the_pinned_ones_is_refused(tmp_path):
    path, pinned = _write_reference(tmp_path, GOOD)
    path.write_bytes(path.read_bytes().replace(b",100,", b",101,", 1))   # edited after pinning
    with pytest.raises(SystemExit, match="sha256"):
        checker.load_reference(path, pinned, FIELDS)


def test_a_duplicate_reference_key_is_refused_even_when_its_hash_is_pinned(tmp_path):
    # The review's case: a conflicting duplicate BEFORE the correct row. A dict
    # keeps the last one, so the corruption was invisible to the comparison.
    duplicated = [_line(10, 1, impact=999)] + GOOD
    path, pinned = _write_reference(tmp_path, duplicated)
    with pytest.raises(SystemExit, match="duplicate"):
        checker.load_reference(path, pinned, FIELDS)


def test_a_reference_row_count_that_is_not_the_pinned_one_is_refused(tmp_path):
    path, pinned = _write_reference(tmp_path, GOOD, recorded_rows=len(GOOD) + 1)
    with pytest.raises(SystemExit, match="rows"):
        checker.load_reference(path, pinned, FIELDS)


def test_a_clean_reference_loads(tmp_path):
    path, pinned = _write_reference(tmp_path, GOOD)
    header, rows, sha = checker.load_reference(path, pinned, FIELDS)
    assert header == FIELDS and len(rows) == 3 and sha == pinned["sha256"]


# -- the verdict, and the exit status through the command -------------------------------

class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def __iter__(self):
        return iter(self._rows)

    def scalar(self):
        return self._scalar


class _FakeSession:
    def execute(self, statement, *args, **kwargs):
        sql = str(statement)
        if "current_database" in sql:
            return _Result(scalar="valo_v4")
        if "FROM matches" in sql:
            return _Result(rows=[(i,) for i in IDS])
        return _Result()

    def rollback(self):
        pass

    def commit(self):
        pass

    def close(self):
        pass


def _run(tmp_path, monkeypatch, impl_lines, *expect):
    reference = tmp_path / "ref"
    reference.mkdir()
    _write_reference(reference, GOOD)
    monkeypatch.setattr(checker, "SessionLocal", _FakeSession)
    monkeypatch.setattr(checker, "replay", lambda db, ids, pairs, fields: (
        {("P0", "exante"): list(impl_lines)}, {("P0", "exante"): Counter()}))
    out = tmp_path / "out"
    code = checker.main(["--reference", str(reference), "--out", str(out), "--pairs", "P0:exante",
                         "--label", "t", *expect])
    return code, json.loads((out / "t.json").read_text(encoding="utf-8"))


def test_a_clean_comparison_exits_zero(tmp_path, monkeypatch):
    code, report = _run(tmp_path, monkeypatch, GOOD, "--expect-scoring-version", "3")
    assert code == 0
    assert report["problems"] == []


def test_a_differing_score_exits_nonzero(tmp_path, monkeypatch):
    changed = [_line(10, 1, impact=101)] + GOOD[1:]
    code, report = _run(tmp_path, monkeypatch, changed, "--expect-scoring-version", "3")
    assert code != 0
    assert any("differ" in p for p in report["problems"])


def test_a_key_on_one_side_only_exits_nonzero(tmp_path, monkeypatch):
    code, report = _run(tmp_path, monkeypatch, GOOD[:2], "--expect-scoring-version", "3")
    assert code != 0
    assert any("only" in p for p in report["problems"])


def test_an_unexpected_scoring_version_exits_nonzero_even_with_equal_scores(tmp_path, monkeypatch):
    # The review's case: every score equal, but version 4 against an expected 3.
    bumped = [_line(r, m, scoring_version=4) for (r, m), _ in GOOD]
    code, report = _run(tmp_path, monkeypatch, bumped, "--expect-scoring-version", "3")
    assert code != 0
    assert report["pairs"]["P0:exante"]["rows_differing"] == 0
    assert any("scoring_version" in p for p in report["problems"])


def test_the_expected_version_is_required(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit):
        checker.main(["--reference", str(tmp_path), "--out", str(tmp_path / "o")])
    assert "--expect-scoring-version" in capsys.readouterr().err
