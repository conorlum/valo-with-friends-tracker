import json
import sys

import pytest

from app.coach import compact, prompt, query, runner, snapshot
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
        reply = self.replies.pop(0) if self.replies else "Nothing to add."      # the ask for a third point
        return (reply(self.asked) if callable(reply) else reply), "session-1"


def run(snap, tmp_path, replies):
    model = Model(snap, tmp_path, replies)
    return runner.generate(snap, 0, tmp_path, BOOK, model, "test-model"), model


def test_the_flags_are_logged_first_and_shown_with_their_ids(snap, tmp_path):
    payload, model = run(snap, tmp_path, [lambda q: json.dumps({"points": [good_point([q])]})])
    log = query.read_log(tmp_path)
    assert log["q1"]["cmd"] == "flag"
    rendered = model.prompts[0][0].split("# The brief\n\n", 1)[1].split("\n\n# What to return", 1)[0]
    assert any(flag["id"] == "q1" for r in compact.parse(rendered)["rounds"] for flag in r["flags"])


def test_the_compact_prompt_keeps_every_runner_evidence_id(snap, tmp_path, monkeypatch):
    from tests.coach.test_compact import drop_nulls

    monkeypatch.setattr(snap, "util", lambda n: {
        "casts": [{"slot": 0, "t": 67.35, "ability": "Fakeout", "kind": "device", "value": 0.0,
                   "trigger": 77.02, "presence": {"life_s": 5.97}}],
        "unused": [{"slot": 0, "t": 69.25, "kind": "held_at_death", "abilities": ["Blindside"]}]})
    brief, logged = runner._brief_with_flag_ids(snap, 0, tmp_path)
    text = prompt.run_prompt(BOOK, brief)
    rendered = text.split("# The brief\n\n", 1)[1].split("\n\n# What to return", 1)[0]
    parsed = compact.parse(rendered)
    assert json.dumps(parsed, sort_keys=True) == json.dumps(drop_nulls(brief), sort_keys=True)
    flag_ids = [f["id"] for r in parsed["rounds"] for f in r["flags"]]
    facts_ids = [r["facts_id"] for r in parsed["rounds"] if "facts_id" in r]
    util_ids = [f["id"] for r in parsed["rounds"] for f in r["util_facts"]]
    assert flag_ids and facts_ids and util_ids
    assert set(flag_ids + facts_ids + util_ids) == set(logged)
    assert all(evidence_id in rendered for evidence_id in logged)


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


def test_number_misses_never_change_the_repair_prompt(snap, tmp_path):
    replies = [lambda q: json.dumps({"points": [good_point([q]), bad_point()]}), "no fix"]
    baseline = Model(snap, tmp_path / "baseline", replies)
    original = runner.generate(snap, 0, tmp_path / "baseline", BOOK, baseline, "test-model")
    misses = []
    tracked = Model(snap, tmp_path / "tracked", replies)
    result = runner.generate(snap, 0, tmp_path / "tracked", BOOK, tracked, "test-model", misses=misses)
    assert result == original
    assert tracked.prompts[1][0].encode() == baseline.prompts[1][0].encode()
    assert misses == [{"stage": "first", "point": 1, "figure": "35", "kind": "metres",
                       "nearest": None, "gap": None}]


@pytest.mark.parametrize("fixed_index", [1, 4])
def test_number_misses_keep_sparse_original_and_appended_indices(snap, tmp_path, fixed_index):
    def first(q):
        points = [good_point([q]) for _ in range(6)]
        points[1], points[4] = bad_point(), bad_point()
        return json.dumps({"points": points})

    repair = {"points": [{"index": fixed_index, **bad_point()}, {"index": 20, **bad_point()}]}
    model = Model(snap, tmp_path, [first, json.dumps(repair)])
    misses = []
    runner.generate(snap, 0, tmp_path, BOOK, model, "test-model", misses=misses)
    assert [(m["stage"], m["point"]) for m in misses] == [
        ("first", 1), ("first", 4), ("repair", fixed_index), ("repair", 6)]
    for record in misses:
        assert set(record) == {"stage", "point", "figure", "kind", "nearest", "gap"}
        assert record["figure"] == "35" and record["kind"] == "metres"
        assert record["nearest"] is None and record["gap"] is None


def test_number_misses_survive_a_run_error_after_the_first_check(snap, tmp_path):
    model = Model(snap, tmp_path, [json.dumps({"points": [bad_point()]})])
    misses = []
    with pytest.raises(runner.RunError, match="session"):
        runner.generate(snap, 0, tmp_path, BOOK, lambda text, session: (model(text, session)[0], None),
                        "test-model", misses=misses)
    assert misses == [{"stage": "first", "point": 0, "figure": "35", "kind": "metres",
                       "nearest": None, "gap": None}]


def test_a_repair_reply_with_the_index_as_a_string_is_still_matched(snap, tmp_path):
    payload, _ = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q]), bad_point()]}),
        lambda q: json.dumps({"points": [{**good_point([q]), "index": "1", "title": "Fixed"}]})])
    assert [p["title"] for p in payload["points"]] == ["Held your ground", "Fixed"] and payload["dropped_points"] == 0


def test_a_point_that_fails_twice_is_still_published_and_listed_as_unchecked(snap, tmp_path):
    payload, _ = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q]), bad_point()]}),
        lambda q: json.dumps({"points": [{"index": 1, **bad_point()}]})])
    assert [p["title"] for p in payload["points"]] == ["Held your ground", "Made up"]
    assert payload["dropped_points"] == 0
    (weak,) = payload["unchecked"]
    assert weak["title"] == "Made up" and any("q999" in p for p in weak["problems"])
    from app.services import coach
    coach.validate_payload(payload)


def test_a_point_that_cannot_be_stored_is_dropped_and_counted(snap, tmp_path):
    nowhere = {**bad_point(), "instances": [{"round": 99, "t": 10.0, "caption": "", "evidence": []}]}
    payload, _ = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q]), nowhere]}),
        lambda q: json.dumps({"points": [{"index": 1, **nowhere}]})])
    assert [p["title"] for p in payload["points"]] == ["Held your ground"]
    assert payload["dropped_points"] == 1 and payload["unchecked"] == []


def test_a_side_that_comes_back_short_is_asked_for_more(snap, tmp_path):
    payload, model = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q])]}),
        lambda q: json.dumps({"points": [{"index": 1, **good_point([q]), "title": "Second"},
                                         {"index": 2, **bad_point(), "title": "Weak third"}]})])
    ask = model.prompts[1][0]
    assert '2 more "good"' in ask and '3 more "improve"' in ask and "starting at 1" in ask
    assert [p["title"] for p in payload["points"]] == ["Held your ground", "Second", "Weak third"]
    assert [w["title"] for w in payload["unchecked"]] == ["Weak third"]


def test_checked_points_are_chosen_before_unchecked_ones(snap, tmp_path):
    payload, _ = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [bad_point()] + [good_point([q]) for _ in range(3)]}), "no fix"])
    assert [p["title"] for p in payload["points"]] == ["Held your ground"] * 3 and payload["unchecked"] == []


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

    payload, _ = run(snap, tmp_path, [forged, lambda q: json.dumps({"points": [{"index": 0, **good_point([q])}]})])
    (weak,) = payload["unchecked"]                         # published as the owner asked, but never as checked
    assert any("could not be reproduced" in p for p in weak["problems"])


def test_a_flag_entry_the_runner_did_not_write_is_not_evidence(snap, tmp_path):
    def planted(q):
        fake = query.append(tmp_path, "flag", [1, 10.0, "made_up"], {"round": 1, "t": 10.0, "metres": 3.0})
        return json.dumps({"points": [{**good_point([fake]), "body": "You were 3 m away."}]})

    payload, _ = run(snap, tmp_path, [planted, "no fix"])
    (weak,) = payload["unchecked"]
    assert any("could not be reproduced" in p for p in weak["problems"])


def test_a_run_with_nothing_storable_fails(snap, tmp_path):
    with pytest.raises(runner.RunError, match="no point could be stored"):
        run(snap, tmp_path, [json.dumps({"points": ["junk"]}), "no fix"])


def test_a_report_the_store_would_refuse_fails_the_run_instead(snap, tmp_path, monkeypatch):
    from app.services import coach

    def refuse(payload):
        raise coach.ReportError("points[0].title is too long")

    monkeypatch.setattr(coach, "validate_payload", refuse)
    with pytest.raises(runner.RunError, match="cannot be stored"):
        run(snap, tmp_path, [lambda q: json.dumps({"points": [good_point([q])]})])


@pytest.mark.parametrize("options, expected", [
    ([], "claude-opus-5-5"), (["--model", "x"], "x"), (["--model", ""], ""),
])
def test_the_report_parser_selects_and_passes_the_model(options, expected):
    args = coach_report.build_parser().parse_args(["abcdef12-0000-4000-8000-000000000000", "0", *options])
    assert args.model == expected
    command = coach_report.claude_command("claude", args.model, None)
    if expected:
        assert command[-2:] == ["--model", expected]
    else:
        assert "--model" not in command


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
                                  "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_CODE_SESSION_ID": "i",
                                  "SystemRoot": "r", "ProgramFiles(x86)": "p"})
    assert env == {"PATH": "x", "CLAUDE_CONFIG_DIR": "c", "SystemRoot": "r", "ProgramFiles(x86)": "p"}


def test_the_q_script_runs_the_query_tool_against_this_run(tmp_path):
    path = coach_report.write_q(tmp_path, tmp_path / "python.exe")
    text = path.read_text(encoding="utf-8")
    assert path.name == "q" and "coach_query.py" in text and '--run' in text and '"$@"' in text and "\\" not in text
    assert path == tmp_path / "model" / "q"
    assert [p.name for p in (tmp_path / "model").iterdir()] == ["q"]
    assert f'--run "{tmp_path.resolve().as_posix()}" --compact --' in text
    assert f'exec "{(tmp_path / "python.exe").as_posix()}" -X utf8 ' in text


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


def _cli_out(cost, turns, tokens, model="claude-x"):
    return {"result": "hi", "session_id": "s", "total_cost_usd": cost, "num_turns": turns, "duration_ms": 60000,
            "usage": {"input_tokens": tokens, "output_tokens": 10, "cache_read_input_tokens": 100,
                      "cache_creation_input_tokens": 5},
            "modelUsage": {model: {"costUSD": cost, "inputTokens": tokens, "outputTokens": 10,
                                   "cacheReadInputTokens": 100, "cacheCreationInputTokens": 5}}}


def test_usage_is_summed_over_every_call_of_a_run(tmp_path, monkeypatch):
    outs = iter([_cli_out(0.5, 12, 1000), _cli_out(0.25, 3, 200)])

    class Done:
        returncode, stderr = 0, ""

        def __init__(self):
            self.stdout = json.dumps(next(outs))

    monkeypatch.setattr(coach_report.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(coach_report.subprocess, "run", lambda command, **kw: Done())
    (tmp_path / "model").mkdir()
    call = coach_report.claude_call(tmp_path, None)
    call("first", None)
    call("repair", "s")
    usage = call.usage
    assert (usage["calls"], usage["num_turns"], usage["input_tokens"], usage["output_tokens"]) == (2, 15, 1200, 20)
    assert usage["cache_read_input_tokens"] == 200 and usage["duration_ms"] == 120000
    assert usage["cost_usd"] == pytest.approx(0.75)
    assert usage["by_model"]["claude-x"]["cost_usd"] == pytest.approx(0.75)
    assert usage["by_model"]["claude-x"]["input_tokens"] == 1200
    line = coach_report.usage_line(usage)
    assert line.startswith("0.00M tokens") and "15 turns" in line and "$" not in line
    big = {**coach_report.new_usage(), "input_tokens": 1_500_000, "output_tokens": 250_000}
    assert coach_report.usage_line(big).startswith("1.75M tokens: 1.50M input") and "0.25M output" in \
        coach_report.usage_line(big)


def test_a_failed_call_says_why_from_stdout_and_counts_its_usage(tmp_path, monkeypatch):
    class Done:
        returncode, stderr = 1, ""
        stdout = json.dumps({**_cli_out(0.1, 1, 5), "subtype": "error_during_execution", "is_error": True,
                             "api_error_status": 429, "result": "rate limited"})

    monkeypatch.setattr(coach_report.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(coach_report.subprocess, "run", lambda command, **kw: Done())
    (tmp_path / "model").mkdir()
    call = coach_report.claude_call(tmp_path, None)
    with pytest.raises(RuntimeError, match="api_error_status=429.*rate limited"):
        call("x", None)
    assert call.usage["calls"] == 1 and call.usage["cost_usd"] == pytest.approx(0.1)


def test_a_failed_call_with_plain_output_shows_it():
    class Done:
        returncode, stderr, stdout = 1, "", "Credit balance is too low"

    assert coach_report.failure_reason(Done(), coach_report.new_usage()) == "Credit balance is too low"
    Done.stdout = ""
    assert coach_report.failure_reason(Done(), coach_report.new_usage()) == "no output"


def test_odd_usage_fields_count_zero():
    usage = coach_report.add_usage(coach_report.new_usage(), {"total_cost_usd": None, "usage": "x",
                                                               "num_turns": True, "modelUsage": {"m": "x"}})
    assert usage["calls"] == 1 and usage["cost_usd"] == 0 and usage["num_turns"] == 0 and usage["by_model"] == {}


def test_usage_is_merged_and_recorded(tmp_path):
    one = coach_report.add_usage(coach_report.new_usage(), _cli_out(0.5, 2, 10))
    two = coach_report.add_usage(coach_report.new_usage(), _cli_out(0.25, 1, 5, model="claude-y"))
    total = coach_report.merge_usage(coach_report.merge_usage(coach_report.new_usage(), one), two)
    assert total["calls"] == 2 and total["cost_usd"] == pytest.approx(0.75) and set(total["by_model"]) == {
        "claude-x", "claude-y"}
    log = tmp_path / "logs" / "usage.jsonl"
    coach_report.record_usage(tmp_path, {"player": "A", "usage": one}, log)
    coach_report.record_usage(tmp_path, {"player": "B", "usage": two}, log)
    assert [json.loads(line)["player"] for line in log.read_text(encoding="utf-8").splitlines()] == ["A", "B"]
    assert json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))["player"] == "B"


@pytest.mark.parametrize("failure", ["timeout", "stderr", "plain", "malformed"])
def test_run_player_records_calls_without_readable_usage(tmp_path, monkeypatch, capsys, failure):
    make_call = coach_report.claude_call
    data = make_data()
    _wire(monkeypatch, tmp_path, [], data)
    monkeypatch.setattr(coach_report, "claude_call", make_call)
    logged = []
    attempted = []

    def failed_run(command, **kwargs):
        attempted.append(command)
        if failure == "timeout":
            raise coach_report.subprocess.TimeoutExpired(command, kwargs["timeout"])
        return coach_report.subprocess.CompletedProcess(
            command, 0 if failure == "malformed" else 1,
            stdout={"stderr": "", "plain": "service unavailable", "malformed": '{"result":'}[failure],
            stderr="service unavailable" if failure == "stderr" else "")

    monkeypatch.setattr(coach_report.subprocess, "run", failed_run)
    monkeypatch.setattr(coach_report, "record_usage", lambda run_dir, entry: logged.append(entry))
    args = coach_report.build_parser().parse_args(["synthetic-match", "0", "--dry-run"])
    args.usage_total = coach_report.new_usage()
    assert coach_report.run_player(args, args.match_uuid, BOOK, "0", 7, 100, data) == 1
    assert len(attempted) == len(logged) == 1
    entry = logged[0]
    assert (entry["attempts"], entry["usage_missing"], entry["outcome"]) == (1, 1, "failed")
    assert entry["usage"] == args.usage_total == coach_report.new_usage()
    assert entry["number_misses"] == []
    assert "usage: none read from 1 failed call(s)" in capsys.readouterr().out


def test_run_player_records_a_repair_timeout_and_keeps_first_call_usage(tmp_path, monkeypatch, capsys):
    make_call = coach_report.claude_call
    data = make_data()
    _wire(monkeypatch, tmp_path, [], data)
    monkeypatch.setattr(coach_report, "claude_call", make_call)
    logged = []
    attempted = []

    def repair_times_out(command, **kwargs):
        attempted.append(command)
        if len(attempted) == 2:
            raise coach_report.subprocess.TimeoutExpired(command, kwargs["timeout"])
        out = {**_cli_out(0.4, 7, 50), "result": json.dumps({"points": [bad_point()]})}
        return coach_report.subprocess.CompletedProcess(command, 0, stdout=json.dumps(out), stderr="")

    monkeypatch.setattr(coach_report.subprocess, "run", repair_times_out)
    monkeypatch.setattr(coach_report, "record_usage", lambda run_dir, entry: logged.append(entry))
    args = coach_report.build_parser().parse_args(["synthetic-match", "0", "--dry-run"])
    args.usage_total = coach_report.new_usage()
    assert coach_report.run_player(args, args.match_uuid, BOOK, "0", 7, 100, data) == 1
    assert len(attempted) == 2 and len(logged) == 1
    entry = logged[0]
    assert (entry["attempts"], entry["usage_missing"], entry["outcome"]) == (2, 1, "failed")
    assert entry["usage"]["calls"] == 1 and entry["usage"]["cost_usd"] == pytest.approx(0.4)
    assert entry["usage"] == args.usage_total
    assert entry["number_misses"] == [{"stage": "first", "point": 0, "figure": "35", "kind": "metres",
                                       "nearest": None, "gap": None}]
    assert "usage: 0.00M tokens" in capsys.readouterr().out


def test_a_failed_run_still_records_its_usage(tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, tmp_path, ["abcdef12-0000-4000-8000-000000000000", "0"], make_data())
    logged = []

    def call(text, session):
        raise AssertionError("generate is replaced")

    call.denials, call.models, call.usage = 0, set(), coach_report.new_usage()
    call.attempts = 1

    received = []

    def generate(*args, misses=None):
        assert isinstance(misses, list)
        received.append(misses)
        misses.append({"stage": "first", "point": 0, "figure": "35", "kind": "metres",
                       "nearest": None, "gap": None})
        coach_report.add_usage(call.usage, _cli_out(0.4, 7, 50))
        raise runner.RunError("no json")

    monkeypatch.setattr(coach_report, "claude_call", lambda run_dir, model: call)
    monkeypatch.setattr(runner, "generate", generate)
    monkeypatch.setattr(coach_report, "record_usage", lambda run_dir, entry, log=None: logged.append(entry))
    assert coach_report.main() == 1
    assert logged[0]["outcome"] == "failed" and logged[0]["usage"]["cost_usd"] == pytest.approx(0.4)
    assert len(logged) == 1 and logged[0]["attempts"] == 1 and logged[0]["usage_missing"] == 0
    assert logged[0]["number_misses"] is received[0] and len(received[0]) == 1
    assert "usage: 0.00M tokens" in capsys.readouterr().out


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
    monkeypatch.setattr(coach_store_report, "resolve", lambda db, uuid, player: (7, 100))
    monkeypatch.setattr(snapshot, "read", lambda db, uuid, **kw: data)
    monkeypatch.setattr(coach, "active_notes", lambda db, kind: ["Plain words."])
    monkeypatch.setattr(coach_report.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(coach_report, "find_claude", lambda: "claude")

    def never(*args, **kwargs):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(coach_report, "claude_call", never)


def test_prompt_only_writes_the_first_prompt_and_never_calls_the_model(tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, tmp_path, ["ABCDEF12-0000-4000-8000-000000000000", "0", "--prompt-only"], make_data())
    assert coach_report.main() == 0
    path = tmp_path / "valo-coach" / "abcdef12-0000-4000-8000-000000000000-p100" / "prompt.txt"
    text = path.read_text(encoding="utf-8")
    assert "Plain words." not in text and "./q where" in text and "Trade." in text
    assert "the enemy Jett" in text and "P5#T5" not in text and "P1#T1" in text   # player 100 is on team1
    out = capsys.readouterr().out
    assert str(path) in out and str(len(text)) in out


@pytest.mark.parametrize("options, expected", [
    ([], "claude-opus-5-5"), (["--model", "x"], "x"), (["--model", ""], ""),
])
def test_the_selected_model_reaches_the_call_usage_and_payload(tmp_path, monkeypatch, options, expected):
    uuid = "abcdef12-0000-4000-8000-000000000000"
    _wire(monkeypatch, tmp_path, [uuid, "0", "--dry-run", *options], make_data())
    logged = []

    def call(text, session):
        raise AssertionError("generate is replaced")

    call.denials, call.models, call.usage = 0, {"cli-model"}, coach_report.new_usage()
    call.attempts = 1
    coach_report.add_usage(call.usage, _cli_out(0.1, 1, 10))

    def make_call(run_dir, model):
        assert model == expected
        return call

    received = []

    def generate(snap, slot, run_dir, book, model_call, model, *, misses=None):
        assert model_call is call
        assert model == (expected or "claude-code (default model)")
        assert isinstance(misses, list)
        received.append(misses)
        misses.append({"stage": "first", "point": 0, "figure": "35", "kind": "metres",
                       "nearest": None, "gap": None})
        return {"model": model, "points": [], "dropped_points": 0}

    monkeypatch.setattr(coach_report, "claude_call", make_call)
    monkeypatch.setattr(runner, "generate", generate)
    monkeypatch.setattr(coach_report, "record_usage", lambda run_dir, entry: logged.append(entry))
    assert coach_report.main() == 0
    assert logged[0]["model"] == (expected or "default")
    assert len(logged) == 1 and logged[0]["attempts"] == 1 and logged[0]["usage_missing"] == 0
    assert logged[0]["number_misses"] is received[0] and len(received[0]) == 1
    path = tmp_path / "valo-coach" / f"{uuid}-p100" / "payload.json"
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == (expected or "cli-model")


def test_a_player_not_in_the_snapshot_is_refused_with_2(tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, tmp_path, ["abcdef12-0000-4000-8000-000000000000", "nobody", "--prompt-only"], make_data())
    assert coach_report.main() == 2
    assert "refused" in capsys.readouterr().err


# --- review fixes ---

def test_q_ends_its_options_so_the_model_cannot_override_run(tmp_path):
    text = coach_report.write_q(tmp_path, tmp_path / "python.exe").read_text(encoding="utf-8")
    assert '"$@"' in text and '-- "$@"' in text
    assert '" -X utf8 "' in text


def test_coach_query_refuses_a_second_run_option(tmp_path, monkeypatch, capsys):
    from scripts import coach_query
    monkeypatch.setattr(sys, "argv", ["coach_query.py", "--run", str(tmp_path), "--run", str(tmp_path / "x"),
                                      "where", "1", "1"])
    assert coach_query.main() != 0
    assert "--run" in capsys.readouterr().err


def test_a_rerun_keeps_an_unstored_payload(tmp_path, monkeypatch, capsys):
    uuid = "abcdef12-0000-4000-8000-000000000000"
    _wire(monkeypatch, tmp_path, [uuid, "0", "--prompt-only"], make_data())
    old = tmp_path / "valo-coach" / f"{uuid}-p100"
    old.mkdir(parents=True)
    (old / "payload.json").write_text('{"kept": true}', encoding="utf-8")
    assert coach_report.main() == 0
    saved = list((tmp_path / "valo-coach").glob(f"{uuid}-p100-payload-*.json"))
    assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == '{"kept": true}'
    assert str(saved[0]) in capsys.readouterr().out
    assert not (old / "payload.json").exists()


def test_a_match_id_that_is_not_a_uuid_is_refused_with_2(tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, tmp_path, ["..\\..\\evil", "0", "--prompt-only"], make_data())
    assert coach_report.main() == 2
    assert "refused" in capsys.readouterr().err
    assert not (tmp_path / "valo-coach").exists()


def test_a_repair_index_of_unicode_digits_does_not_crash(snap, tmp_path):
    payload, _ = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q]), bad_point()]}),
        lambda q: json.dumps({"points": [{**good_point([q]), "index": "²"}]})])
    assert payload["dropped_points"] == 0 and [w["title"] for w in payload["unchecked"]] == ["Made up"]


def test_a_non_object_entry_keeps_its_place_and_fails_there(snap, tmp_path):
    payload, model = run(snap, tmp_path, [
        lambda q: json.dumps({"points": ["junk", bad_point(), good_point([q])]}),
        lambda q: json.dumps({"points": [{"index": 1, **good_point([q]), "title": "Fixed"}]})])
    assert "the point is not an object" in model.prompts[1][0] and '"index": 0' in model.prompts[1][0]
    assert [p["title"] for p in payload["points"]] == ["Fixed", "Held your ground"] and payload["dropped_points"] == 1


def test_a_checker_exception_fails_that_point_instead_of_the_run(snap, tmp_path, monkeypatch):
    from app.coach import checker
    real = checker.check_point

    def flaky(point, *args, **kwargs):
        if point.get("title") == "Made up":
            raise KeyError("boom")
        return real(point, *args, **kwargs)

    monkeypatch.setattr(checker, "check_point", flaky)
    payload, model = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q]), bad_point()]}), "no fix"])
    assert "could not read this point: KeyError" in model.prompts[1][0]
    assert payload["dropped_points"] == 0 and [w["title"] for w in payload["unchecked"]] == ["Made up"]


def test_a_follow_up_without_a_session_id_raises_instead_of_starting_afresh(snap, tmp_path):
    model = Model(snap, tmp_path, ["no json here"])
    model_call = lambda text, session: (model(text, session)[0], None)
    with pytest.raises(runner.RunError, match="session"):
        runner.generate(snap, 0, tmp_path, BOOK, model_call, "test-model")


# PR 132 review.

def test_each_rounds_economy_and_score_can_be_cited(snap, tmp_path):                         # finding 4
    data = make_data()
    data["context"]["rounds"]["1"]["stats"] = {"0": {"impact": -250, "loadout": 3900, "remaining": 100},
                                               "5": {"loadout": 800, "remaining": 0}}
    snapshot.save(data, tmp_path / "econ")
    snap = snapshot.load(tmp_path / "econ")
    point = {"polarity": "good", "theme": "trading", "title": "Bought up", "body": "3,900 against 800, at 0-0.",
             "instances": [{"round": 1, "t": 10.0, "caption": "", "evidence": ["FACTS"]}]}

    class Facts(Model):
        def __call__(self, text, session):
            brief = compact.parse(text.split("# The brief\n\n", 1)[1].split("\n\n# What to return", 1)[0]) \
                if "# The brief" in text else None
            if brief:
                self.facts = brief["rounds"][0]["facts_id"]
            return super().__call__(text, session)

    model = Facts(snap, tmp_path / "econ", [lambda q: json.dumps({"points": [
        {**point, "instances": [{**point["instances"][0], "evidence": [model.facts]}]}]})])
    payload = runner.generate(snap, 0, tmp_path / "econ", BOOK, model, "test-model")
    assert [p["title"] for p in payload["points"]] == ["Bought up"] and payload["unchecked"] == []


def test_fixes_returned_without_an_index_are_taken_in_order(snap, tmp_path):                 # finding 8
    # three good points and three failing ones; the reply fixes all three but leaves out "index"
    payload, _ = run(snap, tmp_path, [
        lambda q: json.dumps({"points": [good_point([q])] * 3 + [{**bad_point(), "polarity": "improve"}] * 3}),
        lambda q: json.dumps({"points": [{**good_point([q]), "title": f"Fixed {i}"} for i in range(3)]})])
    assert "Made up" not in [p["title"] for p in payload["points"]]       # the failing originals were replaced
    assert payload["unchecked"] == [] and payload["dropped_points"] == 0


def test_a_report_the_store_refuses_is_kept_on_disk(snap, tmp_path, monkeypatch):            # finding 9
    from app.services import coach
    monkeypatch.setattr(coach, "validate_payload",
                        lambda payload: (_ for _ in ()).throw(coach.ReportError("theme is longer than 32")))
    with pytest.raises(runner.RunError, match=runner.REFUSED_PAYLOAD):
        run(snap, tmp_path, [lambda q: json.dumps({"points": [good_point([q])]})])
    assert json.loads((tmp_path / runner.REFUSED_PAYLOAD).read_text(encoding="utf-8"))["points"]


def test_the_stored_title_is_trimmed_and_a_long_model_name_is_cut(snap, tmp_path):           # finding 9
    model = Model(snap, tmp_path, [lambda q: json.dumps({"points": [{**good_point([q]), "title": "  Held  "}]})])
    payload = runner.generate(snap, 0, tmp_path, BOOK, model, "m" * 80)
    assert payload["points"][0]["title"] == "Held" and len(payload["model"]) == 64


def test_parallel_appends_never_share_an_id(tmp_path):                                       # finding 6
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(lambda i: query.append(tmp_path, "where", [i], {"i": i}), range(40)))
    assert sorted(ids, key=lambda q: int(q[1:])) == [f"q{i}" for i in range(1, 41)]
    assert sorted(query.read_log(tmp_path)) == sorted(ids) and not (tmp_path / (query.LOG + ".lock")).exists()


def test_the_published_version_is_read_while_the_session_is_open(tmp_path, monkeypatch, capsys):
    import app.db
    from app.services import coach
    from tests.coach.test_coach_store import payload

    _wire(monkeypatch, tmp_path, ["abcdef12-0000-4000-8000-000000000000", "0"], make_data())

    class Db(_Db):
        open = True

        def __exit__(self, *exc):
            self.open = False
            return False

    class Report:               # like a row of a closed session: its attributes can no longer be loaded
        def __init__(self, db):
            self.db = db

        @property
        def version(self):
            assert self.db.open, "read after the session closed"
            return 4

    def call(text, session):
        raise AssertionError("generate is replaced")

    call.denials, call.models, call.usage = 0, set(), coach_report.new_usage()
    call.attempts = 0
    monkeypatch.setattr(app.db, "SessionLocal", Db)
    monkeypatch.setattr(coach_report, "claude_call", lambda run_dir, model: call)
    monkeypatch.setattr(runner, "generate", lambda *args, misses=None: payload())
    monkeypatch.setattr(coach, "store_report", lambda db, match_id, player_id, body: Report(db))
    assert coach_report.main() == 0
    assert "published version 4 (2 points, 0 dropped)" in capsys.readouterr().out


# --- the team command: one coach_report.py process given every player of the named player's team ---

def _team(monkeypatch, argv, results):
    import app.db
    from scripts import coach_store_report, coach_team

    ran = []

    def run_one(command):
        ran.append(command)
        return results.get(command[3], 0)

    monkeypatch.setattr(sys, "argv", ["coach_team.py", *argv])
    monkeypatch.setattr(app.db, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(coach_store_report, "team_names", lambda db, uuid, player: ["Owner#1", "Friend#2", "Third#3"])
    monkeypatch.setattr(coach_team, "run_one", run_one)
    return coach_team, ran


def test_the_team_command_hands_every_teammate_to_one_report_process(monkeypatch):
    coach_team, ran = _team(monkeypatch, ["abc", "Owner#1", "--model", "opus", "--dry-run"], {})
    assert coach_team.main() == 0
    assert [command[2:] for command in ran] == [["abc", "Owner#1", "Friend#2", "Third#3", "--model", "opus",
                                                 "--dry-run"]]
    assert ran[0][1].endswith("coach_report.py")
    with pytest.raises(SystemExit):                 # there is no option that asks for more than one at a time
        monkeypatch.setattr(sys, "argv", ["coach_team.py", "abc", "Owner#1", "--jobs", "2"])
        coach_team.main()


def test_the_team_command_passes_the_report_process_failure_on(monkeypatch):
    coach_team, ran = _team(monkeypatch, ["abc", "Owner#1"], {"Owner#1": 1})
    assert coach_team.main() == 1 and len(ran) == 1


# --- several players in one coach_report.py: one read of the match, the runs one after another ---

def _players(monkeypatch, tmp_path, names, fail=None, fake=True, extra=()):
    """coach_report.main over `names` (slots), counting match reads. With `fake`, run_player is recorded instead
    of run; `fail` maps a name to the exception its run raises."""
    from scripts import coach_store_report

    reads, runs, running = [], [], []
    _wire(monkeypatch, tmp_path, ["abcdef12-0000-4000-8000-000000000000", *names, *extra], make_data())
    monkeypatch.setattr(snapshot, "read", lambda db, uuid, **kw: reads.append(uuid) or make_data())
    monkeypatch.setattr(coach_store_report, "resolve", lambda db, uuid, player: (7, 100 + int(player)))

    def run_player(args, uuid, book, player, match_id, player_id, match):
        assert not running, "a second run started before the first ended"
        running.append(player)
        runs.append((player, player_id))
        running.pop()
        if player in (fail or {}):
            raise fail[player]
        return 0

    if fake:
        monkeypatch.setattr(coach_report, "run_player", run_player)
    return reads, runs


def test_several_players_read_the_match_once_and_run_in_order(tmp_path, monkeypatch, capsys):
    reads, runs = _players(monkeypatch, tmp_path, ["0", "6"])
    assert coach_report.main() == 0
    assert len(reads) == 1 and runs == [("0", 100), ("6", 106)]
    assert "2 of 2 runs succeeded" in capsys.readouterr().out


def test_each_player_of_one_read_sees_only_the_other_teams_names_hidden(tmp_path, monkeypatch, capsys):
    reads, _ = _players(monkeypatch, tmp_path, ["0", "6"], fake=False, extra=["--prompt-only"])
    assert coach_report.main() == 0 and len(reads) == 1
    run = tmp_path / "valo-coach" / "abcdef12-0000-4000-8000-000000000000"
    team1 = (run.parent / f"{run.name}-p100" / "prompt.txt").read_text(encoding="utf-8")   # player 100: team1
    team2 = (run.parent / f"{run.name}-p106" / "prompt.txt").read_text(encoding="utf-8")   # player 106: team2
    assert "P1#T1" in team1 and "P5#T5" not in team1
    assert "P5#T5" in team2 and "P1#T1" not in team2
    for directory in (run.parent / f"{run.name}-p100", run.parent / f"{run.name}-p106"):
        on_disk = "".join(p.read_text(encoding="utf-8") for p in directory.rglob("*.json"))
        assert ("P5#T5" in on_disk) != ("P1#T1" in on_disk)       # one team named, never both


def test_one_players_crash_does_not_stop_the_next_and_fails_the_command(tmp_path, monkeypatch, capsys):
    reads, runs = _players(monkeypatch, tmp_path, ["0", "6"], fail={"0": RuntimeError("boom")})
    assert coach_report.main() == 1 and [p for p, _ in runs] == ["0", "6"]
    out = capsys.readouterr().out
    assert "== 0: FAILED (exit 1) ==" in out and "1 of 2 runs succeeded; failed: 0" in out


def test_one_players_refusal_by_system_exit_does_not_stop_the_batch(tmp_path, monkeypatch, capsys):
    reads, runs = _players(monkeypatch, tmp_path, ["0", "6"], fail={"0": SystemExit("refused: stopped")})
    assert coach_report.main() == 1 and [p for p, _ in runs] == ["0", "6"]
    captured = capsys.readouterr()
    assert "refused: stopped" in captured.err and "1 of 2 runs succeeded; failed: 0" in captured.out


def test_a_player_missing_from_the_replay_fails_alone(tmp_path, monkeypatch, capsys):
    reads, _ = _players(monkeypatch, tmp_path, ["0", "99"], fake=False, extra=["--prompt-only"])
    assert coach_report.main() == 1 and len(reads) == 1      # player 199 is in the match but not the replay
    captured = capsys.readouterr()
    assert "player 199 is not in this replay" in captured.err and "== 99: FAILED (exit 2) ==" in captured.out
    assert (tmp_path / "valo-coach" / "abcdef12-0000-4000-8000-000000000000-p100" / "prompt.txt").exists()


def test_a_missing_claude_refuses_before_reading_anything(tmp_path, monkeypatch, capsys):
    reads, runs = _players(monkeypatch, tmp_path, ["0", "6"])
    monkeypatch.setattr(coach_report, "find_claude", lambda: None)
    assert coach_report.main() == 2 and reads == [] and runs == []
    assert "claude command is not on PATH" in capsys.readouterr().err
    assert not (tmp_path / "valo-coach").exists()


def test_an_unknown_player_refuses_the_batch_before_any_run(tmp_path, monkeypatch, capsys):
    from scripts import coach_store_report
    reads, runs = _players(monkeypatch, tmp_path, ["0", "nobody"])

    def resolve(db, uuid, player):
        if player == "nobody":
            raise ValueError("'nobody' is not a player of this match")
        return 7, 100

    monkeypatch.setattr(coach_store_report, "resolve", resolve)
    assert coach_report.main() == 2 and runs == [] and reads == []
    assert "refused" in capsys.readouterr().err


def test_a_duplicate_player_refuses_the_batch_before_any_run(tmp_path, monkeypatch, capsys):
    from scripts import coach_store_report

    reads, runs = _players(monkeypatch, tmp_path, ["P0#T0", "p0#t0"])
    monkeypatch.setattr(coach_store_report, "resolve", lambda db, uuid, name: (7, 100))
    assert coach_report.main() == 2 and runs == [] and reads == []
    assert "player 'p0#t0' was requested more than once" in capsys.readouterr().err


def test_the_team_command_refuses_a_player_who_is_not_in_the_match(monkeypatch, capsys):
    from scripts import coach_store_report
    coach_team, ran = _team(monkeypatch, ["abc", "nobody"], {})

    def refuse(db, uuid, player):
        raise ValueError("'nobody' is not a player of this match")

    monkeypatch.setattr(coach_store_report, "team_names", refuse)
    assert coach_team.main() == 2 and ran == []
    assert "refused" in capsys.readouterr().err
