"""The executable 1b gate harness: every synthetic row is at its stated expected outcome."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import replay_gate  # noqa: E402


def test_every_synthetic_gate_row_is_at_its_expected_outcome(tmp_path, capsys):
    code = replay_gate.main(["--work", str(tmp_path)])
    printed = capsys.readouterr().out
    assert code == 0, printed
    rows = [line for line in printed.splitlines() if line.count(" | ") == 4]
    verdicts = [line.rsplit(" | ", 1)[1].strip() for line in rows]
    assert "MISMATCH" not in verdicts
    assert verdicts.count("OK") >= 55 and verdicts.count("WAITS") == 4 and "N/A" not in verdicts
    for row in ("two opposing same-agent slots made kill-free, spawn evidence crossed",
                "a mid-match dormant close and reopen", "one phase 4 removed",
                "a kill by a player after their \"left\" close", "the DB's last k rounds deleted, score unchanged"):
        assert any(line.startswith(row) for line in rows), row
    # The rows --condensed --db runs on real replays, driven on the synthetic match.
    generic = [line for line in rows if "synthetic, generic rows" in line]
    assert len(generic) == 24 and all(line.rstrip().endswith("OK") for line in generic), generic


def test_real_rows_use_the_loader_and_report_a_missing_match(tmp_path, monkeypatch):
    """--condensed --db's path, with a stub loader instead of a database."""
    import pickle

    base = replay_gate.synthetic_replays(tmp_path)["base"]
    path = tmp_path / "condensed.pickle"
    path.write_bytes(pickle.dumps((base, None)))
    lines = []

    def record(row, variant, expected, outcome, ok, verdict=None):
        lines.append((row, verdict or ("OK" if ok else "MISMATCH")))

    replay_gate.real_link_rows([path], record, lambda uuid: [replay_gate.db_match()])
    # One replay has no second match to be "the wrong match": that row is N/A, never OK.
    assert [row for row, v in lines if v != "OK"] == ["the wrong match"] * 2, lines
    lines.clear()
    replay_gate.real_link_rows([path], record, lambda uuid: [])
    assert lines == [("the real replay has exactly one DB match", "MISMATCH")]
