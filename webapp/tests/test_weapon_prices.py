"""The declared price table and kill-feed name lists (spec 2026-09-12 section 6)."""
from app.scoring import agent_economy
from app.scoring import weapon_prices as wp

SPEC_PRICES = {
    "Classic": 0, "Shorty": 300, "Frenzy": 450, "Ghost": 500, "Bandit": 600, "Sheriff": 800,
    "Stinger": 1100, "Spectre": 1600, "Bucky": 850, "Judge": 1850, "Bulldog": 2050, "Ares": 1600,
    "Odin": 3200, "Guardian": 2250, "Phantom": 2900, "Vandal": 2900, "Marshal": 950,
    "Outlaw": 2400, "Operator": 4700,
}
CORPUS_NON_WEAPONS = {
    "Headhunter", "Tour De Force", "Blade Storm", "Showstopper", "Overdrive", "Not Dead Yet",
    "Hunter's Fury", "Paint Shells", "Shock Bolt", "Hot Hands", "Annihilation", "Orbital Strike",
    "Aftershock", "Guided Salvo", "Nanoswarm", "Incendiary", "Boom Bot", "Mosh Pit", "Armageddon",
    "FRAG/ment", "TURRET", "Snake Bite", "Blaze", "Razorvine", "Trailblazer", "Trapwire",
    "Special Delivery", "Curveball", "Crush", "Blast Pack", "Dizzy", "Bomb", "Fall", "Melee",
}


def test_prices_are_the_declared_table():
    assert wp.WEAPON_PRICES == SPEC_PRICES


def test_name_sets_are_the_declared_lists_and_disjoint():
    assert wp.NON_PURCHASABLE == CORPUS_NON_WEAPONS
    assert wp.UNIDENTIFIED == {"Weapon", "Unknown", "Primary"}
    assert not (set(wp.WEAPON_PRICES) & wp.NON_PURCHASABLE)
    assert not (set(wp.WEAPON_PRICES) & wp.UNIDENTIFIED)
    assert not (wp.NON_PURCHASABLE & wp.UNIDENTIFIED)


def test_classify():
    assert wp.classify("Spectre") == "priced"
    assert wp.classify("Classic") == "priced"
    assert wp.classify("Headhunter") == "non_purchasable"
    assert wp.classify("Weapon") == "unidentified"
    assert wp.classify("Vandal2") == "unrecognised"
    assert wp.classify("") == "unrecognised"


def test_known_utility_cost_has_no_fallback():
    assert agent_economy.known_utility_cost("Jett") == 550
    assert agent_economy.known_utility_cost("KAY/O") == 700
    assert agent_economy.known_utility_cost("NotAnAgent") is None
    assert agent_economy.known_utility_cost(None) is None
