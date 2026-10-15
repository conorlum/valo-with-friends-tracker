"""Workflow runs on the replay worker (replay_worker/runs.py; spec 2026-10-14-render-workflows-design.md):
the REST client, the start pacer, the run table, the run routes and a ping end to end. Localhost only."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from fake_render import FakeRender, closed_port  # noqa: E402
from replay_worker import runs  # noqa: E402


@pytest.fixture
def render():
    fake = FakeRender()
    try:
        yield fake
    finally:
        fake.close()


def test_start_get_cancel_page(render):
    api = runs.RenderApi("k3y", base=render.url)
    run = api.start("replay-tasks/ping", {"job_id": "j1"})
    assert run["id"] == "trn-1"
    assert render.started[0] == {"id": "trn-1", "task": "replay-tasks/ping", "input": [{"job_id": "j1"}]}
    assert render.auth[0] == "Bearer k3y"
    assert api.get("trn-1")["id"] == "trn-1"
    items, cursor = api.page(["replay-tasks/ping"])
    assert [r["id"] for r in items] == ["trn-1"] and cursor is None
    api.cancel("trn-1")
    assert render.cancelled == ["trn-1"]


def test_page_filters_by_every_slug_and_follows_the_cursor(render):
    api = runs.RenderApi("k", base=render.url)
    for kind in ("ping", "control", "ping"):
        api.start(f"w/{kind}", {})
    api.start("other/ping", {})
    first, cursor = api.page(["w/ping", "w/control"], limit=2)
    second, end = api.page(["w/ping", "w/control"], cursor=cursor, limit=2)
    assert [r["id"] for r in first + second] == ["trn-3", "trn-2", "trn-1"] and end is None
    assert "taskSlug=w%2Fping&taskSlug=w%2Fcontrol" in render.list_calls[0]


def test_429_is_rate_limited_and_404_is_render_error(render):
    api = runs.RenderApi("k", base=render.url)
    render.rate_limit_next = 1
    with pytest.raises(runs.RateLimited):
        api.start("w/ping", {})
    with pytest.raises(runs.RenderError):
        api.get("trn-404")


def test_unreachable_is_render_error():
    api = runs.RenderApi("k", base=f"http://127.0.0.1:{closed_port()}/v1", timeout_s=1)
    with pytest.raises(runs.RenderError):
        api.get("trn-1")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_pacer_allows_per_min_then_waits_for_the_window():
    clock = Clock()
    pacer = runs.Pacer(3, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        pacer.take()
    assert clock.now == 0.0
    pacer.take()
    assert clock.now >= 60.0


def test_pacer_hold_pushes_the_next_start():
    clock = Clock()
    pacer = runs.Pacer(40, clock=clock, sleep=clock.sleep)
    pacer.hold(10)
    assert pacer.take() is True
    assert clock.now >= 10.0


def test_pacer_gives_up_when_told_to_stop():
    clock = Clock()
    pacer = runs.Pacer(1, clock=clock, sleep=clock.sleep)
    pacer.take()
    assert pacer.take(stop=lambda: clock.now >= 5.0) is False      # the next slot is 60 s away
    assert clock.now < 60.0
