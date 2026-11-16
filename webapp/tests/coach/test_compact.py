import copy
import json
import sys

import pytest

from app.coach import compact, query, prompt
from app.coach.notes import NOTES
from tests.coach.test_query import snap, SHARES
from tests.coach.test_prompt import BOOK, BRIEF


def drop_nulls(value):
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


def same(actual, expected):
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


AREA = {"name": "Mid Catwalk", "super": "Mid", "path": ["Mid Catwalk"], "approximate": True}
WHERE = {"round": 11, "t": 59.0, "players": [
    {"slot": 0, "name": "the enemy Raze", "side": "A", "role": "attack", "alive": False,
     "died_at": 19.122, "u": 5440, "v": 4435, "area": AREA},
    {"slot": 1, "name": "Player#TAG", "side": "B", "role": "defense", "alive": True,
     "u": 3291, "v": 4424, "yaw": 273,
     "area": {"name": "Mid Top", "super": "Mid", "path": ["Mid Top"], "approximate": True}, "health": None}],
    "note": "health is a word ... . areas marked approximate are a nearest-label guess, not a painted area"}
KNEW = {"slot": 0, "name": "the enemy Raze", "first_seen": 0.0, "last_seen": 19.0625,
        "in_view": False, "u": 5462, "v": 4439, "area": AREA, "alive": False}
FLAG = {"round": 3, "t": 21.0, "kind": "stack_seen_elsewhere", "site": "A", "enemies_seen": 4,
        "enemies": ["the enemy Raze", "the enemy Omen"], "player_area_then": "B Danger", "hold_s": 8.0,
        "role": "defense"}


@pytest.mark.parametrize("value", [
    WHERE, KNEW, FLAG, {"a": {"b": None, "3": [None, {"x": None}]}},
    [{"a": 1}, {"b": False}, {"a": None}], [{}, {}], [], {},
    [None, False, 0, 59, 59.0, 19.0625, -0.5, ""], [{"a": 1}, 2, [None]],
    ["12", "true", "null", "a|b", "", " lead", "trail ", "two\nlines", "Kay/O ü", "~a", '"a',
     "table", "a;b", "a:b", "<a>", "a\\b", "false", "NaN", "Infinity", "-0.0"],
    AREA, {**AREA, "approximate": False}, {k: v for k, v in AREA.items() if k != "approximate"},
    {"": 0, "3": {}, "a|b": [None], "note": "not special"},
    [{"col": "col"}, {"col": "1", "last": None}],
    [{"nested": [{"a": None}, {"b": [None, {}]}]}, {"nested": {"x": None}}],
    {**AREA, "extra": {"kept": False}},
])
def test_round_trip(value):
    before = copy.deepcopy(value)
    same(compact.parse(compact.render(value)), drop_nulls(value))
    same(value, before)


def test_tables_and_inline_areas_are_compact_and_ordered():
    text = compact.render(WHERE)
    assert text.count("slot") == 1 and text.count("died_at") == 1
    assert text.index("the enemy Raze") < text.index("Player#TAG")
    assert "~(" in text and text.count("approximate") == 1  # only the note spells out the area flag
    assert len(text) < len(json.dumps(WHERE))
    assert compact.render([{"z": 1}, {"a": 2, "z": 0}]).startswith("<z|a;\n")


@pytest.mark.parametrize("cmd,result", [
    ("flag", {**FLAG, "note": "flag caveat"}), ("impact", {"impact": -250}),
    ("where", {"error": "bad round", "note": "error caveat"}),
    ("knew", {"available": False, "reason": "team_knowledge", "note": "missing caveat"}),
])
def test_show_special_answers(cmd, result):
    entry = {"id": "q7", "cmd": cmd, "args": [3, "A Main"], "result": result}
    original = copy.deepcopy(entry)
    header, body = query.show(entry).split("\n", 1)
    assert "q7" in header and cmd in header and "A Main" in header
    same(compact.parse(body.split("\nnote: ", 1)[0]), drop_nulls({k: v for k, v in result.items() if k != "note"}))
    if "note" in result:
        assert body.endswith("note: " + result["note"])
    same(entry, original)


COMMAND_ARGS = {"where": ["1", "15"], "knew": ["1", "15", "A"],
                "distance": ["1", "15", "0", "1"], "control": ["1", "0", "10", "A Site"],
                "timeline": ["1"], "impact": ["1", "0"]}


@pytest.mark.parametrize("approximate", [False, True])
def test_every_command_keeps_results_and_notes(snap, tmp_path, monkeypatch, approximate):
    monkeypatch.setattr(query, "_has_approximate_area", lambda rows: approximate)
    monkeypatch.setattr(snap, "control", lambda n: {**SHARES, "approximate": approximate} if n == 1 else None)
    suffix = ". areas marked approximate are a nearest-label guess, not a painted area"
    for cmd in query.COMMANDS:
        args = COMMAND_ARGS[cmd]
        result = query.answer(snap, cmd, args)
        if cmd in NOTES:
            extra = ("; area labels are approximate nearest-callout guesses" if cmd == "control" else suffix)
            assert result["note"] == EXPECTED_NOTES[cmd] + (extra if approximate and cmd != "distance" else "")
        out = query.run(snap, tmp_path, cmd, args)
        same(out, {"id": out["id"], **result})
        entry = query.read_log(tmp_path)[out["id"]]
        same(entry["result"], result)
        shown = query.show(entry)
        same(compact.parse(shown.split("\n", 1)[1].split("\nnote: ", 1)[0]),
             drop_nulls({k: v for k, v in result.items() if k != "note"}))
        if cmd in NOTES:
            assert NOTES[cmd] not in shown
            assert ("\nnote: " in shown) == (approximate and cmd != "distance")
            if approximate and cmd != "distance":
                assert shown.endswith("note: " + extra)
    for cmd, args in [("where", ["bad", "0"]), ("knew", ["2", "0", "A"])]:
        result = query.answer(snap, cmd, args)
        assert "error" in result or result.get("available") is False
        same(compact.parse(query.show({"id": "q0", "cmd": cmd, "args": args, "result": result}).split("\n", 1)[1]), result)


def test_fixed_notes_appear_once_in_prompt():
    text = prompt.run_prompt(BOOK, BRIEF)
    assert "# Fixed notes" in text
    for cmd, note in EXPECTED_NOTES.items():
        assert NOTES[cmd] == note
        assert text.count(note) == 1


@pytest.mark.parametrize("use_compact", [False, True])
def test_cli_default_json_and_optional_compact(snap, tmp_path, monkeypatch, capsys, use_compact):
    from app.coach import snapfile
    from scripts import coach_query

    monkeypatch.setattr(snapfile, "load", lambda path: snap)
    argv = ["coach_query.py", "--run", str(tmp_path)] + (["--compact"] if use_compact else [])
    monkeypatch.setattr(sys, "argv", argv + ["--", "where", "1", "15"])
    assert coach_query.main() == 0
    text = capsys.readouterr().out.strip()
    entry = query.read_log(tmp_path)["q1"]
    if use_compact:
        assert text == query.show(entry)
    else:
        same(json.loads(text), {"id": "q1", **entry["result"]})


EXPECTED_NOTES = {'where': "health is a word (healthy, hurt, low) after the player's last recorded hit; null means no hit is "
          'recorded so far, not full health; heals are not recorded; a living player with no u/v has no '
          'usable position at that time',
 'knew': 'team knowledge, assuming everything any teammate learned was called. enemies: those the team had '
         'seen, with the place and time of the last sighting. signals: events that found or roughly placed '
         'an enemy, with class found or roughly_placed, time, reason and only a rough_place when positions '
         'are available: from the nearest teammate, a minimap direction (up, down, left, right: a 90 degree '
         'quarter) and a distance band in metres. It is never a spot or a named area; ruled_out: utility '
         'showed where an enemy was NOT, with time, name and reason; for Haunt and Recon, area is where the '
         "pulse looked and found nobody of that name, when the device's position is available. It never "
         "gives the enemy's position. lost_track: the team lost where an enemy was (no place given), with "
         'only time, name and reason. All three lists are null when this round stores no knowledge events. '
         "destroyed: this side's own devices (trip, camera, alarmbot, turret, sound sensor, haunt, recon) "
         'shot by the enemy by this time before anything set them off, with time, device, owner and the area '
         'the device stood in; the shooter is not recorded and a destroyed device tells nothing about where '
         'an enemy was',
 'distance': 'x_looking_off: degrees between where x faced and the direction to the other player (0 is '
             "straight at them, 180 straight away); null when the facing isn't recorded",
 'control': "a and b are the round's side groups; shares are fractions of the area's cells over the span",
 'timeline': 'a destroyed event is a device (trip, camera, alarmbot, turret, sound sensor, haunt, recon) '
             'shot before anything set it off: time, device, owner and the area it stood in. The shooter is '
             'not recorded'}
