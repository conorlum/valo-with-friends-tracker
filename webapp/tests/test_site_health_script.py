from datetime import datetime, timedelta, timezone
from importlib import util
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models.site_events import SiteEvent, SiteEventMark, SiteEventResolution
from app.services import site_health

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "site_health.py"
T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=1)


def load_script():
    spec = util.spec_from_file_location("site_health_script", SCRIPT)
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, tables=[SiteEvent.__table__, SiteEventResolution.__table__, SiteEventMark.__table__])
    yield sessionmaker(bind=eng)
    eng.dispose()


def test_list_prints_rows(factory, capsys):
    site_health.record("slow_page", "/x", "took long\nsecond line", at=T0)
    site_health.record("page_error", "/a", "boom\ntrace", at=T0)
    site_health.record("page_error", "/b", "bang", at=T0 + timedelta(hours=1))
    site_health.flush(session_factory=factory)
    script = load_script()
    assert script.main(["list"], session_factory=factory) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("page_error") and "/b" in lines[0] and "bang" in lines[0]
    assert "/a" in lines[1] and "boom" in lines[1] and "trace" not in lines[1]
    assert lines[2].startswith("slow_page")


def test_list_kind_filter(factory, capsys):
    site_health.record("slow_page", "/x", "e", at=T0)
    site_health.record("page_error", "/a", "e", at=T0)
    site_health.flush(session_factory=factory)
    load_script().main(["list", "--kind", "slow_page"], session_factory=factory)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("slow_page")


def test_no_other_command():
    script = load_script()
    for argv in (["clear"], ["resolve", "x"], []):
        with pytest.raises(SystemExit):
            script.main(argv)


def test_refuses_the_demo(monkeypatch, capsys):
    monkeypatch.setattr(settings, "demo_mode", True)

    def never():
        raise AssertionError("a session was opened")
    assert load_script().main(["list"], session_factory=never) == 2
    assert "demo" in capsys.readouterr().err


def test_the_documented_command_names_the_friends_database():
    assert "--expect-database valowithfriendsdb --read-only scripts/site_health.py list" in SCRIPT.read_text()
