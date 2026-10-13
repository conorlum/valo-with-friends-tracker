"""The names players use for abilities. A stored ability event carries the game's own code name
(`Gumshoe` + `E_CameraTrackingDart`); the coach must say "Spycam dart", or it guesses (the first report called
it a recon dart). The replay viewer already names every ability it draws (`ABILITY_SHAPES` in replay.js), so
that table is read here and stays the one place a name is written. `EXTRA` names the code names the viewer
does not draw. An ability in neither keeps its code name, so a gap shows instead of being papered over."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

VIEWER_JS = Path(__file__).resolve().parents[1] / "static" / "js" / "replay.js"
_ROW = re.compile(r'\[/(\^.+?\$)/,\s*\{\s*ability:\s*"([^"]+)"(?:[^}]*?label:\s*"([^"]+)")?')

# `<agent code>_<name>` for sub-objects the viewer has no row for. Names as the game shows them.
EXTRA = {
    "Gumshoe_E_CameraTrackingDart": "Spycam dart",
    "BountyHunter_E_Divebomb": "Haunt",
    "BountyHunter_Q_TetherGrenade_SphereExpansion": "Seize",
    "Clay_4_Projectile_Primary": "Paint Shells",
    "Clay_4_Projectile_Secondary": "Paint Shells",
    "Clay_4_Projectile_SecondarySpawner": "Paint Shells",
    "Clay_Q_Satchel_Arming": "Blast Pack",
    "Clay_X_Rocket": "Showstopper",
    "Smonk_DecayNade": "Meddle",
    "Sprinter_4_GroundStrike": "Fast Lane",
    "Sprinter_4_Tunnel": "Fast Lane",
    "Sprinter_Q_ElectricSphere": "Relay Bolt",
    "Thorne_4_SlowFIeld_Production": "Slow Orb",
    "Vampire_4_NearsightAoE": "Leer",
    "Vampire_Q_Heal_HealPool_AutoActivate": "Devour",
    "Vampire_Q_Heal_HealPool_High": "Devour",
}


@lru_cache(maxsize=1)
def _viewer_rows() -> tuple:
    try:
        text = VIEWER_JS.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    rows = []
    for pattern, ability, label in _ROW.findall(text):
        try:
            rows.append((re.compile(pattern), label or ability))
        except re.error:            # a pattern only JavaScript reads: that ability keeps its code name
            continue
    return tuple(rows)


def display(code: str | None, name: str | None) -> str | None:
    """The ability's name for a reader, or None when no table has it."""
    if not code or not name:
        return None
    key = f"{code}_{name}"
    if key in EXTRA:
        return EXTRA[key]
    return next((shown for pattern, shown in _viewer_rows() if pattern.match(key)), None)
