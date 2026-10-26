import ast
import sys
from pathlib import Path

from app.coach import detectors
from app.moments import constants
from app.scoring import impact

WEBAPP = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_leaf_imports_only_the_standard_library():
    names = _imports(WEBAPP / "app" / "moments" / "constants.py")
    outside = {n for n in names if n.split(".")[0] not in sys.stdlib_module_names}
    assert not outside, sorted(outside)


def test_trade_window_matches_scoring():
    assert constants.TRADE_WINDOW_S == float(impact.TRADE_WINDOW_SECONDS)


def test_team_fights_chain_on_the_trade_window():
    from app.control import fight_swing
    assert fight_swing.CHAIN_S == constants.TRADE_WINDOW_S


def test_detectors_take_the_leaf_values():
    assert detectors.TRADE_WINDOW_S == constants.TRADE_WINDOW_S
    assert detectors.TRADE_RANGE_M == constants.TRADE_RANGE_M


def test_ability_kinds_are_the_seven():
    kinds = set(constants.UTILITY_KINDS)
    assert len(kinds) == 7
    assert set(constants.ABILITY_KIND.values()) == kinds
    assert set(constants.HIT_KIND.values()) <= kinds and constants.NEAR_THEM_KINDS <= kinds
    assert not constants.NEAR_THEM_KINDS & {"smoke", "heal", "teleport"}


def test_gun_classes_are_known():
    known = {"pistol", "sheriff", "smg", "rifle", "sniper", "lmg", "shotgun", "other"}
    assert set(constants.GUN_CLASS.values()) <= known
    assert "shotgun" in constants.UNTAGGED_CLASSES
