"""The worker's Critical Util input store (replay_worker/server.py `UtilStore`, routes /util/*): its own folder, cap
and prune. Loopback HTTP only; nothing here touches the engine."""

import base64
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from fake_render import FakeRender  # noqa: E402
from replay_worker import server  # noqa: E402
from app.critutil import build_digest, corpus  # noqa: E402

WEBAPP = HERE.parents[1]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def settings_for(tmp_path, **extra):
    return server.Settings(temp_root=tmp_path, workflow_slug="replay-tasks", render_api_key="k", **extra)


@pytest.fixture
def render():
    fake = FakeRender()
    try:
        yield fake
    finally:
        fake.close()


class Live:
    def __init__(self, settings, render, with_runs=True):
        settings.render_api_base = render.url
        self.settings = settings
        self.table = server.make_runs(settings) if with_runs else None
        self.util = server.UtilStore(settings)
        self.httpd = server.make_server(server.Worker(settings), runs_table=self.table, util=self.util)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def call(self, path, body=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        request = urllib.request.Request(self.base + path, data=data, method="GET" if data is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def live(tmp_path, render):
    made = []

    def make(**extra):
        item = Live(settings_for(tmp_path, **extra), render)
        made.append(item)
        return item

    yield make
    for item in made:
        item.close()


def test_put_then_have_misses_nothing(live):
    box = live()
    assert box.call("/util/rounds", {"ref": "r1", "rows": [{"key": "a", "data": b64(b"1")}]})[0] == 200
    assert box.call("/util/obs", {"ref": "r1", "obs": [{"key": "o", "data": b64(b"2")}]})[0] == 200
    status, answer = box.call("/util/have", {"ref": "r1", "rows": ["a", "b"], "obs": ["o", "p"]})
    assert status == 200
    assert answer == {"missing_rows": ["b"], "missing_obs": ["p"]}
    assert box.call("/util/have", {"rows": ["a"], "obs": ["o"]})[1] == {"missing_rows": [], "missing_obs": []}


def test_same_key_other_bytes_overwrites(live):
    box = live()
    box.call("/util/rounds", {"rows": [{"key": "a", "data": b64(b"old")}]})
    box.call("/util/rounds", {"rows": [{"key": "a", "data": b64(b"new!")}]})
    assert (box.util.root / "rounds" / "a.json").read_bytes() == b"new!"
    assert box.util.usage() == 4
    assert not list((box.util.root / "rounds").glob("*.tmp*"))


def test_prune_keeps_only_named_files_and_leaves_obs_alone_without_keep_obs(tmp_path):
    store = server.UtilStore(settings_for(tmp_path))
    for key in ("a", "b", "c"):
        store.put_round(key, b"x")
    for key in ("o1", "o2"):
        store.put_obs(key, b"y")
    store.prune(["a"])
    assert sorted(p.stem for p in (store.root / "rounds").iterdir()) == ["a"]
    assert sorted(p.stem for p in (store.root / "obs").iterdir()) == ["o1", "o2"]
    store.prune(["a"], ["o2"])
    assert sorted(p.stem for p in (store.root / "obs").iterdir()) == ["o2"]


def test_have_with_prune_frees_room_for_a_corpus_that_fits_alone(live):
    box = live(util_reserve=10)
    old = [{"key": f"old{i}", "data": b64(b"12345")} for i in range(2)]          # 10 bytes: full
    assert box.call("/util/rounds", {"rows": old})[0] == 200
    assert box.call("/util/rounds", {"rows": [{"key": "new0", "data": b64(b"1")}]})[0] == 507
    status, answer = box.call("/util/have", {"rows": ["new0", "new1"], "prune": True})
    assert status == 200 and answer["missing_rows"] == ["new0", "new1"]
    assert box.call("/util/rounds", {"rows": [{"key": "new0", "data": b64(b"123456")},
                                              {"key": "new1", "data": b64(b"1234")}]})[0] == 200


def test_have_with_prune_also_prunes_obs_only_when_obs_is_given(live):
    box = live()
    box.call("/util/obs", {"obs": [{"key": "o1", "data": b64(b"1")}, {"key": "o2", "data": b64(b"1")}]})
    box.call("/util/have", {"rows": [], "prune": True})
    assert sorted(p.stem for p in (box.util.root / "obs").iterdir()) == ["o1", "o2"]
    box.call("/util/have", {"rows": [], "obs": ["o2"], "prune": True})
    assert sorted(p.stem for p in (box.util.root / "obs").iterdir()) == ["o2"]


def test_files_of_a_running_build_survive_the_prune(tmp_path):
    store = server.UtilStore(settings_for(tmp_path))
    store.put_round("keep", b"1")
    store.put_round("gone", b"1")
    store.put_obs("obs-keep", b"1")
    store.put_obs("obs-gone", b"1")
    store.hold("build1", ["keep"], ["obs-keep"])
    assert store.have([], [], prune=True) == {"missing_rows": [], "missing_obs": []}
    store.prune([], [])
    assert sorted(p.stem for p in (store.root / "rounds").iterdir()) == ["keep"]
    assert sorted(p.stem for p in (store.root / "obs").iterdir()) == ["obs-keep"]
    store.release("build1")
    store.prune([], [])
    assert not list((store.root / "rounds").iterdir()) and not list((store.root / "obs").iterdir())


def test_every_request_logs_its_ref_and_counts(live, capsys):
    box = live()
    box.call("/util/rounds", {"ref": "abc123", "rows": [{"key": "a", "data": b64(b"1")}]})
    box.call("/util/obs", {"ref": "def456", "obs": [{"key": "o", "data": b64(b"1")}]})
    box.call("/util/have", {"ref": "ghi789", "rows": ["a", "z"], "obs": ["o"]})
    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("util ")]
    assert len(lines) == 3
    for ref, line in zip(("abc123", "def456", "ghi789"), lines):
        assert f"ref={ref}" in line
    assert "rows=2" in lines[2] and "missing_rows=1" in lines[2]


@pytest.mark.parametrize("key", ["a:b", "a/b", "../a", "a\\b", "a b", "", "x" * 121])
def test_a_bad_key_is_a_400_and_writes_nothing(live, key):
    box = live()
    assert box.call("/util/rounds", {"rows": [{"key": key, "data": b64(b"1")}]})[0] == 400
    assert box.call("/util/obs", {"obs": [{"key": key, "data": b64(b"1")}]})[0] == 400
    assert box.call("/util/have", {"rows": [key]})[0] == 400
    assert box.util.usage() == 0


def test_a_batch_with_one_bad_key_stores_none_of_it(live):
    box = live()
    status, _ = box.call("/util/rounds", {"rows": [{"key": "ok", "data": b64(b"1")},
                                                   {"key": "a:b", "data": b64(b"1")}]})
    assert status == 400 and box.util.usage() == 0


def test_a_put_past_the_reserve_is_a_507(live):
    box = live(util_reserve=10)
    assert box.call("/util/rounds", {"rows": [{"key": "a", "data": b64(b"1234567890")}]})[0] == 200
    status, _ = box.call("/util/obs", {"obs": [{"key": "o", "data": b64(b"1")}]})
    assert status == 507
    assert box.util.usage() == 10
    # replacing a file counts only the change
    assert box.call("/util/rounds", {"rows": [{"key": "a", "data": b64(b"123")}]})[0] == 200


def test_a_body_over_4_mb_is_a_413(live):
    # Only the header is sent: a worker that refuses on it never reads the body, so sending one would reset.
    import socket
    box = live()
    with socket.create_connection(("127.0.0.1", box.httpd.server_address[1]), timeout=10) as sock:
        sock.sendall(f"POST /util/rounds HTTP/1.1\r\nHost: x\r\nContent-Length: {4 * 1024 * 1024 + 1}\r\n\r\n".encode())
        assert b" 413 " in sock.recv(64)
    assert box.util.usage() == 0


def test_not_json_is_a_400(live):
    assert live().call("/util/have", raw=b"nope")[0] == 400


def test_health_names_the_digest_and_usage(live):
    box = live(util_reserve=1234)
    box.call("/util/rounds", {"rows": [{"key": "a", "data": b64(b"123")}]})
    status, health = box.call("/health")
    assert status == 200
    assert health["util"] == {"enabled": True, "digest": build_digest.digest(WEBAPP), "protocol": 1,
                              "bytes": 3, "reserve": 1234}


def test_a_missing_source_leaves_the_digest_none(tmp_path, monkeypatch):
    monkeypatch.setattr(build_digest, "SOURCES", ("app/does/not/exist.py",))
    store = server.UtilStore(settings_for(tmp_path))
    assert store.digest is None


def test_workflows_off_answers_404(tmp_path, render):
    box = Live(settings_for(tmp_path), render, with_runs=False)
    try:
        for path in ("/util/have", "/util/rounds", "/util/obs"):
            assert box.call(path, {"rows": []})[0] == 404
        assert box.call("/health")[1]["util"]["enabled"] is False
    finally:
        box.close()


def test_util_dir_is_under_the_archive_when_there_is_one(tmp_path):
    assert server.Settings.from_env({"REPLAY_ARCHIVE_DIR": str(tmp_path / "arch")}).util_root() == \
        tmp_path / "arch" / "util"
    plain = server.Settings.from_env({"REPLAY_WORKER_TMP": str(tmp_path)})
    assert plain.util_root() == tmp_path / "util"
    assert server.Settings.from_env({"REPLAY_UTIL_DIR": str(tmp_path / "u"),
                                     "REPLAY_ARCHIVE_DIR": str(tmp_path / "arch")}).util_root() == tmp_path / "u"


def test_settings_defaults_and_env():
    settings = server.Settings.from_env({"REPLAY_UTIL_RESERVE": "5"})
    assert settings.util_reserve == 5
    assert server.Settings().util_reserve == 2 * 1024 ** 3
    assert server.Settings().util_result_max == server.Settings().run_result_max_bytes


def test_the_archive_reserve_grows_by_the_util_share(tmp_path):
    settings = server.Settings(temp_root=tmp_path, archive_dir=tmp_path / "arch", util_reserve=100,
                               run_result_max_bytes=7)
    server.reserve_util(settings, runs_table=object())
    assert settings.archive_extra_reserve == 2 * 100 + 4 * 7


def test_no_extra_reserve_without_an_archive_or_workflows_or_outside_it(tmp_path):
    plain = server.Settings(temp_root=tmp_path, util_reserve=100)
    server.reserve_util(plain, runs_table=object())
    assert plain.archive_extra_reserve == 0
    assert plain.util_root() == tmp_path / "util"
    archived = server.Settings(temp_root=tmp_path, archive_dir=tmp_path / "arch", util_reserve=100)
    server.reserve_util(archived, runs_table=None)
    assert archived.archive_extra_reserve == 0
    outside = server.Settings(temp_root=tmp_path, archive_dir=tmp_path / "arch", util_dir=tmp_path / "elsewhere")
    server.reserve_util(outside, runs_table=object())
    assert outside.archive_extra_reserve == 0


def test_leftover_tars_and_results_are_gone_after_a_restart(tmp_path):
    settings = settings_for(tmp_path)
    store = server.UtilStore(settings)
    store.put_round("a", b"1")
    store.put_obs("o", b"1")
    (store.root / "runs").mkdir(exist_ok=True)
    (store.root / "results").mkdir(exist_ok=True)
    (store.root / "runs" / "t.tar").write_bytes(b"tar")
    (store.root / "results" / "r.json").write_bytes(b"{}")
    again = server.UtilStore(settings)
    assert not list((again.root / "runs").iterdir()) and not list((again.root / "results").iterdir())
    assert again.have(["a"], ["o"]) == {"missing_rows": [], "missing_obs": []}


def test_the_key_pattern_is_the_one_the_corpus_uses():
    assert corpus.KEY_RE is build_digest.KEY_RE


# ---------------------------------------------------------------- a util build as a Workflow run (W7)

import hashlib  # noqa: E402
import time  # noqa: E402

from replay_worker import runs as runs_mod  # noqa: E402

CONTEXT = corpus.encode_context({}, [], None, [], [], False, {})


class BuildBox(Live):
    """Live, with a fake Render whose util runs do what `behave(box, args)` says."""

    def __init__(self, settings, render, behave=None):
        super().__init__(settings, render)
        self.builds = self.httpd.util_builds
        self.behave = behave or (lambda box, args: None)
        render.runner = lambda kind, args: self.behave(self, args) if kind == "util" else None

    def send(self, method, path, data=None, token="", headers=None):
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"X-Run-Token": token, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def output(self, args, data, token=None):
        return self.send("POST", f"/runs/{args['job_id']}/output", data, args["token"] if token is None else token)[0]

    def result(self, args, envelope):
        return self.send("POST", f"/runs/{args['job_id']}/result", json.dumps(envelope).encode(), args["token"])[0]

    def fetch_input(self, args, name="inputs"):
        return self.send("GET", f"/runs/{args['job_id']}/input/{name}", token=args["token"])[1]

    def open_build(self, key="k1", rows=(), obs=(), refit=False, digest=None, **extra):
        body = {"key": key, "digest": digest or self.util.digest, "rows": list(rows), "obs": list(obs),
                "refit": refit, "context": b64(CONTEXT), **extra}
        return self.call("/util/build", body)

    def wait_for(self, build_id, states=("done", "failed")):
        end = time.monotonic() + 10
        body = None
        while time.monotonic() < end:
            status, body = self.call(f"/util/build/{build_id}")
            if status == 200 and body["status"] in states:
                return body
            time.sleep(0.02)
        raise AssertionError(f"build never reached {states}: {body}")


@pytest.fixture
def box(tmp_path, render):
    made = []

    def make(behave=None, **extra):
        item = BuildBox(settings_for(tmp_path, **extra), render, behave)
        made.append(item)
        return item

    yield make
    for item in made:
        item.close()


def good(payload=b'{"values":{}}', **over):
    def behave(b, args):
        assert b.output(args, payload) == 200
        envelope = {"status": "ok", "key": args["key"], "values_key": "vk", "digest": args["digest"], "version": 7,
                    "refit": False, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), **over}
        assert b.result(args, envelope) == 200
    return behave


def seed(b, rows=("r1",), obs=("o1",)):
    for key in rows:
        b.util.put_round(key, b'{"a":1}')
    for key in obs:
        b.util.put_obs(key, b'[["m",[]]]')


def test_missing_rows_and_obs_are_a_409_naming_them(box):
    b = box()
    seed(b, rows=("r1",), obs=())
    status, body = b.open_build(rows=["r1", "r2"], obs=["o9"])
    assert status == 409 and body == {"missing_rows": ["r2"], "missing_obs": ["o9"]}
    assert b.builds.builds == {}


def test_a_key_not_matching_the_pattern_is_a_400_and_so_are_bad_members(box):
    b = box()
    assert b.open_build(key="a/b")[0] == 400
    assert b.open_build(rows=["a:b"])[0] == 400
    assert b.call("/util/build", {"key": "k"})[0] == 400
    assert b.call("/util/build", raw=b"nope")[0] == 400
    assert b.open_build(context="@@@")[0] == 400


def test_workflows_off_answers_404_for_every_build_route(tmp_path, render):
    off = Live(settings_for(tmp_path), render, with_runs=False)
    try:
        assert off.call("/util/build", {"key": "k"})[0] == 404
        assert off.call("/util/build/abc")[0] == 404
    finally:
        off.close()


def test_a_build_streams_its_output_then_posts_a_matching_envelope(box):
    seen = {}

    def behave(b, args):
        seen["inputs"] = b.fetch_input(args)
        seen["args"] = args
        good(b"STREAMED-BYTES")(b, args)
    b = box(behave)
    seed(b)
    status, made = b.open_build(rows=["r1"], obs=["o1"], refit=True)
    assert status == 202 and made["key"] == "k1" and made["status"] in ("queued", "running")
    done = b.wait_for(made["id"])
    assert done["status"] == "done"
    assert done["sha256"] == hashlib.sha256(b"STREAMED-BYTES").hexdigest() and done["bytes"] == 14
    assert done["values_key"] == "vk" and done["version"] == 7 and done["digest"] == b.util.digest
    assert "error" not in done
    assert b.send("GET", f"/util/build/{made['id']}/result") == (200, b"STREAMED-BYTES")
    assert (b.util.root / "results" / f"{made['id']}.json").read_bytes() == b"STREAMED-BYTES"
    assert seen["args"]["key"] == "k1" and seen["args"]["digest"] == b.util.digest
    # the tar the worker packed is the one the corpus reads, with no corpus import on the worker's side
    tar = b.util.root / "check.tar"
    tar.write_bytes(seen["inputs"])
    inputs = corpus.unpack_inputs(tar)
    assert inputs.rounds == {"r1": {"a": 1}} and inputs.obs == {"o1": [["m", []]]}
    assert inputs.context["refit"] is False
    assert not list((b.util.root / "runs").iterdir())       # the tar is gone once the build settles


def test_the_result_is_a_404_until_the_build_is_done(box):
    gate = threading.Event()

    def behave(b, args):
        gate.wait(10)
        good()(b, args)
    b = box(behave)
    made = b.open_build()[1]
    assert b.send("GET", f"/util/build/{made['id']}/result")[0] == 404
    gate.set()
    b.wait_for(made["id"])
    assert b.send("GET", f"/util/build/{made['id']}/result")[0] == 200
    assert b.call("/util/build/nope")[0] == 404 and b.send("GET", "/util/build/nope/result")[0] == 404


def test_a_sha_or_size_that_differs_from_the_streamed_file_is_failed_inputs(box):
    for name, lie in (("sha", {"sha256": "0" * 64}), ("size", {"bytes": 3})):
        b = box(good(**lie))
        done = b.wait_for(b.open_build(key=name)[1]["id"])
        assert done["status"] == "failed" and done["error_kind"] == "inputs" and done["error"]
        assert not list((b.util.root / "results").iterdir())


def test_an_ok_envelope_with_no_streamed_file_is_failed_inputs(box):
    def behave(b, args):
        b.result(args, {"status": "ok", "key": args["key"], "values_key": "v", "digest": "d", "version": 1,
                        "refit": False, "bytes": 2, "sha256": hashlib.sha256(b"{}").hexdigest()})
    b = box(behave)
    done = b.wait_for(b.open_build()[1]["id"])
    assert done["status"] == "failed" and done["error_kind"] == "inputs"


def test_a_run_that_dies_without_posting_is_failed_infra(box, monkeypatch):
    monkeypatch.setattr(runs_mod, "GRACE_S", 0)
    b = box()                                  # the fake ends the run ("completed") and posts nothing
    made = b.open_build()[1]
    end = time.monotonic() + 10
    while time.monotonic() < end and b.call(f"/util/build/{made['id']}")[1]["status"] not in ("done", "failed"):
        b.table.reconcile()
        time.sleep(0.05)
    got = b.call(f"/util/build/{made['id']}")[1]
    assert got["status"] == "failed" and got["error_kind"] == "infra"


def test_a_deploy_refusal_is_failed_deploy(box):
    def behave(b, args):
        b.result(args, {"status": "failed", "error_kind": "deploy", "key": args["key"],
                        "error": "the worker and the task run different code"})
    b = box(behave)
    done = b.wait_for(b.open_build()[1]["id"])
    assert done["status"] == "failed" and done["error_kind"] == "deploy" and "different code" in done["error"]


def test_a_run_that_times_out_is_failed_infra_naming_the_limit(tmp_path, render):
    b = BuildBox(settings_for(tmp_path, util_run_timeout_s=0.3), render)
    try:
        done = b.wait_for(b.open_build()[1]["id"])
        assert done["status"] == "failed" and done["error_kind"] == "infra" and "timed out" in done["error"]
    finally:
        b.close()


def test_the_same_key_twice_is_one_build_even_when_done(box, render):
    gate = threading.Event()

    def behave(b, args):
        gate.wait(10)
        good()(b, args)
    b = box(behave)
    first = b.open_build()[1]
    second = b.open_build()[1]
    assert first["id"] == second["id"]
    gate.set()
    b.wait_for(first["id"])
    assert b.open_build()[1]["id"] == first["id"]
    assert len(render.started) == 1


def test_a_failed_key_gets_a_fresh_build(box, render):
    calls = []

    def behave(b, args):
        calls.append(args["job_id"])
        if len(calls) == 1:
            b.result(args, {"status": "failed", "error_kind": "infra", "error": "boom"})
        else:
            good()(b, args)
    b = box(behave)
    first = b.open_build()[1]
    assert b.wait_for(first["id"])["status"] == "failed"
    second = b.open_build()[1]
    assert second["id"] != first["id"]
    assert b.wait_for(second["id"])["status"] == "done"
    assert b.call(f"/util/build/{first['id']}")[0] == 404          # the failed one was replaced
    assert len(render.started) == 2


def test_different_keys_are_different_builds(box):
    b = box(good())
    one = b.open_build(key="plain", refit=False)[1]
    two = b.open_build(key="plain-refit", refit=True)[1]
    assert one["id"] != two["id"]
    b.wait_for(one["id"])
    b.wait_for(two["id"])


def test_one_build_runs_at_a_time(box):
    live_now, most = [0], [0]
    lock = threading.Lock()

    def behave(b, args):
        with lock:
            live_now[0] += 1
            most[0] = max(most[0], live_now[0])
        time.sleep(0.15)
        with lock:
            live_now[0] -= 1
        good()(b, args)
    b = box(behave)
    ids = [b.open_build(key=f"k{i}")[1]["id"] for i in range(3)]
    for build_id in ids:
        assert b.wait_for(build_id)["status"] == "done"
    assert most[0] == 1


def test_output_with_a_wrong_token_is_a_409_and_changes_nothing(box):
    got = {}

    def behave(b, args):
        got["wrong"] = b.output(args, b"x", token="nope")
        got["none"] = b.send("POST", f"/runs/{args['job_id']}/output", b"x")[0]
        got["unknown"] = b.send("POST", "/runs/zzz/output", b"x", token=args["token"])[0]
        good()(b, args)
    b = box(behave)
    done = b.wait_for(b.open_build()[1]["id"])
    assert got == {"wrong": 409, "none": 409, "unknown": 409} and done["status"] == "done"


def test_output_is_for_util_runs_only(box):
    b = box()
    run = b.table.open("ping", "pingx", {}, 60)
    try:
        assert b.send("POST", "/runs/pingx/output", b"x", token=run.token)[0] == 409
    finally:
        b.table.close(run)


def test_output_past_util_result_max_is_refused_and_nothing_is_kept(tmp_path, render):
    got = {}

    def behave(b, args):
        got["big"] = b.output(args, b"x" * 2001)
        got["files"] = sorted(p.name for p in (b.util.root / "results").iterdir())
        got["ok"] = b.output(args, b"x" * 2000)
        b.result(args, {"status": "ok", "key": args["key"], "values_key": "v", "digest": "d", "version": 1,
                        "refit": False, "bytes": 2000, "sha256": hashlib.sha256(b"x" * 2000).hexdigest()})
    b = BuildBox(settings_for(tmp_path, run_result_max_bytes=2000), render, behave)
    try:
        done = b.wait_for(b.open_build()[1]["id"])
        assert got["big"] == 413 and got["files"] == [] and got["ok"] == 200 and done["status"] == "done"
    finally:
        b.close()


def test_a_non_refit_open_leaves_every_obs_file_and_a_refit_open_prunes_the_rest(box):
    gate = threading.Event()
    b = box(lambda box_, args: gate.wait(10))
    seed(b, rows=("r1", "r-old"), obs=("o1", "o-old"))
    assert b.open_build(key="plain", rows=["r1"], obs=["o1"], refit=False)[0] == 202
    assert sorted(p.stem for p in (b.util.root / "rounds").iterdir()) == ["r1"]          # rows pruned
    assert sorted(p.stem for p in (b.util.root / "obs").iterdir()) == ["o-old", "o1"]    # obs untouched
    seed(b, rows=("r2",), obs=("o2",))
    assert b.open_build(key="refit", rows=["r2"], obs=["o2"], refit=True)[0] == 202
    # the first build is still queued/running: its files are held, everything else not named is gone
    assert sorted(p.stem for p in (b.util.root / "rounds").iterdir()) == ["r1", "r2"]
    assert sorted(p.stem for p in (b.util.root / "obs").iterdir()) == ["o1", "o2"]
    gate.set()


def test_a_settled_build_releases_its_files(box):
    b = box(good())
    seed(b)
    b.wait_for(b.open_build(rows=["r1"], obs=["o1"])[1]["id"])
    b.util.prune([], [])
    assert not list((b.util.root / "rounds").iterdir()) and not list((b.util.root / "obs").iterdir())


def test_only_the_newest_four_builds_are_kept(box):
    b = box(good())
    ids = []
    for i in range(6):
        ids.append(b.open_build(key=f"k{i}")[1]["id"])
        b.wait_for(ids[-1])
    time.sleep(0.1)
    assert [b.call(f"/util/build/{i}")[0] for i in ids] == [404, 404, 200, 200, 200, 200]
    assert sorted(p.stem for p in (b.util.root / "results").iterdir()) == sorted(ids[2:])


def test_every_state_change_logs_the_key_and_build_id(box, capsys):
    b = box(good())
    made = b.open_build(key="logged", ref="abc123")[1]
    b.wait_for(made["id"])
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("util build ")]
    mine = [ln for ln in lines if "key=logged" in ln]
    assert [ln.split()[2] for ln in mine] == ["queued", "running", "done"]
    assert all(f"id={made['id']}" in ln and "ref=abc123" in ln for ln in mine)


def test_the_run_limit_setting_defaults_to_1500_and_reads_its_env():
    assert server.Settings().util_run_timeout_s == 1500
    assert server.Settings.from_env({"REPLAY_UTIL_RUN_TIMEOUT_S": "9"}).util_run_timeout_s == 9
