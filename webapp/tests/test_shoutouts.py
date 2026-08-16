from app.services.shoutouts import PlayerShoutout, assign_shoutouts, display_most_active_as_percentage

CUSTOM_CATEGORIES = [
    ("wins_together", "Best Duo", "won {v}% of rounds together"),
]


def test_assign_shoutouts_uses_custom_categories_param():
    roster = [(1, "Alice#NA1", "Jett"), (2, "Bob#NA1", "Omen")]
    raw_dicts = {"wins_together": {1: 80, 2: 40}}
    shoutouts = assign_shoutouts(roster, raw_dicts, {}, categories=CUSTOM_CATEGORIES)

    alice = next(s for s in shoutouts if s.player_id == 1)
    assert alice.headline == "Best Duo"
    assert alice.detail == "won 80% of rounds together"


def test_assign_shoutouts_default_categories_unchanged():
    roster = [(1, "Alice#NA1", "Jett")]
    raw_dicts = {"entry_kill_counts": {1: 3}}
    shoutouts = assign_shoutouts(roster, raw_dicts, {})
    assert shoutouts[0].headline == "Entry Fragger"


def test_most_active_detail_is_a_percentage_of_rounds_played():
    shoutouts = [
        PlayerShoutout(1, "Alice#NA1", "Jett", "Most Active", "9 rounds with a kill or assist"),
        PlayerShoutout(2, "Bob#NA1", "Omen", "Entry Fragger", "3 first bloods"),
        PlayerShoutout(3, "Cy#NA1", "Sova", "Most Active", "4 rounds with a kill or assist"),
    ]
    display_most_active_as_percentage(shoutouts, {1: 9, 2: 5, 3: 4}, {1: 12, 2: 12})

    assert shoutouts[0].detail == "75% of rounds with a kill or assist"
    assert shoutouts[1].detail == "3 first bloods"  # other categories untouched
    assert shoutouts[2].detail == "4 rounds with a kill or assist"  # no rounds played known: left as-is
