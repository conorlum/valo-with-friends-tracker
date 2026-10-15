"""scripts/map_pool.py (show / refresh / seed) and the MAP_POOL_AUTO entry in render.yaml."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings
from app.db import Base
from app.models.map_pool import MapPoolChange, MapPoolPatchNotes
from app.models.match import Match
from app.services import map_pool

WEBAPP = Path(__file__).resolve().parents[1]
RENDER_YAML = WEBAPP.parent / "render.yaml"
UTC = timezone.utc


def _load_cli():
    spec = importlib.util.spec_from_file_location("map_pool_cli", WEBAPP / "scripts" / "map_pool.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load_cli()


@pytest.fixture(autouse=True)
def _clear_cache():
    map_pool.clear_pool_cache()
    yield
    map_pool.clear_pool_cache()


@pytest.fixture
def factory(tmp_path):
    eng = create_engine("sqlite:///" + str(tmp_path / "pool.sqlite"))
    Base.metadata.create_all(
        eng, tables=[MapPoolChange.__table__, MapPoolPatchNotes.__table__, Match.__table__]
    )
    yield sessionmaker(bind=eng)
    eng.dispose()


def test_render_yaml_map_pool_auto_only_on_friends_service():
    doc = yaml.safe_load(RENDER_YAML.read_text(encoding="utf-8"))
    found = {}
    for svc in doc["services"]:
        for e in svc.get("envVars", []):
            if e.get("key") == "MAP_POOL_AUTO":
                found[svc["name"]] = e.get("value")
    assert found == {"valowithfriendstracker": "true"}


def test_show_prints_seed_pool(factory, capsys):
    with factory() as db:
        code = cli.cmd_show(db, None)
    out = capsys.readouterr().out
    assert code == 0
    expected = sorted(map_pool.seed_history().pool_at(datetime.now(UTC)))
    assert expected
    for name in expected:
        assert name in out
    assert "latest change" in out


def test_show_at_time(factory, capsys):
    with factory() as db:
        cli.cmd_show(db, "2024-01-01T00:00:00+00:00")
    out = capsys.readouterr().out
    for name in sorted(map_pool.seed_history().pool_at(datetime(2024, 1, 1, tzinfo=UTC))):
        assert name in out


def test_refresh_and_seed_refuse_in_demo_mode(factory, monkeypatch, capsys):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert cli.cmd_refresh(factory, lambda url: "") == 2
    assert cli.cmd_seed(factory, "x") == 2
    assert "demo" in capsys.readouterr().out.lower()
    with factory() as db:
        assert db.scalars(select(MapPoolChange)).first() is None


def test_seed_replaces_seed_rows_only(factory, monkeypatch, capsys):
    monkeypatch.setattr(settings, "demo_mode", False)
    when = datetime(2023, 1, 1, tzinfo=UTC)
    with factory() as db:
        db.add(MapPoolChange(map_name="Bogus", in_pool=True, effective_at=when, patch="0.0", source="seed"))
        db.add(MapPoolChange(map_name="Kept", in_pool=True, effective_at=when, patch="0.1", source="patch_notes"))
        db.commit()
    assert cli.cmd_seed(factory, "testdb") == 0
    assert "testdb" in capsys.readouterr().out
    with factory() as db:
        seeds = db.scalars(select(MapPoolChange).where(MapPoolChange.source == "seed")).all()
        assert "Bogus" not in {r.map_name for r in seeds}
        assert len(seeds) == len(map_pool.seed_history().changes)
        assert [r.map_name for r in db.scalars(select(MapPoolChange).where(MapPoolChange.source == "patch_notes"))] == ["Kept"]


def test_seed_keeps_a_fetched_row_the_seed_now_also_has(factory, monkeypatch):
    """A change fetched by the refresh and later added to the committed seed: reseeding must not trip the
    unique key; the fetched row stays and the seed's other rows go in."""
    monkeypatch.setattr(settings, "demo_mode", False)
    last = map_pool.seed_history().changes[-1]
    with factory() as db:
        db.add(MapPoolChange(map_name=last.map_name, in_pool=last.in_pool, effective_at=last.effective_at,
                             patch=last.patch, source="patch_notes"))
        db.commit()
    assert cli.cmd_seed(factory, "testdb") == 0
    with factory() as db:
        rows = db.scalars(select(MapPoolChange)).all()
        assert len(rows) == len(map_pool.seed_history().changes)
        same = [r for r in rows if r.map_name == last.map_name and r.in_pool == last.in_pool
                and r.effective_at.replace(tzinfo=UTC) == last.effective_at]
        assert [r.source for r in same] == ["patch_notes"]
