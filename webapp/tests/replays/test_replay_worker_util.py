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
