"""Every held ability has a kit name (app/replays/held_names.py, static/data/held_abilities.json).

An unnamed held ability ("Ability out" in the players panel, "an ability" in the coach) is a bug: these tests fail on
any archetype seen in an archived replay that the table lacks, and condense reports one it meets in a new replay
(Site health: held_ability_unnamed)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.coach import held
from app.replays import held_names
from app.services import site_health

WEBAPP = Path(__file__).resolve().parents[2]
FIXTURE = WEBAPP / "tests/fixtures/held_ability_archetypes.txt"
KITS = json.loads((WEBAPP / "app/static/data/abilities.json").read_text(encoding="utf-8"))
AGENTS = json.loads((WEBAPP / "app/static/data/agents.json").read_text(encoding="utf-8"))
# Agents no archived replay has shown holding an ability yet. Their items reach Site health until a replay with one
# is harvested (scripts: export it, add its Default__Ability_* names to the fixture and the table) and they leave here.
AWAITING_A_REPLAY = {"Cable"}   # Deadlock


def archetypes():
    return [line for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]


def test_every_held_ability_seen_in_a_replay_has_an_entry():
    missing = [a for a in archetypes() if not held_names.lookup(*held_names.parts(a))[0]]
    assert missing == []
    assert len(archetypes()) >= 140


def test_every_entry_is_a_kit_name_of_that_agent():
    for code, kit in held_names.table().items():
        agent = AGENTS.get(code)
        for item, name in kit.items():
            if name is None:
                continue
            if agent is not None:
                assert name in KITS[agent], (code, item, name)
            else:   # Gekko's creatures (Pawn, AggroBot) and the knife's base ability have no agent code of their own
                assert any(name in k for k in KITS.values()), (code, item, name)


def test_every_agent_has_entries_unless_awaiting_a_replay():
    assert set(AGENTS) - set(held_names.table()) == AWAITING_A_REPLAY


@pytest.mark.parametrize("archetype,expected", [
    ("Default__Ability_Wraith_4_Smoke_C", ("Wraith", "C", "Smoke")),
    ("Default__Ability_E_Aggrobot_DiscTurret_C", ("Aggrobot", "E", "DiscTurret")),
    ("Default__Ability_Nox_BarbedWire_C", ("Nox", None, "BarbedWire")),
    ("Default__Ability_Cashew_X_Airstrike_C", ("Cashew", "X", "Airstrike")),
    ("Default__BombEquippable_C", None),
])
def test_parts_split_an_archetype_as_the_hold_row_stores_it(archetype, expected):
    assert held_names.parts(archetype) == expected


def test_the_replay_slot_letter_is_not_the_key():
    assert held_names.lookup("Wraith", "C", "Smoke") == (True, "Dark Cover")
    assert held_names.lookup("Phoenix", "C", "Molotov_Production") == (True, "Hot Hands")
    assert held_names.lookup("Cashew", "E", "Airstrike") == (True, "Guided Salvo")
    assert held_names.lookup("Cashew", "X", "Airstrike") == (True, "Armageddon")
    assert held_names.lookup("Wushu", None, "Passive_Glide") == (True, None)
    assert held_names.lookup("Wraith", "C", "Unheard") == (False, None)


def test_the_coach_names_held_and_possessed_items_from_the_tables():
    assert held.item_name("Omen", {"cat": "ability", "code": "Wraith", "key": "C", "name": "Smoke"}) == "Dark Cover"
    assert held.item_name("Omen", {"cat": "ability", "code": "Wraith", "key": "C", "name": "Unheard"}) == held.UNNAMED
    assert held.item_name("Jett", {"cat": "ability", "code": "Wushu", "key": None, "name": "Passive_Glide"}) is None
    camera = {"k": "ability", "code": "Gumshoe", "name": "E_PossessableCamera"}
    assert held.ability_name("Cypher", camera) == "Spycam"


# Condense's report of an unnamed hold: tests/replays/test_replay_extras.py,
# test_held_abilities_without_a_kit_name_are_reported_not_named.

UUID = "38968d35-6369-4288-8781-8daff59ca311"


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(site_health, "record", lambda *a, **k: seen.append(("record", a, k)))
    monkeypatch.setattr(site_health, "resolve", lambda *a, **k: seen.append(("resolve", a, k)))
    return seen


def stored(action):
    return SimpleNamespace(action=action, replay_id=7)


def test_held_ability_unnamed_is_a_recorded_kind_on_the_card():
    from app.services import replay_errors

    assert site_health.HELD_KIND in site_health.RECORDED_KINDS
    assert site_health.HELD_KIND in dict(replay_errors.KINDS) and site_health.HELD_KIND in replay_errors.EVENT_KINDS
    assert replay_errors.GROUPS[site_health.HELD_KIND] == "Data"


@pytest.mark.parametrize("action", ["stored", "replaced"])
def test_a_stored_condense_with_unnamed_holds_records_the_match(calls, action):
    site_health.note_held_names(stored(action), {"extras": {"held_unnamed": ["Wraith C/Unheard"]}}, UUID.upper())
    [(op, args, kwargs)] = calls
    assert op == "record" and args[:2] == (site_health.HELD_KIND, UUID) and "Wraith C/Unheard" in args[2]
    assert args[3] == {"replay_id": 7, "names": ["Wraith C/Unheard"]}


@pytest.mark.parametrize("report", [{"extras": {"held_unnamed": []}}, {"extras": {}}, {}, None])
def test_a_stored_clean_condense_resolves(calls, report):
    site_health.note_held_names(stored("stored"), report, UUID)
    assert [(op, args) for op, args, _ in calls] == [("resolve", (site_health.HELD_KIND, UUID))]


@pytest.mark.parametrize("action", ["unchanged", "kept_existing", "refused"])
def test_an_action_that_stored_nothing_is_left_alone(calls, action):
    site_health.note_held_names(stored(action), {"extras": {"held_unnamed": ["x"]}}, UUID)
    assert calls == []


def test_the_policy_never_raises(monkeypatch):
    monkeypatch.setattr(site_health, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue")))
    site_health.note_held_names(stored("stored"), {"extras": {"held_unnamed": ["x"]}}, UUID)
