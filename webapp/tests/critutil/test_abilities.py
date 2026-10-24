"""The ability table loader, the wiki duration parser and the fetch/merge script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.critutil import abilities

WEBAPP_ROOT = Path(__file__).resolve().parents[2]
PINNED_EXTRACTOR_HASH = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


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


def test_no_duration_row():
    assert abilities.parse_duration(_table(EQUIP)) == (None, [])
    assert abilities.parse_duration("no table here") == (None, [])


def test_duration_row_without_number():
    row = "|[[File:Duration.png|20px|link=]] Duration\n|Until destroyed\n|{{Cross}}\n"
    secs, rows = abilities.parse_duration(_table(row))
    assert secs is None
    assert rows == [{"label": "Duration", "value": "Until destroyed"}]


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


def test_extractor_hash_is_pinned():
    assert abilities.extractor_hash() == PINNED_EXTRACTOR_HASH, (
        "the extractor-facing fields of app/critutil/abilities.json changed: bump app/critutil/constants.VERSION "
        "and, once this branch has shipped, GAPS_REVISION (stored rows are recomputed only by the gap run); "
        "then re-record this hash")
