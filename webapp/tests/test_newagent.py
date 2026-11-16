"""New-agent tooling writes only the selected identity and inventories replay objects offline."""

import ast
import io
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import newagent  # noqa: E402
import vendor_ability_icons as vendor  # noqa: E402

WEBAPP = Path(__file__).resolve().parents[1]
TABLES = (
    "app/static/data/agents.json",
    "app/data/agent_roles.json",
    "app/static/data/abilities.json",
    "app/critutil/constants.py",
)
CLASSES = "C=status,Q=blind,E=dash"


def fake_agent(name, code):
    return {
        "displayName": name, "developerName": code, "isPlayableCharacter": True,
        "role": {"displayName": "Duelist"}, "displayIcon": f"fake://{code}/agent",
        "abilities": [
            {"slot": slot, "displayName": ability, "displayIcon": f"fake://{code}/{slot}"}
            for slot, ability in (("Grenade", "Quiet Field"), ("Ability1", "Redirect Flash"),
                                  ("Ability2", "Redirect Pull"), ("Ultimate", "Silence"))
        ] + [{"slot": "Passive", "displayName": "Passive", "displayIcon": None}],
    }


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _without_hush(root):
    """The real tables now hold Hush; put the copies back to the state before it was added."""
    def rewrite(relative, pattern, replacement=b""):
        path = root / relative
        data = path.read_bytes()
        out, count = re.subn(pattern, replacement, data, flags=re.DOTALL)
        assert count == 1, relative
        path.write_bytes(out)

    rewrite(TABLES[0], rb' {2}"Paprika": "Hush",\r?\n')
    rewrite(TABLES[1], rb'"Hush", ')
    rewrite(TABLES[2], rb' {2}"Hush": \{.*?\r?\n {2}\},\r?\n')
    rewrite(TABLES[3], rb' {4}"Hush": \{[^\n]*\n')


@pytest.fixture
def identity_case(tmp_path, monkeypatch):
    root = tmp_path / "webapp"
    for relative in TABLES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WEBAPP / relative, target)
    _without_hush(root)
    # Unrelated art must remain intact too.
    for relative in ("app/static/img/agents/waylay.png", "app/static/img/abilities/waylay/ability1.png"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WEBAPP / relative, target)
    agents = [fake_agent("Waylay", "Terra"), fake_agent("Hush", "Paprika")]
    api = tmp_path / "api.json"
    api.write_text(json.dumps({"status": 200, "data": agents}), encoding="utf-8")
    png = io.BytesIO()
    Image.new("RGB", (3, 2), "red").save(png, format="PNG")
    calls = []

    def fetch(url):
        assert url.startswith("fake://Paprika/"), "identity fetched another agent"
        calls.append(url)
        return png.getvalue()

    monkeypatch.setattr(newagent, "fetch", fetch)
    args = ["identity", "Hush", "--root", str(root), "--api-json", str(api), "--classes", CLASSES]
    return root, args, agents, calls, png.getvalue()


def test_identity_only_selected_agent_and_idempotent(identity_case, capsys):
    root, args, agents, calls, png = identity_case
    before = snapshot(root)
    assert newagent.main(args) == 0
    after = snapshot(root)
    old_agents = json.loads(before[TABLES[0]])
    assert json.loads(after[TABLES[0]]) == {**old_agents, "Paprika": "Hush"}
    newline = b"\r\n" if b"\r\n" in before[TABLES[0]] else b"\n"
    assert after[TABLES[0]].replace(b'  "Paprika": "Hush",' + newline, b"") == before[TABLES[0]]
    assert list(json.loads(after[TABLES[0]])) == sorted((*old_agents, "Paprika"))
    old_roles = json.loads(before[TABLES[1]])
    roles = json.loads(after[TABLES[1]])
    assert roles.pop("Duelist") == sorted(old_roles.pop("Duelist") + ["Hush"])
    assert roles == old_roles
    assert [line for line in after[TABLES[1]].splitlines() if b'"Duelist"' not in line] == [
        line for line in before[TABLES[1]].splitlines() if b'"Duelist"' not in line
    ]
    abilities = json.loads(after[TABLES[2]])
    assert abilities.pop("Hush") == {
        a["displayName"]: f'/static/img/abilities/hush/{a["slot"].lower()}.png'
        for a in agents[1]["abilities"] if a["displayIcon"]
    }
    assert abilities == json.loads(before[TABLES[2]])
    added_abilities = json.dumps({"Hush": json.loads(after[TABLES[2]])["Hush"]}, indent=2)[2:-2]
    added_abilities = (added_abilities + ",\n").replace("\n", newline.decode()).encode()
    assert after[TABLES[2]].replace(added_abilities, b"") == before[TABLES[2]]
    text = after[TABLES[3]].decode()
    added = ('    "Hush": {"C": ("Quiet Field", "status"), "Q": ("Redirect Flash", "blind"), "E": ("Redirect Pull", "dash")},'
             + ("\r\n" if b"\r\n" in before[TABLES[3]] else "\n"))
    assert text.replace(added, "").encode() == before[TABLES[3]]
    assert text.index('    "Harbor":') < text.index(added) < text.index('    "Iso":')
    module = ast.parse(text)
    keys = ast.literal_eval(next(n.value for n in module.body if isinstance(n, ast.Assign)
                                and any(isinstance(t, ast.Name) and t.id == "AGENT_KEYS" for t in n.targets)))
    assert keys["Hush"] == {"C": ("Quiet Field", "status"), "Q": ("Redirect Flash", "blind"),
                            "E": ("Redirect Pull", "dash")}
    for ability in agents[1]["abilities"][:4]:
        assert after[f'app/static/img/abilities/hush/{ability["slot"].lower()}.png'] == png
    with Image.open(root / "app/static/img/agents/hush.png") as image:
        assert image.size == (256, 256) and image.mode == "RGBA"
    assert len(calls) == 5
    for path in before.keys() - set(TABLES):
        assert after[path] == before[path]
    output = capsys.readouterr().out
    for item in ("ULT_COST", "abilities_seed.json", "objects", "agents.json", "constants.py"):
        assert item in output
    assert newagent.main(args) == 0
    assert snapshot(root) == after


@pytest.mark.parametrize("problem", ["unknown", "missing slot", "duplicate slot", "code conflict", "shape"])
def test_identity_refuses_before_writing(identity_case, problem, capsys):
    root, args, agents, calls, _ = identity_case
    if problem == "unknown":
        args[1] = "Unknown"
    elif problem == "missing slot":
        args[-1] = "C=status,Q=blind"
    elif problem == "duplicate slot":
        args[-1] = "C=status,Q=blind,E=dash,C=smoke"
    elif problem == "code conflict":
        path = root / TABLES[0]
        mapping = json.loads(path.read_text())
        mapping["OtherCode"] = "Hush"
        path.write_text(json.dumps(mapping), encoding="utf-8")
    else:
        (root / TABLES[3]).write_text("AGENT_KEYS = dict()\n", encoding="utf-8")
    before = snapshot(root)
    with pytest.raises(SystemExit) as error:
        newagent.main(args)
    assert error.value.code != 0
    assert snapshot(root) == before
    assert not calls
    assert capsys.readouterr().err


def test_vendor_output_matches_original_loop(tmp_path, monkeypatch):
    agents = [fake_agent("Waylay", "Terra"), fake_agent("Hush", "Paprika")]
    # Include both kinds skipped by the original loop.
    agents[0]["abilities"].append({"slot": "Ability3", "displayName": "Missing", "displayIcon": None})
    expected_index = {}
    expected_files = {}
    for agent in sorted(agents, key=lambda a: a["displayName"]):
        slug = vendor.agent_slug(agent["displayName"])
        entries = {}
        for ability in agent.get("abilities") or []:
            if not ability.get("displayIcon") or ability.get("slot") == "Passive":
                continue
            slot = ability["slot"].lower()
            relative = f"img/abilities/{slug}/{slot}.png"
            expected_files[relative] = ability["displayIcon"].encode()
            entries[ability["displayName"]] = f"/static/{relative}"
        expected_index[agent["displayName"]] = dict(sorted(entries.items()))
    expected_text = json.dumps(expected_index, indent=2, ensure_ascii=False) + "\n"
    # Match write_text's platform newline translation, as the original script did.
    expected_files["data/abilities.json"] = expected_text.replace("\n", os.linesep).encode()
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(vendor, "STATIC", tmp_path)
    monkeypatch.setattr(vendor, "fetch_json", lambda url: agents)
    monkeypatch.setattr(vendor, "fetch", lambda url: url.encode())
    assert vendor.main() == 0
    assert snapshot(tmp_path) == expected_files


def test_objects_counts_paths_types_and_references(tmp_path, capsys):
    root = tmp_path / "root"
    script = root / "app/static/js/replay.js"
    script.parent.mkdir(parents=True)
    script.write_text("// Paprika_Q_RedirectFlash\n", encoding="utf-8")
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    rows = [
        {"type": "projectile", "name": "Projectile_Paprika_Q_RedirectFlash_C"},
        {"type": "flash", "nested": ["Projectile_Paprika_Q_RedirectFlash_C_12"]},
        {"type": "ability", "archetype": "/Game/.../Ability_Paprika_E_RedirectPull.Ability_Paprika_E_RedirectPull_C"},
        {"type": "projectile", "name": "Projectile_Terra_Q_Dash_C"},
    ]
    (first / "events.ndjson").write_text("\n".join(map(json.dumps, rows)) + "\n", encoding="utf-8")
    (second / "events.ndjson").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    before = snapshot(tmp_path)
    args = ["objects", "Paprika", str(first), str(second), "--root", str(root)]
    assert newagent.main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and all("Terra" not in line for line in lines)
    flash = next(line for line in lines if "Projectile_Paprika" in line)
    pull = next(line for line in lines if "Ability_Paprika" in line)
    assert "total=3" in flash and "flash=1" in flash and "projectile=2" in flash
    assert "app/static/js/replay.js" in flash and "UNREFERENCED" not in flash
    assert "total=1" in pull and "ability=1" in pull and "UNREFERENCED" in pull
    assert newagent.main(args + ["--fail-unreferenced"]) == 1
    assert snapshot(tmp_path) == before


def test_objects_finds_all_prefixes_and_full_name_references(tmp_path, capsys):
    names = ["Paprika_Direct", "FXC_Paprika_Q_Blinded", "BlindConfig_Paprika_Q_Flash",
             "ForceModule_Paprika_4_PullDash"]
    export = tmp_path / "export"
    export.mkdir()
    (export / "events.ndjson").write_text(json.dumps({"type": "buff", "values": names}), encoding="utf-8")
    path = tmp_path / "app/moments/constants.py"
    path.parent.mkdir(parents=True)
    path.write_text(repr(names), encoding="utf-8")
    assert newagent.main(["--root", str(tmp_path), "objects", "Paprika", str(export),
                          "--fail-unreferenced"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(names)
    for name in names:
        line = next(line for line in lines if line.startswith(name + " "))
        assert "total=1" in line and "buff=1" in line and "app/moments/constants.py" in line


def test_objects_merges_class_defaults_and_rpc_names(tmp_path, capsys):
    rows = [
        {"type": "class", "name": "Default__Ability_Paprika_4_PullBlast"},
        {"type": "ability", "name": "Ability_Paprika_4_PullBlast"},
        {"type": "rpc", "name": "Projectile_Paprika_E_RedirectPull_C:HideRedirectGuides"},
        {"type": "projectile", "name": "Projectile_Paprika_E_RedirectPull_C"},
        {"type": "rpc", "name": "Projectile_Paprika_E_RedirectPull_C_12:HideRedirectGuides"},
        {"type": "class", "name": "/Game/Abilities/Ability_Paprika_4_PullBlast.Default__Ability_Paprika_4_PullBlast_C"},
    ]
    (tmp_path / "events.ndjson").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    assert newagent.main(["--root", str(tmp_path), "objects", "Paprika", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Ability_Paprika_4_PullBlast total=3 ability=1, class=2 UNREFERENCED",
        "Projectile_Paprika_E_RedirectPull total=3 projectile=1, rpc=2 UNREFERENCED",
    ]


@pytest.mark.parametrize("name, source, referenced", [
    ("BlindConfig_Paprika_Q_Flash", "Phoenix_Q_Flash", False),
    ("BlindConfig_Paprika_Q_Flash", '"Q_Flash": {}', True),
    ("BlindConfig_Paprika_Q_Flash", "Q_FlashExtra", False),
    ("BlindConfig_Paprika_Q_Flash", "_Q_Flash", False),
    ("BlindConfig_Paprika_Q_Flash", "BlindConfig_Paprika_Q_Flash", True),
    ("Projectile_Paprika_Q_RedirectFlash", "Paprika_Q_RedirectFlash", True),
    ("Projectile_Paprika_Q_RedirectFlash", "Other_Paprika_Q_RedirectFlash_C", True),
    ("Paprika_PC", "RPC PCache ABCPC ABC_PC PC_1", False),
    ("Paprika_PC", '"PC": {}', True),
    ("Ability_Paprika_E_RedirectPullArea", '"E_RedirectPullArea": {}', True),
    ("Ability_Paprika_E_RedirectPullArea", "Other_E_RedirectPullArea", False),
])
def test_objects_reference_token_boundaries(tmp_path, capsys, name, source, referenced):
    (tmp_path / "events.ndjson").write_text(json.dumps({"type": "object", "name": name}), encoding="utf-8")
    path = tmp_path / "app/static/js/replay.js"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    result = newagent.main(["--root", str(tmp_path), "objects", "Paprika", str(tmp_path),
                            "--fail-unreferenced"])
    output = capsys.readouterr().out
    assert result == (0 if referenced else 1)
    assert ("app/static/js/replay.js" in output) == referenced
    assert ("UNREFERENCED" in output) != referenced
