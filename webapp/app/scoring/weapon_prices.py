"""Declared weapon prices and kill-feed name lists.

Spec: docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md,
section 6. Current prices checked 2026-09-12; one table for every date is an
accepted limitation. A name in none of the three sets is UNRECOGNISED and
makes the bonus-denial calculator abstain rather than guess.
"""

WEAPON_PRICES = {
    "Classic": 0, "Shorty": 300, "Frenzy": 450, "Ghost": 500, "Bandit": 600, "Sheriff": 800,
    "Stinger": 1100, "Spectre": 1600, "Bucky": 850, "Judge": 1850,
    "Bulldog": 2050, "Guardian": 2250, "Phantom": 2900, "Vandal": 2900,
    "Marshal": 950, "Outlaw": 2400, "Operator": 4700, "Ares": 1600, "Odin": 3200,
}

NON_PURCHASABLE = frozenset({
    "Headhunter", "Tour De Force", "Blade Storm", "Showstopper", "Overdrive", "Not Dead Yet",
    "Hunter's Fury", "Paint Shells", "Shock Bolt", "Hot Hands", "Annihilation", "Orbital Strike",
    "Aftershock", "Guided Salvo", "Nanoswarm", "Incendiary", "Boom Bot", "Mosh Pit", "Armageddon",
    "FRAG/ment", "TURRET", "Snake Bite", "Blaze", "Razorvine", "Trailblazer", "Trapwire",
    "Special Delivery", "Curveball", "Crush", "Blast Pack", "Dizzy", "Bomb", "Fall", "Melee",
})

UNIDENTIFIED = frozenset({"Weapon", "Unknown", "Primary"})


def classify(name: str) -> str:
    if name in WEAPON_PRICES:
        return "priced"
    if name in NON_PURCHASABLE:
        return "non_purchasable"
    if name in UNIDENTIFIED:
        return "unidentified"
    return "unrecognised"
