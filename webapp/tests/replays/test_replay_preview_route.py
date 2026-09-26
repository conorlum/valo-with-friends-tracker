"""The local replay preview's dev routes: off by default, off in demo mode, local files only.

The routes are called directly, not through TestClient (see tests/test_site_modes.py)."""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader

from app.config import Settings, settings
from app.routers import replay_preview

WEBAPP = Path(__file__).resolve().parents[2]

UUID = "a5549826-7163-4662-8bf0-dfdde439846e"


@pytest.fixture
def preview_dir(tmp_path, monkeypatch):
    (tmp_path / UUID).mkdir()
    bundle = {"built_at": "2026-09-26T12:00:00", "rounds": {"1": {}, "2": {}},
              "match": {"map": "Ascent", "match_id": 1, "team1_rounds_won": 13, "team2_rounds_won": 11}}
    (tmp_path / UUID / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_preview_dir", str(tmp_path))
    return tmp_path


def _status(call) -> int:
    with pytest.raises(HTTPException) as raised:
        call()
    return raised.value.status_code


def test_the_setting_is_off_by_default():
    assert Settings.model_fields["replay_preview_dir"].default is None


def test_unset_means_every_route_is_404(monkeypatch):
    monkeypatch.setattr(settings, "replay_preview_dir", None)
    assert _status(lambda: replay_preview.replay_bundle(UUID)) == 404
    assert _status(lambda: replay_preview.replay_page(None, UUID)) == 404
    assert _status(lambda: replay_preview.replay_list(None)) == 404


def test_demo_mode_is_404_even_with_the_folder_set(preview_dir, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert _status(lambda: replay_preview.replay_bundle(UUID)) == 404
    assert _status(lambda: replay_preview.replay_list(None)) == 404


def test_the_bundle_is_served_from_the_folder(preview_dir):
    response = replay_preview.replay_bundle(UUID)
    assert response.path == preview_dir / UUID / "bundle.json" and response.media_type == "application/json"


@pytest.mark.parametrize("bad", ["..", "../x", "not-a-uuid", UUID.upper(), UUID + "0"])
def test_only_uuid_folders_are_reachable(preview_dir, bad):
    assert _status(lambda: replay_preview.replay_bundle(bad)) == 404


def test_a_missing_bundle_is_404(preview_dir):
    assert _status(lambda: replay_preview.replay_bundle("00000000-0000-4000-8000-000000000000")) == 404


def test_the_summary_reads_the_bundle(preview_dir):
    summary = replay_preview._summary(preview_dir / UUID / "bundle.json")
    assert summary == {"uuid": UUID, "map": "Ascent", "match_id": 1, "score": [13, 11], "rounds": 2,
                       "built_at": "2026-09-26T12:00:00"}


def test_the_linked_partial_has_the_feed_board_and_layers():
    env = Environment(loader=FileSystemLoader(str(WEBAPP / "app" / "templates")))
    env.get_template("replays/preview.html")
    env.get_template("replays/preview_list.html")
    html = env.get_template("replays/_player.html").render(
        replay={"map_name": "Ascent", "round_numbers": [1]}, linked=True)
    for hook in ("data-replay-feed", "data-replay-board", "data-replay-banner", "data-replay-tip",
                 'data-replay-layer="tracers"', 'data-replay-layer="abilities"'):
        assert hook in html
    unlinked = env.get_template("replays/_player.html").render(replay={"map_name": "Ascent", "round_numbers": [1]},
                                                              linked=False)
    assert "data-replay-feed" not in unlinked and "Not linked to a match on this site" in unlinked


def test_replay_url_only_for_matches_with_a_bundle(preview_dir, monkeypatch):
    from app.services.replay_preview import replay_preview_enabled, replay_url

    assert replay_preview_enabled() is True
    assert replay_url(UUID) == f"/dev/replays/{UUID}"
    assert replay_url(UUID.upper()) == f"/dev/replays/{UUID}", "external_id case doesn't matter"
    assert replay_url("00000000-0000-4000-8000-000000000000") is None
    assert replay_url("Haven101725908") is None and replay_url(None) is None
    monkeypatch.setattr(settings, "demo_mode", True)
    assert replay_preview_enabled() is False and replay_url(UUID) is None


class _Match:
    def __init__(self, external_id):
        self.external_id, self.map_name, self.played_at = external_id, "Ascent", None
        self.team1_rounds_won, self.team2_rounds_won = 13, 11


@pytest.mark.parametrize("enabled", [True, False])
def test_the_matches_list_has_a_watch_column_only_when_the_preview_is_on(preview_dir, monkeypatch, enabled):
    from app.templates import templates

    if not enabled:
        monkeypatch.setattr(settings, "replay_preview_dir", None)
    html = templates.env.get_template("matches/list.html").render(
        matches=[_Match(UUID), _Match("00000000-0000-4000-8000-000000000000")], current_player=None,
        showing_own_matches=False, static_version=lambda: "1")
    assert ("<th>Watch</th>" in html) is enabled
    assert (f'href="/dev/replays/{UUID}"' in html) is enabled
    assert html.count("watch-link") == (1 if enabled else 0)
