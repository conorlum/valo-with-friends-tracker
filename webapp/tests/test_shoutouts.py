from app.services.shoutouts import assign_shoutouts

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
