from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.patch_notes import (
    PoolChange,
    parse_listing,
    parse_patch_notes,
    pool_lines,
)

FIXTURES = Path(__file__).parent / "fixtures" / "patch_notes"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _pairs(changes) -> list[tuple[str, bool]]:
    return [(c.map_name, c.in_pool) for c in changes]


# ---- real pages ------------------------------------------------------------


def test_patch_12_08():
    notes = parse_patch_notes(_read("patch_12_08.html"))
    assert set(_pairs(notes.changes)) == {("Ascent", True), ("Bind", False)}
    assert len(notes.changes) == 2
    assert notes.published_at == datetime(2026, 4, 28, 13, 0, tzinfo=timezone.utc)
    assert notes.mentions_pool and not notes.is_miss


def test_patch_13_00():
    notes = parse_patch_notes(_read("patch_13_00.html"))
    assert set(_pairs(notes.changes)) == {
        ("Summit", True),
        ("Sunset", True),
        ("Fracture", False),
        ("Pearl", False),
    }
    assert len(notes.changes) == 4
    assert not notes.is_miss


def test_patch_13_04():
    notes = parse_patch_notes(_read("patch_13_04.html"))
    assert set(_pairs(notes.changes)) == {("Abyss", True), ("Breeze", False)}
    assert len(notes.changes) == 2


def test_patch_13_06_has_no_pool_change_and_is_not_a_miss():
    notes = parse_patch_notes(_read("patch_13_06.html"))
    assert notes.changes == ()
    assert notes.mentions_pool is False  # it only says "the active map pool"
    assert notes.is_miss is False
    assert notes.published_at == datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc)


# ---- one-line cases --------------------------------------------------------


def test_two_maps_out_of_competitive_and_deathmatch():
    got = pool_lines("ABYSS and CORRODE are OUT of Competitive and Deathmatch queues.")
    assert _pairs(got) == [("Abyss", False), ("Corrode", False)]


def test_is_back_in():
    assert _pairs(pool_lines("Abyss is back IN the COMPETITIVE queue.")) == [("Abyss", True)]


def test_are_back_in_no_trailing_punctuation():
    got = pool_lines("Pearl and Split are BACK in the COMPETITIVE QUEUE")
    assert _pairs(got) == [("Pearl", True), ("Split", True)]


def test_no_competitive_queue_means_nothing():
    assert pool_lines("ASCENT is IN the Deathmatch queue.") == ()


def test_enters_other_queues_means_nothing():
    assert pool_lines("Split enters Unrated & Swift Play queues") == ()


def test_comma_list_with_and():
    got = pool_lines("Abyss, Corrode, and Bind are IN the Competitive queue.")
    assert _pairs(got) == [("Abyss", True), ("Corrode", True), ("Bind", True)]


def test_sentence_split_ignores_leading_sentence():
    got = pool_lines("Welcome back. Abyss is IN the Competitive queue.")
    assert _pairs(got) == [("Abyss", True)]


def test_unknown_map_outside_a_pool_block_is_dropped():
    assert pool_lines("Premier is IN the Competitive queue.") == ()


def test_unknown_map_inside_a_pool_block_is_accepted():
    got = pool_lines("Newmap is IN the Competitive queue.", in_pool_block=True)
    assert _pairs(got) == [("Newmap", True)]


def test_duplicates_removed_order_kept():
    got = pool_lines("Bind is IN the Competitive queue. Bind is IN the Competitive queue.")
    assert _pairs(got) == [("Bind", True)]


def test_name_must_be_letters_and_at_most_three_words():
    assert pool_lines("Ascent 2 is IN the Competitive queue.", in_pool_block=True) == ()
    assert (
        pool_lines("The very long map name is IN the Competitive queue.", in_pool_block=True)
        == ()
    )


# ---- html structure --------------------------------------------------------


def test_nested_list_ends_the_parents_text():
    html = (
        "<ul><li>Map Pool Updates<ul><li><strong>ASCENT</strong> is <strong>IN</strong> "
        "the Competitive queue.</li></ul></li></ul>"
    )
    notes = parse_patch_notes(html)
    assert _pairs(notes.changes) == [("Ascent", True)]


def test_unknown_map_accepted_under_pool_item_but_not_elsewhere():
    under = (
        "<ul><li>Map Pool Updates<ul><li>Newmap is IN the Competitive queue.</li></ul></li></ul>"
    )
    assert _pairs(parse_patch_notes(under).changes) == [("Newmap", True)]
    elsewhere = "<ul><li>Newmap is IN the Competitive queue.</li></ul>"
    assert parse_patch_notes(elsewhere).changes == ()


def test_unknown_map_accepted_under_pool_heading():
    html = "<h3>Map Pool Updates</h3><ul><li>Newmap is IN the Competitive queue.</li></ul>"
    assert _pairs(parse_patch_notes(html).changes) == [("Newmap", True)]
    after = html + "<h3>Modes</h3><ul><li>Other is IN the Competitive queue.</li></ul>"
    assert _pairs(parse_patch_notes(after).changes) == [("Newmap", True)]


def test_miss_when_pool_mentioned_but_unparseable():
    html = "<ul><li>Map Pool Updates</li><li>Ascent joins the Competitive rotation.</li></ul>"
    notes = parse_patch_notes(html)
    assert notes.mentions_pool
    assert notes.changes == ()
    assert notes.is_miss


def test_unknown_map_outside_block_makes_a_miss():
    html = "<p>Map rotation news</p><ul><li>Premier is IN the Competitive queue.</li></ul>"
    notes = parse_patch_notes(html)
    assert notes.changes == () and notes.is_miss


def test_only_rich_text_sections_are_read_when_present():
    html = (
        "<p>Ascent is IN the Competitive queue.</p>"
        '<section data-testid="ArticleRichTextBlade"><p>Bind is OUT of the Competitive queue.</p></section>'
        "<footer><p>Split is IN the Competitive queue.</p></footer>"
    )
    assert _pairs(parse_patch_notes(html).changes) == [("Bind", False)]


def test_nbsp_and_whitespace_are_normalised():
    html = "<p>SUMMIT&nbsp;and&nbsp;<strong>SUNSET&nbsp;</strong>are&nbsp;<strong>IN</strong>\n the COMPETITIVE queue.</p>"
    assert _pairs(parse_patch_notes(html).changes) == [("Summit", True), ("Sunset", True)]


def test_published_at_from_graph_node_and_missing():
    ld = (
        '<script type="application/ld+json">{"@graph":[{"@type":"Organization"},'
        '{"@type":"TechArticle","datePublished":"2025-04-15T13:00:00.000Z"}]}</script>'
    )
    notes = parse_patch_notes(ld + "<p>Nothing here.</p>")
    assert notes.published_at == datetime(2025, 4, 15, 13, 0, tzinfo=timezone.utc)
    assert parse_patch_notes("<p>Nothing here.</p>").published_at is None


# ---- listing ---------------------------------------------------------------


def test_listing_keeps_patch_notes_items_only():
    items = parse_listing(_read("listing.html"))
    assert len(items) == 4
    assert [i.title for i in items] == [
        "VALORANT Patch Notes 13.08",
        "VALORANT Patch Notes 13.06",
        "VALORANT Patch Notes 12.08",
        "VALORANT Patch Notes 10.07",
    ]
    urls = [i.url for i in items]
    assert urls[0] == "https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-13-08"
    assert urls[3].startswith("https://playvalorant.com/en-us/news/valorant-patch-notes-10-07")
    assert all(u.startswith("https://") for u in urls)
    assert items[0].published_at == datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)
    assert items[2].published_at == datetime(2026, 4, 28, 13, 0, tzinfo=timezone.utc)
    assert not any("youtube" in u for u in urls)
    assert not any(i.title == "Patch 11.08 Gameplay Updates" for i in items)


def test_listing_without_next_data_is_empty():
    assert parse_listing("<html><body>nothing</body></html>") == []


def test_pool_change_is_frozen():
    with pytest.raises(Exception):
        PoolChange("Ascent", True).in_pool = False  # type: ignore[misc]
