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


def _pool(s):
    return frozenset(s.split())


# (patch, effective time, pool right after it), from the patch notes; 6.0 to 13.08.
EXPECTED = [
    ("6.0", "2023-01-10T14:00:00Z", "Ascent Fracture Haven Icebox Pearl Split"),
    ("6.01", "2023-01-18T14:00:00Z", "Ascent Fracture Haven Icebox Lotus Pearl Split"),
    ("6.08", "2023-04-25T14:00:00Z", "Ascent Bind Fracture Haven Lotus Pearl Split"),
    ("7.04", "2023-08-29T13:00:00Z", "Ascent Bind Breeze Haven Lotus Split"),
    ("7.05", "2023-09-06T13:00:00Z", "Ascent Bind Breeze Haven Lotus Split Sunset"),
    ("8.0", "2024-01-09T14:00:00Z", "Ascent Bind Breeze Icebox Lotus Split Sunset"),
    ("8.11", "2024-06-11T13:00:00Z", "Ascent Bind Haven Icebox Lotus Sunset"),
    ("9.0", "2024-06-25T13:00:00Z", "Abyss Ascent Bind Haven Sunset"),
    ("9.01", "2024-07-16T13:00:00Z", "Abyss Ascent Bind Haven Lotus Sunset"),
    ("9.02", "2024-07-30T13:00:00Z", "Abyss Ascent Bind Haven Icebox Lotus Sunset"),
    ("9.08", "2024-10-22T13:00:00Z", "Abyss Ascent Bind Haven Pearl Split Sunset"),
    ("10.00", "2025-01-07T14:00:00Z", "Abyss Bind Fracture Haven Lotus Pearl Split"),
    ("10.04", "2025-03-04T14:00:00Z", "Ascent Fracture Haven Icebox Lotus Pearl Split"),
    ("10.08", "2025-04-29T13:00:00Z", "Ascent Haven Icebox Lotus Pearl Split Sunset"),
    ("11.00", "2025-06-24T13:00:00Z", "Ascent Bind Corrode Haven Icebox Lotus Sunset"),
    ("11.04", "2025-08-19T13:00:00Z", "Abyss Ascent Bind Corrode Haven Lotus Sunset"),
    ("11.08", "2025-10-14T13:00:00Z", "Abyss Bind Corrode Haven Pearl Split Sunset"),
    ("12.00", "2026-01-06T14:00:00Z", "Abyss Bind Breeze Corrode Haven Pearl Split"),
    ("12.05", "2026-03-17T13:00:00Z", "Bind Breeze Fracture Haven Lotus Pearl Split"),
    ("12.08", "2026-04-28T13:00:00Z", "Ascent Breeze Fracture Haven Lotus Pearl Split"),
    ("13.00", "2026-06-23T13:00:00Z", "Ascent Breeze Haven Lotus Split Summit Sunset"),
    ("13.04", "2026-08-18T13:00:00Z", "Abyss Ascent Haven Lotus Split Summit Sunset"),
    ("13.08", "2026-10-13T13:00:00Z", "Abyss Bind Corrode Haven Lotus Summit Sunset"),
]


def _seed_history():
    from datetime import timedelta  # noqa: F401
    from app.services.map_pool import Change, PoolHistory

    seed = json.loads((WEBAPP / "app" / "data" / "map_pool_seed.json").read_text(encoding="utf-8"))
    return seed, PoolHistory(
        Change(c["map"], c["in_pool"], datetime.fromisoformat(c["effective_at"].replace("Z", "+00:00")), c["patch"], "seed")
        for c in seed["changes"]
    )


@pytest.mark.parametrize("patch,at,pool", EXPECTED)
def test_committed_seed_pool_at_each_patch(patch, at, pool):
    from datetime import timedelta

    _, hist = _seed_history()
    t = datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(minutes=1)
    assert hist.pool_at(t) == _pool(pool), patch


def test_committed_seed_current_pool_and_shape():
    seed, hist = _seed_history()
    assert hist.current_pool(now=datetime(2026, 10, 14, tzinfo=UTC)) == _pool("Abyss Bind Corrode Haven Lotus Summit Sunset")
    assert seed["changes"] and seed["patch_notes"]
    assert all(c["source_url"].startswith("https://") for c in seed["changes"])
    assert {r["patch"] for r in seed["patch_notes"]} >= {p for p, _, _ in EXPECTED}


def test_patch_url_prefers_listing_then_exceptions_then_pattern():
    base = "https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-"
    listing = {"13.08": "https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-13-08"}
    assert bmps.patch_url("13.08", listing) == listing["13.08"]
    assert bmps.patch_url("8.11", listing) == base + "811/"
    assert bmps.patch_url("9.02", listing) == base + "902/"
    assert bmps.patch_url("10.07", listing) == "https://playvalorant.com/en-us/news/valorant-patch-notes-10-07/"
    assert bmps.patch_url("5.0", listing) == base + "5-0/"
    assert bmps.patch_url("7.04", listing) == base + "7-04/"


def test_fetcher_cache_makes_no_request(tmp_path):
    import hashlib

    url = "https://example.invalid/page"
    (tmp_path / hashlib.sha1(url.encode()).hexdigest()[:12]).write_text("cached body", encoding="utf-8")
    f = bmps.Fetcher(tmp_path)
    assert f(url) == "cached body"
    assert f.requests == 0


def _page(date, lines):
    body = "".join(f"<p>{l}</p>" for l in lines)
    ld = json.dumps({"@type": "NewsArticle", "datePublished": date})
    return f'<html><head><script type="application/ld+json">{ld}</script></head><body>{body}</body></html>'


def test_build_seed_with_fake_fetch():
    wiki = FIXTURE.read_text(encoding="utf-8")
    pages = {bmps.WIKI_URL: json.dumps({"parse": {"wikitext": {"*": wiki}}}), bmps.LISTING_URL: "<html></html>"}
    asked = []

    def fake(url):
        asked.append(url)
        if url in pages:
            return pages[url]
        return _page("2023-05-05T14:00:00.000Z", ["nothing"])

    seed = bmps.build_seed(fake, corrections=[], built_at=datetime(2026, 10, 14, tzinfo=UTC))
    assert seed["built_at"] == "2026-10-14T00:00:00Z"
    assert seed["sources"] == [bmps.WIKI_URL, bmps.LISTING_URL]
    by_patch = {r["patch"]: r for r in seed["patch_notes"]}
    assert by_patch["8.11"]["url"].endswith("valorant-patch-notes-811/")
    assert by_patch["6.0"]["published_at"] == "2023-05-05T14:00:00Z"
    ins = [c for c in seed["changes"] if c["patch"] == "5.0" and c["in_pool"]]
    assert ins and all(c["effective_at"] == "2022-06-22T00:00:00Z" for c in ins)
    assert {"url", "patch", "published_at"} == set(seed["patch_notes"][0])
    assert {"map", "in_pool", "effective_at", "patch", "source_url"} == set(seed["changes"][0])
    assert len(asked) == len(set(asked))
