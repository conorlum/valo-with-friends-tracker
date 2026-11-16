"""Waiting-agent messages carry the replay's agent code without matching other holds."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.replays.contract import waiting_agent_code, waiting_agent_text  # noqa: E402
from replay_worker.server import held_reason, parser_refused_reason  # noqa: E402


@pytest.mark.parametrize("code", ["Paprika", "Foo_Bar"])
def test_waiting_agent_code_round_trips(code):
    text = waiting_agent_text(code)
    assert text == (
        f"waiting for support for a new agent (code name {code}): the file is kept "
        "and will be parsed once the site supports it"
    )
    assert waiting_agent_code(text) == code


@pytest.mark.parametrize("text", [
    None,
    "parse failed",
    held_reason("++Ares-Core+release-99.99"),
    parser_refused_reason("x"),
])
def test_other_reasons_are_not_waiting_agent_text(text):
    assert waiting_agent_code(text) is None


@pytest.mark.parametrize("prefix, suffix", [("extra ", ""), ("", " extra"), ("", "\n")])
def test_waiting_agent_code_requires_the_whole_message(prefix, suffix):
    assert waiting_agent_code(prefix + waiting_agent_text("Paprika") + suffix) is None
