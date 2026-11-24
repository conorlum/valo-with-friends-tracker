"""Reason classes cover the stored vocabulary, including indirect utility emissions."""

import ast
from itertools import product
from pathlib import Path
import re

import pytest

from app.replays.control_reasons import CLASSES, SIGHT_SOUND, TWO_KIND, classify


APP = Path(__file__).resolve().parents[2] / "app"
TABLE = {
    "found": "seen kill plant damage gunfire footsteps tap orb revived neural_theft trip reveal "
             "drone_dart camera_dart tejo_drone waylay_recall trip_destroyed camera_destroyed hush_suspension "
             "prowler_hit wingman_hit boom_bot_hit seekers_hit hunters_fury_hit seize_hit",
    "roughly_placed": "knife_hit knife_all leer_seen nightfall_count dizzy_count thrash_count storm_surge_count fakeout_direction",
    "ruled_out": "knife_zero knife_missed skye_no_cue hypothesis_cleared omen_seen "
                 "nightfall_zero dizzy_zero thrash_zero storm_surge_zero "
                 "prowler_miss wingman_miss boom_bot_miss seekers_miss hunters_fury_miss",
    "lost_track": "omen_unheard yoru_drift_unheard yoru_beacon",
    "other": "died omen_channel",
}


@pytest.mark.parametrize("expected,reasons", TABLE.items())
def test_table(expected, reasons):
    for reason in reasons.split():
        assert classify(reason) == expected
        assert CLASSES[reason] == expected


@pytest.mark.parametrize("reason", ["haunt", "recon"])
def test_two_kind_reasons_and_old_rows(reason):
    assert classify(reason, "locate") == "found"
    assert classify(reason, "exclude") == "ruled_out"
    assert classify(reason) == "ruled_out"
    assert classify(reason, None) == "ruled_out"


def test_vocabulary_and_unknown():
    assert set(CLASSES) == {r for reasons in TABLE.values() for r in reasons.split()}
    assert TWO_KIND == {"haunt", "recon"}
    assert SIGHT_SOUND == {"seen", "footsteps", "gunfire", "damage", "kill", "tap", "orb"}
    assert classify("made_up") == "other"
    assert classify("made_up", "locate") == "other"


def _bindings(nodes):
    """All assignments in a scope, including branches and tuple unpacking."""
    bindings = {}

    def bind(target, value):
        if isinstance(target, ast.Name):
            bindings.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for i, child in enumerate(target.elts):
                bind(child, ast.Subscript(value=value, slice=ast.Constant(i)))
        elif isinstance(target, ast.Subscript):
            bind(target.value, value)

    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                bind(target, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            bind(node.target, node.value)
    return bindings


def _values(node, bindings, seen=frozenset()):
    """Resolve possible literals without importing/executing the control engine.

    Dictionaries represent their possible values; tuples retain their positions so
    `kind, reason = plan[slot]` does not mistake operation kinds for reasons.
    Unknown expressions fail closed rather than silently shrinking the sweep.
    """
    if isinstance(node, ast.Constant):
        return {node.value}
    if isinstance(node, ast.Name):
        assert node.id not in seen, f"cyclic reason assignment: {node.id}"
        assert node.id in bindings, f"unresolved reason variable: {node.id}"
        return set().union(*(_values(v, bindings, seen | {node.id}) for v in bindings[node.id]))
    if isinstance(node, (ast.Tuple, ast.List)):
        return set(product(*(_values(v, bindings, seen) for v in node.elts)))
    if isinstance(node, ast.Dict):
        return set().union(*(_values(v, bindings, seen) for v in node.values))
    if isinstance(node, ast.DictComp):
        return _values(node.value, bindings, seen)
    if isinstance(node, ast.IfExp):
        return _values(node.body, bindings, seen) | _values(node.orelse, bindings, seen)
    if isinstance(node, ast.Subscript):
        values = _values(node.value, bindings, seen)
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            return {v[node.slice.value] for v in values}
        return values
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        values = _values(node.func.value, bindings, seen)
        return values | (_values(node.args[1], bindings, seen) if len(node.args) > 1 else set())
    pytest.fail(f"unresolved reason expression: {ast.dump(node)}")


def _utility_emissions(source):
    tree = ast.parse(source)
    globals_ = _bindings(tree.body)
    emissions = []
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id in ("Info", "zero_hits")]
    for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        bindings = globals_ | _bindings(ast.walk(fn))
        for call in (n for n in ast.walk(fn) if n in calls):
            parameters = (("rnd", "t", "side", "reason") if call.func.id == "zero_hits"
                          else ("t", "side", "slot", "kind", "reason"))
            args = dict(zip(parameters, call.args))
            args.update({kw.arg: kw.value for kw in call.keywords})
            if fn.name == "zero_hits" and call.func.id == "Info":
                # This factory forwards the caller's reason. Check its fixed operation here;
                # every concrete reason is swept at the zero_hits call, including future readers.
                assert isinstance(args["kind"], ast.Constant) and args["kind"].value == "exclude"
                assert isinstance(args["reason"], ast.Name) and args["reason"].id == "reason"
                calls.remove(call)
                continue
            if call.func.id == "zero_hits":
                args["kind"] = ast.Constant("exclude")
            reasons = _values(args["reason"], bindings)
            kinds = _values(args["kind"], bindings)
            assert reasons and kinds, f"empty emission at {fn.name}:{call.lineno}"
            emissions.extend((r, k, f"utility.py:{call.lineno} ({fn.name})") for r in reasons for k in kinds)
            calls.remove(call)
    assert not calls, "Info calls outside a resolved function"
    return emissions


def test_source_sweep():
    source = (APP / "control/utility.py").read_text(encoding="utf-8")
    emissions = _utility_emissions(source)
    reasons = {r for r, _, _ in emissions}
    globals_ = _bindings(ast.parse(source).body)
    for name in ("REVEAL_REASONS", "PULSE_SOURCES"):
        reasons |= _values(ast.Name(id=name), globals_)

    # Engine's locating kinds, plus its direct reason appends. Utility reasons
    # enter via info.reason and are covered by the Info-call sweep above.
    reasons |= {"died", "seen", "hypothesis_cleared", "kill", "plant", "damage",
                "gunfire", "footsteps", "tap", "orb"}
    engine = ast.parse((APP / "control/engine.py").read_text(encoding="utf-8"))
    for node in ast.walk(engine):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append" and isinstance(node.func.value, ast.Subscript)
                and isinstance(node.func.value.value, ast.Attribute)
                and node.func.value.value.attr == "reasons"):
            reason = node.args[0].elts[3]
            if isinstance(reason, ast.Constant):
                reasons.add(reason.value)
            else:
                assert ast.unparse(reason) == "info.reason", "new indirect engine reason needs a sweep"

    js = (APP / "static/js/replay_control.js").read_text(encoding="utf-8")
    block = re.search(r"\bREASON_TEXT\s*=\s*\{(.*?)\};", js, re.S)
    assert block, "REASON_TEXT not found"
    keys = re.findall(r'(?:^|,)\s*(?:"([\w]+)"|\'([\w]+)\'|([\w]+))\s*:', block[1])
    assert keys, "no REASON_TEXT keys found"
    reasons |= {next(part for part in key if part) for key in keys}
    missing = reasons - (set(CLASSES) | TWO_KIND)
    assert not missing, f"unclassified reasons: {sorted(missing)}; emissions: {emissions}"


def test_only_haunt_and_recon_depend_on_kind():
    source = (APP / "control/utility.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    bindings = _bindings(tree.body)
    kinds = _values(ast.Name(id="KINDS"), bindings).pop() + ("cleanup", None)
    varying = {r for r in set(CLASSES) | TWO_KIND if len({classify(r, k) for k in kinds}) > 1}
    assert varying == TWO_KIND == {"haunt", "recon"}
    emissions = _utility_emissions(source)
    assert {k for r, k, _ in emissions if r == "omen_channel"} == {"pause", "resume"}
    for reason in {r for r, _, _ in emissions} - TWO_KIND:
        assert len({classify(reason, k) for r, k, _ in emissions if r == reason}) == 1


@pytest.mark.parametrize("called", [False, True])
def test_sweep_resolves_zero_hits_reasons_at_its_callers(called):
    source = '''
def zero_hits(rnd, t, side, reason, *, needs, **kwargs):
    return [Info(t, side, slot, "exclude", reason, **kwargs) for slot in living]
'''
    if called:
        source += '''
def reader():
    reason = "new_zero"
    zero_hits(rnd, t, side, reason, needs="status")
    zero_hits(rnd, t, side, reason="another_zero", needs="reveal")
'''
    emissions = _utility_emissions(source)
    assert {(r, k) for r, k, _ in emissions} == (
        {("new_zero", "exclude"), ("another_zero", "exclude")} if called else set())


def test_sweep_resolves_indirect_mapping_and_conditional_reasons():
    # Same tuple-valued per-slot mapping and unpacking used by the knife reader,
    # with new reasons so this proves the sweep does not rely on known names.
    source = '''
REASONS = {"source": "new_reveal"}
def reader():
    plan = {s: ("restrict", "new_hit") if s in hit else ("exclude", "new_miss") for s in living}
    kind, reason = plan[slot]
    Info(t, side, slot, kind, reason)
    alias = REASONS.get(source, "new_fallback")
    Info(t, side, slot, kind="locate", reason=alias)
'''
    emissions = _utility_emissions(source)
    assert {r for r, _, _ in emissions} == {"new_hit", "new_miss", "new_reveal", "new_fallback"}


def test_sweep_rejects_unresolved_reason():
    with pytest.raises(AssertionError, match="unresolved reason variable"):
        _utility_emissions('def reader():\n    Info(t, side, slot, "locate", unknown)\n')
