import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[1]
FIXTURE = WEBAPP / "tests" / "fixtures" / "patch_notes" / "wiki_maps_history.txt"

_spec = importlib.util.spec_from_file_location(
    "build_map_pool_seed", WEBAPP / "scripts" / "build_map_pool_seed.py"
)
bmps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bmps)

UTC = timezone.utc


@pytest.fixture(scope="module")
def parsed():
    return bmps.parse_wiki_history(FIXTURE.read_text(encoding="utf-8"))


def test_columns_skip_unfilled(parsed):
    columns, pools, _ = parsed
    assert columns[0] == "5.0"
    assert columns[-1] == "12.08"
    assert "13.00" not in columns and "13.04" not in columns and "13.08" not in columns
    assert set(columns) == set(pools)
    assert columns[:4] == ["5.0", "6.0", "6.08", "7.04"]


def test_pools_from_wiki(parsed):
    _, pools, _ = parsed
    assert pools["5.0"] == {"Ascent", "Bind", "Breeze", "Fracture", "Haven", "Icebox", "Pearl"}
    assert pools["12.08"] == {"Ascent", "Breeze", "Fracture", "Haven", "Lotus", "Pearl", "Split"}


def test_wiki_late_entries_are_in_wiki_columns(parsed):
    _, pools, _ = parsed
    # the wiki counts these from the release patch; corrections fix them
    assert "Lotus" in pools["6.0"]
    assert "Sunset" in pools["7.04"]
    assert "Abyss" in pools["8.11"]


def test_releases(parsed):
    _, _, releases = parsed
    assert releases["Ascent"] == "1.0"
    assert releases["Icebox"] == "1.10"
    assert releases["Breeze"] == "2.08"
    assert releases["Fracture"] == "3.05"
    assert releases["Pearl"] == "5.0"
    assert releases["Abyss"] == "8.11"
    assert releases["Sunset"] == "7.04"


def test_tolerates_colspan_semicolon_and_short_row():
    wikitext = """===History===
{| class="wikitable"
!rowspan=2|Maps
!rowspan=2|Release<br>Patch
!colspan=100|Starting Patch
|-
!{{patch|5.0}}
!{{patch|6.0}}
!{{patch|6.08}}
|-
|[[A]]
|{{patch|1.0}}
|style="background:#9eff9e;"|
|style="background:#ffc7c7;"|
|style="background:#9eff9e;"|
|-
|[[B]]
|{{patch|5.0}}
| colspan="2;" style="background:gray;" |
|style="background:#9eff9e;"|
|-
|[[C]]
|{{patch|1.0}}
|style="background:#9eff9e;"|
|-class=sortbottom
|}"""
    columns, pools, releases = bmps.parse_wiki_history(wikitext)
    assert columns == ["5.0", "6.0", "6.08"]
    assert pools == {"5.0": {"A", "C"}, "6.0": set(), "6.08": {"A", "B"}}
    assert releases == {"A": "1.0", "B": "5.0", "C": "1.0"}


def test_apply_corrections_edits_and_inserts_column():
    columns = ["6.0", "6.08"]
    pools = {"6.0": {"A", "Lotus"}, "6.08": {"A", "Lotus", "B"}}
    corrections = [
        {"patch": "6.0", "remove": ["Lotus"]},
        {"patch": "6.01", "after_patch": "6.0", "add": ["Lotus"]},
        {"patch": "6.02", "after_patch": "6.01", "add": ["C"], "remove": ["A"]},
    ]
    cols2, pools2 = bmps.apply_corrections(columns, pools, corrections)
    assert cols2 == ["6.0", "6.01", "6.02", "6.08"]
    assert pools2["6.0"] == {"A"}
    assert pools2["6.01"] == {"A", "Lotus"}
    assert pools2["6.02"] == {"Lotus", "C"}
    assert pools2["6.08"] == {"A", "Lotus", "B"}
    # inputs untouched
    assert columns == ["6.0", "6.08"] and pools["6.0"] == {"A", "Lotus"}


def test_apply_corrections_unknown_patch_raises():
    with pytest.raises(KeyError):
        bmps.apply_corrections(["6.0"], {"6.0": {"A"}}, [{"patch": "9.9", "add": ["X"]}])


def test_committed_corrections_reproduce_patch_note_pools(parsed):
    columns, pools, _ = parsed
    corrections = json.loads((WEBAPP / "scripts" / "map_pool_corrections.json").read_text())
    for c in corrections:
        assert c["quote"] and c["url"].startswith("https://")
    cols, p = bmps.apply_corrections(columns, pools, corrections)
    assert "Lotus" not in p["6.0"] and "Lotus" in p["6.01"]
    assert "Sunset" not in p["7.04"] and "Sunset" in p["7.05"]
    assert "Abyss" not in p["8.11"]
    assert p["9.0"] == {"Abyss", "Ascent", "Bind", "Haven", "Sunset"}
    assert p["9.01"] == p["9.0"] | {"Lotus"}
    assert p["9.02"] == p["9.01"] | {"Icebox"}
    assert cols.index("8.11") < cols.index("9.0") < cols.index("9.01") < cols.index("9.02") < cols.index("9.08")
    # Pearl is not a correction
    assert "Pearl" in p["5.0"]


def test_pools_to_changes_diff():
    t1 = datetime(2022, 6, 22, tzinfo=UTC)
    t2 = datetime(2023, 1, 10, tzinfo=UTC)
    dated = [
        ("5.0", t1, {"Ascent", "Bind", "Pearl"}),
        ("6.0", t2, {"Ascent", "Pearl", "Lotus"}),
    ]
    changes = bmps.pools_to_changes(dated, prior={"Ascent", "Bind", "Split"})
    got = {(c.map_name, c.in_pool, c.effective_at, c.patch, c.source) for c in changes}
    assert got == {
        ("Pearl", True, t1, "5.0", "seed"),
        ("Split", False, t1, "5.0", "seed"),
        ("Bind", False, t2, "6.0", "seed"),
        ("Lotus", True, t2, "6.0", "seed"),
    }


def test_release_changes_pre_5_0():
    releases = {"Ascent": "1.0", "Icebox": "1.10", "Breeze": "2.08", "Fracture": "3.05", "Pearl": "5.0", "Lotus": "6.0"}
    changes = bmps.release_changes(releases)
    got = {(c.map_name, c.effective_at.date().isoformat(), c.patch) for c in changes}
    assert got == {
        ("Ascent", "2020-06-02", "1.0"),
        ("Icebox", "2020-10-13", "1.10"),
        ("Breeze", "2021-04-27", "2.08"),
        ("Fracture", "2021-09-08", "3.05"),
    }
    assert all(c.in_pool and c.source == "seed" for c in changes)
    assert bmps.release_changes(releases) is not None
    assert {m for m in bmps.pre_5_0_pool(releases)} == {"Ascent", "Icebox", "Breeze", "Fracture"}


def test_main_stub_raises_s3b(tmp_path):
    with pytest.raises(NotImplementedError, match="S3b"):
        bmps.main(["--cache", str(tmp_path), "--out", str(tmp_path / "x.json")])
