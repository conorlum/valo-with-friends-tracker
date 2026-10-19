from datetime import datetime

from app.services import agent_roles as ar

ALL_AGENTS = {
    "Duelist": ["Iso", "Jett", "Neon", "Phoenix", "Raze", "Reyna", "Waylay", "Yoru"],
    "Initiator": ["Breach", "Fade", "Gekko", "KAY/O", "Skye", "Sova", "Tejo"],
    "Controller": ["Astra", "Brimstone", "Clove", "Harbor", "Miks", "Omen", "Viper"],
    "Sentinel": ["Chamber", "Cypher", "Deadlock", "Killjoy", "Sage", "Veto", "Vyse"],
}


def test_table_has_four_roles_and_29_agents():
    assert ar.ROLES == ("duelist", "initiator", "controller", "sentinel")
    seen = []
    for role, agents in ALL_AGENTS.items():
        for a in agents:
            assert ar.role_of(a) == role.lower()
            seen.append(a)
    assert len(seen) == 29 == len(set(seen))
    assert ar._AGENT_ROLE.keys() == set(seen)


def test_role_of():
    assert ar.role_of("KAY/O") == "initiator"
    assert ar.role_of("Jett") == "duelist"
    assert ar.role_of("Nobody") is None
    assert ar.role_of(None) is None


def test_parse_filter():
    played = {"Jett", "Newbie"}
    assert ar.parse_filter("role:Duelist", played) == "role:duelist"
    assert ar.parse_filter("role:sentinel", played) == "role:sentinel"
    assert ar.parse_filter("agent:Jett", played) == "agent:Jett"
    assert ar.parse_filter("agent:Newbie", played) == "agent:Newbie"
    for bad in (None, "", "bogus", "role:", "role:healer", "agent:Nobody", "agent:", "agent:jett"):
        assert ar.parse_filter(bad, played) == ""


def test_filter_agents():
    played = ["Jett", "Raze", "Sage", "Newbie"]
    assert ar.filter_agents("role:duelist", played) == frozenset({"Jett", "Raze"})
    assert ar.filter_agents("role:controller", played) == frozenset()
    assert ar.filter_agents("agent:Sage", played) == frozenset({"Sage"})


def test_filter_label():
    assert ar.filter_label("role:duelist") == "Duelist"
    assert ar.filter_label("agent:KAY/O") == "KAY/O"
    assert ar.filter_label("") == ""


def test_rank_agents():
    d1, d2 = datetime(2026, 1, 1), datetime(2026, 2, 1)
    rows = [
        ("Zed", 3, d1),
        ("Alpha", 3, d1),
        ("Recent", 3, d2),
        ("Undated", 3, None),
        ("Top", 5, None),
        ("Low", 1, d2),
    ]
    assert ar.rank_agents(rows) == ["Top", "Recent", "Alpha", "Zed", "Undated", "Low"]
