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
    assert verdicts.count("OK") >= 30 and verdicts.count("WAITS") == 4
    for row in ("two opposing same-agent slots made kill-free, spawn evidence crossed",
                "a mid-match dormant close and reopen", "one phase 4 removed",
                "a kill by a player after their \"left\" close", "the DB's last k rounds deleted, score unchanged"):
        assert any(line.startswith(row) for line in rows), row
