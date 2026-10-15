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


import json
import threading
import time


def make_runs(render, tmp_path, clock=time.time):
    return runs.Runs(runs.RenderApi("k", base=render.url), "replay-tasks", runs.Pacer(1000), tmp_path / "runs",
                     clock=clock)


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_first_valid_result_wins(render, tmp_path):
    table = make_runs(render, tmp_path)
    run = table.open("ping", "j1", {"ping": write(tmp_path / "p.txt", "x")}, timeout_s=30)
    table.start(run, {"hello": 1})
    assert render.started[0]["task"] == "replay-tasks/ping"
    assert render.started[0]["input"][0] == {"hello": 1, "job_id": "j1", "token": run.token}
    assert table.deliver("j1", "wrong", write(tmp_path / "a.json", {"status": "ok", "n": 0})) == 409
    assert table.deliver("j1", run.token, write(tmp_path / "b.json", {"status": "ok", "n": 1})) == 200
    assert table.deliver("j1", run.token, write(tmp_path / "c.json", {"status": "ok", "n": 2})) == 409
    assert table.wait(run) == {"status": "ok", "n": 1}
    assert table.deliver("j1", run.token, write(tmp_path / "d.json", {"status": "ok"})) == 409  # closed


def test_a_result_that_is_not_a_json_object_is_400(render, tmp_path):
    table = make_runs(render, tmp_path)
    run = table.open("ping", "j1", {}, timeout_s=30)
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2", encoding="utf-8")
    assert table.deliver("j1", run.token, bad) == 400
    assert table.deliver("j1", run.token, write(tmp_path / "list.json", [1])) == 400
    assert not run.done.is_set()


def test_files_only_by_name_and_token(render, tmp_path):
    table = make_runs(render, tmp_path)
    f = write(tmp_path / "p.txt", "x")
    run = table.open("ping", "j1", {"ping": f}, timeout_s=30)
    assert table.file("j1", "ping", run.token) == f
    assert table.file("j1", "ping", "nope") is None
    assert table.file("j1", "../p.txt", run.token) is None
    assert table.file("j2", "ping", run.token) is None


def test_one_open_run_per_job(render, tmp_path):
    table = make_runs(render, tmp_path)
    table.open("ping", "j1", {}, timeout_s=30)
    with pytest.raises(ValueError):
        table.open("ping", "j1", {}, timeout_s=30)


class SharedClock:
    """One fake clock for the table and its pacer: sleeping advances it."""
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_429_waits_then_starts(render, tmp_path):
    clock = SharedClock()
    table = make_runs(render, tmp_path, clock=clock)
    table.pacer = runs.Pacer(1000, clock=clock, sleep=clock.sleep)
    render.rate_limit_next = 2
    run = table.open("ping", "j1", {}, timeout_s=60)
    table.start(run, {})
    assert run.render_id == "trn-1" and not run.done.is_set()
    assert clock.now >= 1000.0 + 2 * runs.RATE_LIMIT_WAIT_S        # two holds, on the fake clock


def test_no_start_after_the_deadline(render, tmp_path):
    clock = SharedClock()
    table = make_runs(render, tmp_path, clock=clock)
    table.pacer = runs.Pacer(1000, clock=clock, sleep=clock.sleep)
    render.rate_limit_next = 100                                  # Render keeps saying 429
    run = table.open("ping", "j1", {}, timeout_s=25)
    table.start(run, {})
    assert run.render_id is None and run.result["error_kind"] == "infra"
    assert clock.now < 1000.0 + 25 + runs.RATE_LIMIT_WAIT_S + 1     # it stopped at the deadline


def test_render_down_settles_infra(tmp_path):
    table = runs.Runs(runs.RenderApi("k", base=f"http://127.0.0.1:{closed_port()}/v1", timeout_s=1), "w",
                      runs.Pacer(1000), tmp_path / "runs")
    run = table.open("ping", "j1", {}, timeout_s=30)
    table.start(run, {})
    assert table.wait(run)["error_kind"] == "infra"


def test_on_started_gets_the_render_id_before_start_returns(render, tmp_path):
    table = make_runs(render, tmp_path)
    seen = []
    run = table.open("ping", "j1", {}, timeout_s=30)
    table.start(run, {}, on_started=seen.append)
    assert seen == ["trn-1"]


def test_deadline_cancels_and_settles_timeout(render, tmp_path):
    table = make_runs(render, tmp_path)
    run = table.open("ping", "j1", {}, timeout_s=0.3)
    table.start(run, {})
    answer = table.wait(run)
    assert answer["error_kind"] == "timeout" and render.cancelled == ["trn-1"]


def test_reconcile_settles_a_run_that_ended_without_a_result_after_grace(render, tmp_path):
    render.finish = False                         # statuses change only when this test says so
    now = [1000.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    run = table.open("ping", "j1", {}, timeout_s=3600)
    table.start(run, {})
    render.status["trn-1"] = "failed"
    table.reconcile()
    assert not run.done.is_set()                  # within grace: its result may still be on the way
    now[0] += runs.GRACE_S
    table.reconcile()
    assert run.result["error_kind"] == "infra" and "failed" in run.result["error"]


def test_reconcile_leaves_running_runs_and_a_result_in_grace_wins(render, tmp_path):
    render.finish = False
    now = [1000.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    run = table.open("ping", "j1", {}, timeout_s=3600)
    table.start(run, {})
    table.reconcile()
    assert render.status["trn-1"] == "running" and not run.done.is_set()
    render.status["trn-1"] = "completed"
    table.reconcile()
    assert table.deliver("j1", run.token, write(tmp_path / "r.json", {"status": "ok"})) == 200
    now[0] += runs.GRACE_S
    table.reconcile()
    assert run.result == {"status": "ok"}


def test_reconcile_finds_an_old_run_by_paging_in_one_list(render, tmp_path, monkeypatch):
    render.finish = False
    monkeypatch.setattr(runs, "RECONCILE_PAGE_SIZE", 10)
    now = [1000.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    old = table.open("heights", "old", {}, timeout_s=3600)
    table.start(old, {})
    api = runs.RenderApi("k", base=render.url)
    for _ in range(25):                     # 25 newer builds (not this table's) push it to the third page
        api.start("replay-tasks/heights", {})
    render.status[old.render_id] = "failed"
    table.reconcile()
    assert len(render.list_calls) == 3 and render.get_calls == []
    assert all("taskSlug=replay-tasks%2Fheights" in call for call in render.list_calls)


def test_status_counts_open_runs(render, tmp_path):
    table = make_runs(render, tmp_path)
    table.open("ping", "j1", {}, timeout_s=30)
    assert table.status()["open"] == 1


import urllib.error
import urllib.request

from replay_worker import server  # noqa: E402


def test_settings_from_env_defaults_local():
    s = server.Settings.from_env({})
    assert (s.parse_runner, s.control_runner, s.heights_runner) == ("local", "local", "local")
    assert s.task_starts_per_min == 1000 and not s.workflow_wanted()


def test_settings_refuse_an_unknown_runner():
    with pytest.raises(ValueError):
        server.Settings.from_env({"REPLAY_CONTROL_RUNNER": "cloud"})


def test_workflow_runner_without_key_or_slug_stops_startup():
    with pytest.raises(SystemExit):
        server.make_runs(server.Settings.from_env({"REPLAY_CONTROL_RUNNER": "workflow"}))


def test_no_key_means_no_run_table():
    assert server.make_runs(server.Settings.from_env({})) is None


@pytest.fixture
def live(render, tmp_path):
    """A worker server with a run table pointed at the fake Render."""
    settings = server.Settings(temp_root=tmp_path, workflow_slug="replay-tasks", render_api_key="k",
                               render_api_base=render.url)
    table = server.make_runs(settings)
    worker = server.Worker(settings)
    httpd = server.make_server(worker, runs_table=table)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", table
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(url, method="GET", data=None, token=None, headers=None):
    headers = {**({"X-Run-Token": token} if token else {}), **(headers or {})}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_input_route_streams_the_named_file_only(live, tmp_path):
    base, table = live
    big = tmp_path / "big.bin"
    big.write_bytes(b"a" * (3 * runs.CHUNK + 5))
    run = table.open("ping", "j1", {"vrf": big}, timeout_s=30)
    status, body = call(f"{base}/runs/j1/input/vrf", token=run.token)
    assert status == 200 and len(body) == 3 * runs.CHUNK + 5
    assert call(f"{base}/runs/j1/input/vrf", token="bad")[0] == 404
    assert call(f"{base}/runs/j1/input/..%2Fjob.json", token=run.token)[0] == 404


def test_result_route_takes_the_first_valid_post(live):
    base, table = live
    run = table.open("ping", "j1", {}, timeout_s=30)
    assert call(f"{base}/runs/j1/result", "POST", b'{"status": "ok"}', token="bad")[0] == 409
    assert call(f"{base}/runs/j1/result", "POST", b'{"status": "ok"}', token=run.token)[0] == 200
    assert call(f"{base}/runs/j1/result", "POST", b'{"status": "no"}', token=run.token)[0] == 409
    assert run.result == {"status": "ok"}


def test_result_route_refuses_bad_lengths_before_writing(live):
    import http.client
    base, table = live
    run = table.open("ping", "j2", {}, timeout_s=30)
    host, port = base.replace("http://", "").split(":")
    for length in ("0", "-5", "abc", str(server.Settings().run_result_max_bytes + 1)):
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        try:
            conn.putrequest("POST", "/runs/j2/result")
            conn.putheader("X-Run-Token", run.token)
            conn.putheader("Content-Length", length)
            conn.endheaders()
            assert conn.getresponse().status in (400, 413), length
        finally:
            conn.close()
    assert not any(run.folder.glob("result-*")) and not run.done.is_set()


def test_health_names_the_runners_and_the_workflow(live):
    base, _ = live
    body = json.loads(call(f"{base}/health")[1])
    assert body["runners"] == {"parse": "local", "control": "local", "heights": "local"}
    assert body["workflow"]["open"] == 0
