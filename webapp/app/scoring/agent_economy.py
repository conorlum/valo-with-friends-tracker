# Per-agent credit cost to buy every non-ultimate ability once (excludes any
# free signature-ability charge), and the credit-equivalent value of that
# free charge where one exists. Sourced by hand from the Valorant wiki,
# cross-checked with the user rather than scraped (see
# docs/superpowers/plans/2026-07-30-squad-page.md's follow-up conversation).
# Keyed the same way as app.templates.agent_icon_slug: "/" stripped, lowercased.
ARMOR_COST = 1000  # heavy shields -- the ceiling for a survivor's "normal" top-up spend

AGENT_UTILITY_COST: dict[str, int] = {
    "astra": 600,
    "breach": 700,
    "brimstone": 650,
    "chamber": 1000,
    "clove": 600,
    "cypher": 600,
    "deadlock": 700,
    "fade": 700,
    "gekko": 550,
    "harbor": 500,
    "iso": 500,
    "jett": 550,
    "kayo": 700,
    "killjoy": 600,
    "miks": 800,
    "neon": 500,
    "omen": 600,
    "phoenix": 600,
    "raze": 700,
    "reyna": 650,
    "sage": 700,
    "skye": 700,
    "sova": 700,
    "tejo": 750,
    "veto": 600,
    "viper": 500,
    "vyse": 500,
    "waylay": 600,
    "yoru": 600,
}

# Credit-equivalent value tracker.gg's loadout figure assigns to an agent's
# free (re-buyable, but free the first time each round) signature-ability
# charge -- inflates the displayed loadout number even though it cost
# nothing. Agents with no such ability are absent (treated as 0).
AGENT_FREE_ABILITY_CREDITS: dict[str, int] = {
    "astra": 150,
    "brimstone": 100,
    "clove": 150,
    "miks": 100,
    "omen": 150,
    "phoenix": 250,
    "reyna": 250,
    "skye": 250,
    "tejo": 150,
    "yoru": 150,
}

# Fallback ceiling for an agent name that doesn't match the table above
# (unknown/renamed agent) -- the median of the known utility costs.
DEFAULT_UTILITY_COST = 650


def _normalize_agent(agent: str | None) -> str:
    return (agent or "").replace("/", "").lower()


def max_utility_cost(agent: str | None) -> int:
    """Max credits this agent could spend on non-ultimate abilities (their
    free signature charge excluded) in a single round."""
    return AGENT_UTILITY_COST.get(_normalize_agent(agent), DEFAULT_UTILITY_COST)


def free_ability_credits(agent: str | None) -> int:
    """Credit-equivalent value of this agent's free ability charge, which
    inflates the raw loadout stat without costing the player anything."""
    return AGENT_FREE_ABILITY_CREDITS.get(_normalize_agent(agent), 0)
