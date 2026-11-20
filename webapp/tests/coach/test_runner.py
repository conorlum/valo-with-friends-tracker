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
        reply = self.replies.pop(0) if self.replies else "Nothing to add."      # the ask for a third point
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
    monkeypatch.setattr(coach_store_report, "on_roster", lambda name: name != "Random#0")
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


# --- review fixes ---

def test_q_ends_its_options_so_the_model_cannot_override_run(tmp_path):
    text = coach_report.write_q(tmp_path, tmp_path / "python.exe").read_text(encoding="utf-8")
    assert '"$@"' in text and '-- "$@"' in text


def test_coach_query_refuses_a_second_run_option(tmp_path, monkeypatch, capsys):
    from scripts import coach_query
    monkeypatch.setattr(sys, "argv", ["coach_query.py", "--run", str(tmp_path), "--run", str(tmp_path / "x"),
                                      "where", "1", "1"])
    assert coach_query.main() != 0
    assert "--run" in capsys.readouterr().err


def test_a_rerun_keeps_an_unstored_payload(tmp_path, monkeypatch, capsys):
    uuid = "abcdef12-0000-4000-8000-000000000000"
    _wire(monkeypatch, tmp_path, [uuid, "0", "--prompt-only"], make_data())
    old = tmp_path / "valo-coach" / f"{uuid}-p8"
    old.mkdir(parents=True)
    (old / "payload.json").write_text('{"kept": true}', encoding="utf-8")
    assert coach_report.main() == 0
    saved = list((tmp_path / "valo-coach").glob(f"{uuid}-p8-payload-*.json"))
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
        runner.generate(snap, 0, tmp_path, BOOK, [], model_call, "test-model")


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
            brief = json.loads(text.split("# The brief\n\n", 1)[1].split("\n\n# What to return", 1)[0]) \
                if "# The brief" in text else None
            if brief:
                self.facts = brief["rounds"][0]["facts_id"]
            return super().__call__(text, session)

    model = Facts(snap, tmp_path / "econ", [lambda q: json.dumps({"points": [
        {**point, "instances": [{**point["instances"][0], "evidence": [model.facts]}]}]})])
    payload = runner.generate(snap, 0, tmp_path / "econ", BOOK, [], model, "test-model")
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
    payload = runner.generate(snap, 0, tmp_path, BOOK, [], model, "m" * 80)
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

    call.denials, call.models = 0, set()
    monkeypatch.setattr(app.db, "SessionLocal", Db)
    monkeypatch.setattr(coach_report, "claude_call", lambda run_dir, model: call)
    monkeypatch.setattr(runner, "generate", lambda *args: payload())
    monkeypatch.setattr(coach, "store_report", lambda db, match_id, player_id, body: Report(db))
    assert coach_report.main() == 0
    assert "published version 4 (2 points, 0 dropped)" in capsys.readouterr().out


# --- the team command: one coach_report.py run per player of the named player's team ---

def _team(monkeypatch, argv, results, off_roster=()):
    import app.db
    from scripts import coach_store_report, coach_team

    ran = []

    def run_one(command):
        ran.append(command)
        return results.get(command[3], 0)

    monkeypatch.setattr(sys, "argv", ["coach_team.py", *argv])
    monkeypatch.setattr(app.db, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(coach_store_report, "team_names", lambda db, uuid, player: ["Owner#1", "Friend#2", "Third#3"])
    monkeypatch.setattr(coach_store_report, "on_roster", lambda name: name not in off_roster)
    monkeypatch.setattr(coach_team, "run_one", run_one)
    return coach_team, ran


def test_the_team_command_gives_a_teammate_off_the_roster_no_run(monkeypatch, capsys):
    coach_team, ran = _team(monkeypatch, ["abc", "Owner#1"], {}, off_roster=("Friend#2",))
    assert coach_team.main() == 0
    assert [command[3] for command in ran] == ["Owner#1", "Third#3"]
    out = capsys.readouterr().out
    assert "not on the roster, no run: Friend#2" in out and "2 of 2 runs succeeded" in out


def test_the_team_command_refuses_a_team_with_nobody_on_the_roster(monkeypatch, capsys):
    coach_team, ran = _team(monkeypatch, ["abc", "Owner#1"], {}, off_roster=("Owner#1", "Friend#2", "Third#3"))
    assert coach_team.main() == 2 and ran == []


def test_a_player_off_the_roster_is_refused_before_anything_is_read(tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, tmp_path, ["ABCDEF12-0000-4000-8000-000000000000", "Random#0"], make_data())
    assert coach_report.main() == 2
    assert "not on the roster" in capsys.readouterr().err and not (tmp_path / "valo-coach").exists()


def test_the_roster_is_the_tracked_players_file_whatever_the_case():
    from scripts import coach_store_report

    assert coach_store_report.on_roster(" nprightdolphin#na1 ") and not coach_store_report.on_roster("Jello#meoww")


def test_a_runs_usage_adds_up_over_its_calls():
    total = {}
    one = {"total_cost_usd": 1.25, "num_turns": 30, "duration_ms": 90000,
           "usage": {"input_tokens": 100, "output_tokens": 2000, "cache_read_input_tokens": 50000,
                     "cache_creation_input_tokens": 7000}}
    coach_report.add_usage(total, one)
    coach_report.add_usage(total, {**one, "total_cost_usd": None, "usage": "broken"})
    assert total == {"calls": 2, "cost_usd": 1.25, "turns": 60, "seconds": 180.0, "input": 100, "output": 2000,
                     "cache_read": 50000, "cache_write": 7000}
    assert coach_report.usage_line(total).startswith("usage: $1.25 at API prices, 2 call(s), 60 turns, 3.0 min")
    assert coach_report.usage_line({}) == "usage: no model call finished"


def test_the_team_command_runs_every_teammate_in_order_and_passes_the_options_on(monkeypatch, capsys):
    coach_team, ran = _team(monkeypatch, ["abc", "Owner#1", "--model", "opus", "--dry-run"], {})
    assert coach_team.main() == 0
    assert [command[2:] for command in ran] == [["abc", name, "--model", "opus", "--dry-run"]
                                                for name in ("Owner#1", "Friend#2", "Third#3")]
    assert all(command[1].endswith("coach_report.py") for command in ran)
    out = capsys.readouterr().out
    assert "3 players, one at a time" in out and "== 2 of 3: Friend#2 ==" in out and "3 of 3 runs succeeded" in out


def test_the_team_command_never_runs_two_players_at_once(monkeypatch):
    coach_team, ran = _team(monkeypatch, ["abc", "Owner#1"], {})
    running = []

    def run_one(command):
        assert not running, "a second run started before the first ended"
        running.append(command)
        ran.append(command)
        running.pop()
        return 0

    monkeypatch.setattr(coach_team, "run_one", run_one)
    assert coach_team.main() == 0 and len(ran) == 3
    with pytest.raises(SystemExit):                 # there is no option that asks for more than one at a time
        monkeypatch.setattr(sys, "argv", ["coach_team.py", "abc", "Owner#1", "--jobs", "2"])
        coach_team.main()


def test_one_failed_teammate_does_not_stop_the_others_and_fails_the_command(monkeypatch, capsys):
    coach_team, ran = _team(monkeypatch, ["abc", "Owner#1"], {"Friend#2": 1})
    assert coach_team.main() == 1 and len(ran) == 3
    out = capsys.readouterr().out
    assert "== Friend#2: FAILED (exit 1) ==" in out and "2 of 3 runs succeeded; failed: Friend#2" in out


def test_the_team_command_refuses_a_player_who_is_not_in_the_match(monkeypatch, capsys):
    from scripts import coach_store_report
    coach_team, ran = _team(monkeypatch, ["abc", "nobody"], {})

    def refuse(db, uuid, player):
        raise ValueError("'nobody' is not a player of this match")

    monkeypatch.setattr(coach_store_report, "team_names", refuse)
    assert coach_team.main() == 2 and ran == []
    assert "refused" in capsys.readouterr().err
