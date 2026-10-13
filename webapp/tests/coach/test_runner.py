import json
import sys

import pytest

from app.coach import query, runner, snapshot
from app.coach.playbook import Playbook
from scripts import coach_report
from tests.coach import toys
from tests.coach.test_snapshot import CONTEXT

BOOK = Playbook("4", ("trading", "rotation"), "# Principles\n- Trade.")
KNEW = {"A": {"5": [[5.0, 8.0, 8000, 8000]]}, "B": {}}
SHARES = {"t": [0.0], "cells": {}, "areas": {}, "knew": KNEW, "group_side": None}


def make_data():
    blobs = {1: toys.round_blob({**toys.ten(), 0: (5000, 5000)}, kills=[{"t": 20.0, "killer": 5, "victim": 0}], n=1)}
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]},
               "rounds": {"1": {"db": {"winner": "team2"}, "stats": {"0": {"impact": -250}}, "kills": {}}}}
    return snapshot.assemble(context, blobs, {1: ("ok", False, SHARES)})


@pytest.fixture
def snap(tmp_path):
    snapshot.save(make_data(), tmp_path)
    return snapshot.load(tmp_path)


def good_point(evidence):
    return {"polarity": "good", "theme": "trading", "title": "Held your ground", "body": "You stayed alive early.",
            "instances": [{"round": 1, "t": 10.0, "caption": "early round", "evidence": evidence}]}


def bad_point():
    return {"polarity": "good", "theme": "trading", "title": "Made up", "body": "You were 35 m away.",
            "instances": [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q999"]}]}


class Model:
    """Answers each call from a script; on the first call it also asks one query, as the real model would."""

    def __init__(self, snap, run_dir, replies):
        self.snap, self.run_dir, self.replies, self.prompts = snap, run_dir, list(replies), []

    def __call__(self, text, session):
        self.prompts.append((text, session))
        if len(self.prompts) == 1:
            self.asked = query.run(self.snap, self.run_dir, "where", ["1", "10"])["id"]
        reply = self.replies.pop(0)
        return (reply(self.asked) if callable(reply) else reply), "session-1"


def run(snap, tmp_path, replies):
    model = Model(snap, tmp_path, replies)
    return runner.generate(snap, 0, tmp_path, BOOK, [], model, "test-model"), model


def test_the_flags_are_logged_first_and_shown_with_their_ids(snap, tmp_path):
    payload, model = run(snap, tmp_path, [lambda q: json.dumps({"points": [good_point([q])]})])
    log = query.read_log(tmp_path)
    assert log["q1"]["cmd"] == "flag"
    assert '"id": "q1"' in model.prompts[0][0] or '"id":"q1"' in model.prompts[0][0]


def test_a_checked_report_becomes_a_storable_payload(snap, tmp_path):
    payload, _ = run(snap, tmp_path, [lambda q: "Here it is:\n```json\n" + json.dumps({"points": [good_point([q])]}) + "\n```"])
    assert payload["playbook_version"] == "4" and payload["model"] == "test-model" and payload["dropped_points"] == 0
    assert [p["title"] for p in payload["points"]] == ["Held your ground"]
    from app.services import coach
    coach.validate_payload(payload)


def test_a_failed_point_gets_one_repair_and_is_kept_when_fixed(snap, tmp_path):
    payload, model = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q]), bad_point()]}),
        lambda q: json.dumps({"points": [{"index": 1, **good_point([q]), "title": "Fixed"}]})])
    assert [p["title"] for p in payload["points"]] == ["Held your ground", "Fixed"] and payload["dropped_points"] == 0
    assert model.prompts[1][1] == "session-1" and "q999" in model.prompts[1][0]


def test_a_repair_reply_with_the_index_as_a_string_is_still_matched(snap, tmp_path):
    payload, _ = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q]), bad_point()]}),
        lambda q: json.dumps({"points": [{**good_point([q]), "index": "1", "title": "Fixed"}]})])
    assert [p["title"] for p in payload["points"]] == ["Held your ground", "Fixed"] and payload["dropped_points"] == 0


def test_a_point_that_fails_twice_is_dropped_and_counted(snap, tmp_path):
    payload, _ = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q]), bad_point()]}),
        lambda q: json.dumps({"points": [{"index": 1, **bad_point()}]})])
    assert [p["title"] for p in payload["points"]] == ["Held your ground"] and payload["dropped_points"] == 1


def test_no_json_is_asked_for_once_and_then_the_run_fails(snap, tmp_path):
    with pytest.raises(runner.RunError, match="no report"):
        run(snap, tmp_path, ["I could not decide.", "Still thinking."])


def test_more_than_three_points_a_side_are_cut_to_three(snap, tmp_path):
    payload, _ = run(snap, tmp_path, [lambda q: json.dumps({"points": [good_point([q]) for _ in range(5)]})])
    assert len(payload["points"]) == 3


def test_extract_points_reads_bare_fenced_and_embedded_json():
    body = json.dumps({"points": [{"title": "x"}]})
    for text in (body, f"```json\n{body}\n```", f"Report follows.\n{body}\nDone."):
        assert runner.extract_points(text) == [{"title": "x"}]
    assert runner.extract_points("nothing here") is None and runner.extract_points('{"other": 1}') is None


def test_an_answer_edited_in_the_log_file_is_not_evidence(snap, tmp_path):
    def forged(q):                                         # the model's own answer, rewritten on disk after the fact
        path = tmp_path / query.LOG
        text = path.read_text(encoding="utf-8")
        assert '"alive":true' in text
        path.write_text(text.replace('"alive":true', '"alive":false'), encoding="utf-8")
        return json.dumps({"points": [good_point([q])]})

    with pytest.raises(runner.RunError, match="no point passed"):
        run(snap, tmp_path, [forged, lambda q: json.dumps({"points": [{"index": 0, **good_point([q])}]})])


def test_a_flag_entry_the_runner_did_not_write_is_not_evidence(snap, tmp_path):
    def planted(q):
        fake = query.append(tmp_path, "flag", [1, 10.0, "made_up"], {"round": 1, "t": 10.0, "metres": 3.0})
        return json.dumps({"points": [{**good_point([fake]), "body": "You were 3 m away."}]})

    with pytest.raises(runner.RunError, match="no point passed"):
        run(snap, tmp_path, [planted, "no fix"])


def test_a_report_the_store_would_refuse_fails_the_run_instead(snap, tmp_path, monkeypatch):
    from app.services import coach

    def refuse(payload):
        raise coach.ReportError("points[0].title is too long")

    monkeypatch.setattr(coach, "validate_payload", refuse)
    with pytest.raises(runner.RunError, match="cannot be stored"):
        run(snap, tmp_path, [lambda q: json.dumps({"points": [good_point([q])]})])


def test_the_model_gets_one_tool_one_command_and_none_of_the_owners_setup():
    command = coach_report.claude_command("claude", None, None)
    assert command[:4] == ["claude", "-p", "--output-format", "json"]
    for flag in ("--restricted", "--safe-mode", "--strict-mcp-config"):
        assert flag in command
    assert command[command.index("--tools") + 1] == "Bash"
    assert command[command.index("--allowedTools") + 1] == "Bash(./q:*)"
    assert not any("skip" in part or "bypass" in part.lower() for part in command)
    resumed = coach_report.claude_command("claude", "opus", "session-1")
    assert resumed[-4:] == ["--model", "opus", "--resume", "session-1"]


def test_the_models_environment_is_an_allow_list():
    env = coach_report.child_env({"DATABASE_URL": "postgresql://secret", "database_url": "y", "PATH": "x",
                                  "ANTHROPIC_API_KEY": "k", "MY_SECRET": "s", "CLAUDE_CONFIG_DIR": "c",
                                  "SystemRoot": "r", "ProgramFiles(x86)": "p"})
    assert env == {"PATH": "x", "CLAUDE_CONFIG_DIR": "c", "SystemRoot": "r", "ProgramFiles(x86)": "p"}


def test_the_q_script_runs_the_query_tool_against_this_run(tmp_path):
    path = coach_report.write_q(tmp_path, tmp_path / "python.exe")
    text = path.read_text(encoding="utf-8")
    assert path.name == "q" and "coach_query.py" in text and '--run' in text and '"$@"' in text and "\\" not in text
    assert path == tmp_path / "model" / "q"
    assert [p.name for p in (tmp_path / "model").iterdir()] == ["q"]
    assert f'--run "{tmp_path.resolve().as_posix()}"' in text


def test_the_model_runs_in_the_folder_that_holds_only_q(tmp_path, monkeypatch):
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, json.dumps({"result": "hi", "session_id": "s"}), ""

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        return Done()

    monkeypatch.setattr(coach_report.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(coach_report.subprocess, "run", fake_run)
    (tmp_path / "model").mkdir()
    assert coach_report.claude_call(tmp_path, None)("x", None) == ("hi", "s")
    assert seen["cwd"] == tmp_path / "model"


# --- main(): the command itself, with the database and the model replaced ---

class _Db:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _wire(monkeypatch, tmp_path, argv, data):
    import app.db
    from app.coach import playbook
    from app.services import coach
    from scripts import coach_store_report

    monkeypatch.setattr(sys, "argv", ["coach_report.py", *argv])
    monkeypatch.setattr(app.db, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(playbook, "load", lambda: BOOK)
    monkeypatch.setattr(coach_store_report, "resolve", lambda db, uuid, player: (7, 8))
    monkeypatch.setattr(snapshot, "read", lambda db, uuid: data)
    monkeypatch.setattr(coach, "active_notes", lambda db, kind: ["Plain words."])
    monkeypatch.setattr(coach_report.tempfile, "gettempdir", lambda: str(tmp_path))

    def never(*args, **kwargs):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(coach_report, "claude_call", never)


def test_prompt_only_writes_the_first_prompt_and_never_calls_the_model(tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, tmp_path, ["ABCDEF12-0000-4000-8000-000000000000", "0", "--prompt-only"], make_data())
    assert coach_report.main() == 0
    path = tmp_path / "valo-coach" / "abcdef12-0000-4000-8000-000000000000-p8" / "prompt.txt"
    text = path.read_text(encoding="utf-8")
    assert "Plain words." in text and "./q where" in text and "Trade." in text
    out = capsys.readouterr().out
    assert str(path) in out and str(len(text)) in out


def test_a_player_not_in_the_snapshot_is_refused_with_2(tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, tmp_path, ["abcdef12-0000-4000-8000-000000000000", "nobody", "--prompt-only"], make_data())
    assert coach_report.main() == 2
    assert "refused" in capsys.readouterr().err
