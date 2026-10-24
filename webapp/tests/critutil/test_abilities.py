"""The ability table loader, the wiki duration parser and the fetch/merge script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.critutil import abilities

WEBAPP_ROOT = Path(__file__).resolve().parents[2]
PINNED_EXTRACTOR_HASH = "b23046a71304588119a3cba28fc675e3f0739a8390a209dba8a59aefbc544d86"


def _script():
    spec = importlib.util.spec_from_file_location("fetch_util_durations", WEBAPP_ROOT / "scripts" / "fetch_util_durations.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HEAD = '==Stats==\n{| class="wikitable" style="text-align:left"\n!Stat\n!Value\n!Confirmed<br>value?\n'
TAIL = "|}\n{{#tag:small|Notes}}\n\n==Quotes==\n"


def _table(*rows: str) -> str:
    return HEAD + "".join("|-\n" + r for r in rows) + TAIL


EQUIP = "|[[File:Equip.png|20px|link=]] Equip time\n|0.8 seconds\n|{{Tick}}\n"
DURATION = "|[[File:Duration.png|20px|link=]] Duration\n|8 seconds\n|{{Tick}}<sup/>{{patchv|1.0}}\n"


def test_plain_duration_row():
    secs, rows = abilities.parse_duration(_table(EQUIP, DURATION))
    assert secs == 8.0
    assert rows == [{"label": "Duration", "value": "8 seconds"}]


def test_duration_while_applying_nearsight():
    row = "|[[File:Duration.png|20px|link=]] Duration while applying nearsight\n|1.6 seconds\n|{{Tick}}\n"
    secs, rows = abilities.parse_duration(_table(EQUIP, row))
    assert secs == 1.6
    assert rows[0]["label"] == "Duration while applying nearsight"


def test_plain_row_wins_over_max_blind_duration():
    blind = "|[[File:Blind.png|20px|link=]] Max blind duration\n|2.5 seconds\n|{{Cross}}\n"
    secs, rows = abilities.parse_duration(_table(blind, DURATION))
    assert secs == 8.0
    assert len(rows) == 2
    secs, _ = abilities.parse_duration(_table(blind))
    assert secs == 2.5


def test_range_takes_the_upper_end():
    for text in ("1–3 seconds", "1-3 seconds"):
        row = f"|[[File:Duration.png|20px|link=]] Duration\n|{text}<br><small>(by distance)</small>\n|{{{{Cross}}}}\n"
        assert abilities.parse_duration(_table(row))[0] == 3.0


def test_rowspan_label():
    row = 'rowspan="2"|[[File:Duration.png|20px|link=]] Duration\n|12.5 seconds\n|{{Tick}}\n|-\n|more\n|{{Tick}}\n'
    secs, rows = abilities.parse_duration(_table("|" + row))
    assert secs == 12.5
    assert rows[0]["label"] == "Duration"


def test_unquoted_rowspan_label():
    # Sky Smoke, Dark Cover, Ruse, Shear: `|rowspan=2| Duration`, no quotes around the value
    row = 'rowspan=2|[[File:Duration.png|20px|link=]] Duration\n|19.25 seconds\n|{{Tick}}\n|-\n|more\n|{{Tick}}\n'
    secs, rows = abilities.parse_duration(_table("|" + row), field="Duration")
    assert secs == 19.25
    assert rows[0]["label"] == "Duration"


def test_no_duration_row():
    assert abilities.parse_duration(_table(EQUIP)) == (None, [])
    assert abilities.parse_duration("no table here") == (None, [])


def test_duration_row_without_number():
    row = "|[[File:Duration.png|20px|link=]] Duration\n|Until destroyed\n|{{Cross}}\n"
    secs, rows = abilities.parse_duration(_table(row))
    assert secs is None
    assert rows == [{"label": "Duration", "value": "Until destroyed"}]


def test_field_picks_the_named_row_case_insensitively():
    blind = "|[[File:Blind.png|20px|link=]] Max blind duration\n|2.5 seconds\n|{{Cross}}\n"
    secs, rows = abilities.parse_duration(_table(blind, DURATION), field="max BLIND duration")
    assert secs == 2.5
    assert len(rows) == 2
    assert abilities.parse_duration(_table(blind, DURATION), field="Duration")[0] == 8.0


def test_field_label_with_html_comment_prefix():
    row = "|<!-- note -->Concuss duration\n|4 seconds\n|{{Tick}}\n"
    secs, rows = abilities.parse_duration(_table(DURATION, row), field="Concuss duration")
    assert secs == 4.0
    assert rows[1]["label"] == "Concuss duration"


def test_field_not_on_the_page_gives_none():
    secs, rows = abilities.parse_duration(_table(DURATION), field="Slow duration")
    assert secs is None
    assert rows == [{"label": "Duration", "value": "8 seconds"}]
    assert abilities.parse_duration(_table(DURATION), field="none")[0] is None


def test_from_dir_uses_field_and_slash_file_names(tmp_path):
    blind = "|[[File:Blind.png|20px|link=]] Max blind duration\n|2.5 seconds\n|{{Cross}}\n"
    seed = {"abilities": [
        {"match": "A", "agent": "X", "ability": "Alpha", "wiki": {"page": "Nebula / Dissipate", "field": "Max blind duration"}},
        {"match": "B", "agent": "Y", "ability": "Beta", "wiki": {"page": "Beta", "field": "none"}},
        {"match": "C", "agent": "Z", "ability": "Gamma", "wiki": {"page": "Gamma", "field": None}},
    ]}
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "Nebula _ Dissipate.json").write_text(
        json.dumps({"parse": {"wikitext": {"*": _table(DURATION, blind)}}}), encoding="utf-8")
    (saved / "Beta.json").write_text(
        json.dumps({"parse": {"wikitext": {"*": _table(DURATION)}}}), encoding="utf-8")
    (saved / "Gamma.json").write_text(json.dumps({"error": {"code": "missingtitle"}}), encoding="utf-8")
    out = tmp_path / "out.json"
    _script().main(["--from", str(saved), "--seed", str(seed_path), "--out", str(out)])
    got = json.loads(out.read_text(encoding="utf-8"))["abilities"]
    assert got[0]["duration_s"] == 2.5 and got[0]["wiki"]["field"] == "Max blind duration"
    assert got[1]["duration_s"] is None and got[1]["provisional"] is True and got[1]["wiki"]["field"] == "none"
    assert got[2]["duration_s"] is None and got[2]["provisional"] is True


def _entries():
    return [
        {"match": "Grenade_Molotov", "agent": "Phoenix", "ability": "Blaze", "slot": "C", "type": "wall",
         "kinds": ["smoke"], "rule": "duration", "duration_s": 8.0, "wiki": {"page": "Blaze"}, "pieces": ["a"]},
        {"match": "Sprint", "agent": "Jett", "ability": "Tailwind", "slot": "E", "type": None, "kinds": [],
         "rule": "duration", "duration_s": 3.0, "wiki": {}, "pieces": []},
    ]


def test_lookup_by_code_and_agent():
    es = _entries()
    assert abilities.lookup("Ability_Grenade_Molotov_C", entries=es)["ability"] == "Blaze"
    assert abilities.lookup("Ability_Grenade_Molotov_C", agent="Phoenix", entries=es)["ability"] == "Blaze"
    assert abilities.lookup("Ability_Grenade_Molotov_C", agent="Jett", entries=es) is None
    assert abilities.lookup("Nothing", entries=es) is None


def test_hashes_change_with_relevant_fields():
    es = _entries()
    t0, e0 = abilities.table_hash(es), abilities.extractor_hash(es)
    es[0]["duration_s"] = 9.0
    es[0]["wiki"] = {"page": "Other"}
    assert abilities.table_hash(es) != t0
    assert abilities.extractor_hash(es) == e0
    for field, value in (("match", "X"), ("pieces", ["b"]), ("kinds", ["wall"]), ("type", "zone")):
        es2 = _entries()
        es2[0][field] = value
        assert abilities.extractor_hash(es2) != e0, field


def test_extractor_hash_missing_type_is_null():
    a = [{"match": "x", "pieces": [], "kinds": []}]
    b = [{"match": "x", "pieces": [], "kinds": [], "type": None}]
    assert abilities.extractor_hash(a) == abilities.extractor_hash(b)


def test_load_explicit_path(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"abilities": _entries()}), encoding="utf-8")
    assert abilities.load(p) == _entries()


def test_from_dir_merges_and_reports(tmp_path, capsys):
    seed = {"abilities": [
        {"match": "A", "agent": "X", "ability": "Alpha", "wiki": {"page": "Alpha"}, "replay_s": 10.7},
        {"match": "B", "agent": "Y", "ability": "Beta", "replay_s": 5.0},
        {"match": "C", "agent": "Z", "ability": "Gamma"},
    ]}
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "Alpha.json").write_text(
        json.dumps({"parse": {"wikitext": {"*": _table(DURATION)}}}), encoding="utf-8")
    (saved / "Beta.json").write_text(
        json.dumps({"parse": {"wikitext": {"*": _table(EQUIP)}}}), encoding="utf-8")
    out = tmp_path / "out.json"
    _script().main(["--from", str(saved), "--seed", str(seed_path), "--out", str(out)])
    got = json.loads(out.read_text(encoding="utf-8"))["abilities"]
    assert [e["ability"] for e in got] == ["Alpha", "Beta", "Gamma"]
    assert got[0]["duration_s"] == 8.0
    assert got[0]["wiki"]["value"] == "8 seconds"
    assert got[0]["wiki"]["rows"] == [{"label": "Duration", "value": "8 seconds"}]
    assert "provisional" not in got[0]
    for e in got[1:]:
        assert e["duration_s"] is None and e["provisional"] is True
    text = capsys.readouterr().out
    assert "Alpha" in text and "10.7" in text
    assert "Beta" in text and "Gamma" in text


TYPES = {"flash", "smoke", "wall", "molly", "status", "recon", "placeable", "heal", "ult", "other"}
RULES = {"duration", "status", "flash", "placeable", "on_spans", "heal", "ult:until_end", "ult:until_death",
         "ult:capture", "ult:thrash", "ult:duel", "ult:omen", "ult:raze", "ult:seekers"}
KNOWN_CODES = [
    "Clay_X_Rocket", "Aggrobot_RollyPolly", "Phoenix_4_Molotov_Production", "Clay_4_Projectile_Primary",
    "Clay_4_Projectile_Secondary", "Gumshoe_E_CameraTrackingDart", "Gumshoe_E_PossessableCamera",
    "Grenadier_Q_SemtexBasic", "Iris_Concuss", "Iris_ConcussPulse", "Nox_Wall", "Nox_WallTrap",
    "Smonk_NewSmoke_PDS", "Cashew_X_Segment", "Deadeye_E_Trap", "Sprinter_4_GroundStrike",
]


def test_every_agent_key_ability_and_slot_x_row_is_in_the_table():
    from app.critutil.constants import AGENT_KEYS
    have = {(e["agent"], e["ability"]) for e in abilities.load()}
    for agent, slots in AGENT_KEYS.items():
        for slot, (name, _kind) in slots.items():
            assert (agent, name) in have, (agent, name)
    assert len(have) == len(abilities.load())
    assert sum(1 for e in abilities.load() if e["slot"] == "X") >= 26


def test_table_types_rules_and_regexes():
    import re
    for e in abilities.load():
        assert e["type"] in TYPES, e["ability"]
        assert e["rule"] in RULES or re.fullmatch(r"fixed:\d+(\.\d+)?", e["rule"]), e["ability"]
        assert isinstance(e["kinds"], list) and e["kinds"]
        re.compile(e["match"])
        for p in e["pieces"]:
            re.compile(p)


def test_no_two_entries_match_the_same_known_code_name():
    import re
    for code in KNOWN_CODES:
        hits = [e["ability"] for e in abilities.load() if re.search(e["match"], code)]
        assert len(hits) <= 1, (code, hits)
    assert abilities.lookup("Clay_X_Rocket")["ability"] == "Showstopper"
    assert abilities.lookup("Phoenix_4_Molotov_Production")["ability"] == "Hot Hands"
    assert abilities.lookup("Gumshoe_E_CameraTrackingDart")["ability"] == "Spycam dart"


def test_astra_stars_are_not_her_ult():
    # stars are placed 2-6 a round in Astral Form; only the Cosmic Divide wall is the ult
    star = abilities.lookup("Rift_X_Markers")
    assert star["ability"] == "Astral Form star" and star["type"] == "other" and "ult" not in star["kinds"]
    assert abilities.lookup("Rift_X_GlobalWall")["ability"] == "Cosmic Divide"


def test_extractor_hash_is_pinned():
    assert abilities.extractor_hash() == PINNED_EXTRACTOR_HASH, (
        "the extractor-facing fields of app/critutil/abilities.json changed: bump app/critutil/constants.VERSION "
        "and, once this branch has shipped, GAPS_REVISION (stored rows are recomputed only by the gap run); "
        "then re-record this hash")
