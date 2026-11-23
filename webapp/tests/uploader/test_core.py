import http.client
import io
import json
import ssl
import logging
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from valo_uploader import __version__, constants
from valo_uploader.api import BadKey, Client, Offline, Refused, Rejected
from valo_uploader.config import Config
from valo_uploader.keystore import clear_key, load_key, looks_like_key, save_key
from valo_uploader.log import KeyRedactionFilter, configure_logging
from valo_uploader.paths import app_dir, demos_dir, install_dir
from valo_uploader.state import State


@pytest.fixture
def env(tmp_path):
    return {"APPDATA": str(tmp_path / "roaming"), "LOCALAPPDATA": str(tmp_path / "local")}


def test_paths_and_constants(env):
    assert app_dir(env) == Path(env["APPDATA"]) / "ValoUploader"
    assert demos_dir(env) == Path(env["LOCALAPPDATA"]) / "VALORANT" / "Saved" / "Demos"
    assert install_dir(env) == Path(env["LOCALAPPDATA"]) / "Programs" / "ValoUploader"
    assert __version__ == "0.1.0"
    assert constants.REFUSED_RETRY_S == 600
    assert constants.REFUSED_WINDOW_S == 5 * 3600
    assert constants.FAILED_RETRY_S == 86400
    assert constants.FAILED_RETRY_CAP_S == 14 * 86400
    assert constants.RETRY_GENERATION == 1


def test_key_round_trip_is_protected_and_clear_is_idempotent(env):
    key = "vwf1_" + "a" * 43
    xor = lambda value: bytes(b ^ 0x55 for b in value)
    assert load_key(env=env, unprotect=xor) is None
    save_key(key, env=env, protect=xor)
    assert key.encode() not in (app_dir(env) / "key.bin").read_bytes()
    assert load_key(env=env, unprotect=xor) == key
    assert looks_like_key(key)
    for bad in (key + "\n", key + "a", key[:-1], "other_" + "a" * 43):
        assert not looks_like_key(bad)
    clear_key(env=env)
    clear_key(env=env)
    assert load_key(env=env, unprotect=xor) is None


def test_state_round_trip_and_due_excludes_stored(env):
    state = State(env=env)
    sha = "a" * 64
    assert state.entry(sha) is None
    state.mark(sha, paths=["one.vrf", "copy.vrf"], size=123, mtime=50.0,
               status="uploading", upload_id="upload-1", first_refused_at=10.0,
               attempts=2, next_try=100.0)
    loaded = State(env=env)
    assert loaded.entry(sha) == {
        "paths": ["one.vrf", "copy.vrf"], "size": 123, "mtime": 50.0,
        "status": "uploading", "upload_id": "upload-1", "stored_at": None,
        "first_refused_at": 10.0, "attempts": 2, "next_try": 100.0, "deleted_at": None,
        "next_delete_check": 0.0, "failures": 0,
    }
    assert loaded.due(99) == []
    assert loaded.due(100) == [sha]
    loaded.mark(sha, status="stored", stored_at=101)
    assert State(env=env).entry(sha)["stored_at"] == 101
    assert loaded.due(200) == []
    assert loaded.entry(sha)["paths"] == ["one.vrf", "copy.vrf"]


def test_state_unreplaced_temp_does_not_change_committed_file(env):
    state = State(env=env)
    state.mark("b" * 64, paths=["demo.vrf"], status="pending")
    (app_dir(env) / "state.json.tmp").write_text('{"partial":', encoding="utf-8")
    assert State(env=env).entry("b" * 64)["status"] == "pending"


def test_refused_is_a_state_status_that_is_never_due(env):
    state = State(env=env)
    state.mark("d" * 64, paths=["gone.vrf"], status="refused", next_try=0.0)
    assert state.due(10 ** 12) == []
    assert State(env=env).entry("d" * 64)["status"] == "refused"


def test_items_is_one_consistent_snapshot_even_when_a_row_is_dropped_meanwhile(env, monkeypatch):
    state = State(env=env)
    monkeypatch.setattr(state, "_save", lambda entries: None)
    for name in "abc":
        state.mark(name * 64, paths=[name])
    real_entry, calls = state.entry, []

    def racing(sha):
        calls.append(sha)
        if len(calls) == 1:  # another thread removes a row while this one reads
            state.drop("b" * 64)
        return real_entry(sha)

    monkeypatch.setattr(state, "entry", racing)
    rows = state.items()
    assert [sha[0] for sha, _ in rows] == ["a", "b", "c"]
    assert all(isinstance(row, dict) for _, row in rows)
    assert state.due(10 ** 9)  # also safe on a replaced dict


def test_rejected_file_is_due_at_its_scheduled_retry(env):
    state = State(env=env)
    state.mark("f" * 64, paths=["retry.vrf"], status="rejected", next_try=constants.FAILED_RETRY_S)
    assert state.due(constants.FAILED_RETRY_S - 1) == []
    assert state.due(constants.FAILED_RETRY_S) == ["f" * 64]


@pytest.mark.parametrize("broken", ['{"unfinished":', '[]', '{"sha": "invalid row"}'])
def test_corrupt_state_is_preserved_and_starts_fresh(env, broken):
    folder = app_dir(env)
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(broken, encoding="utf-8")
    state = State(env=env)
    assert state.due(1000) == []
    backups = list(folder.glob("state.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == broken
    state.mark("c" * 64, status="pending")
    assert State(env=env).entry("c" * 64)["status"] == "pending"


def test_config_defaults_bad_json_and_persisted_update_fields(env):
    config = Config.load(env=env)
    assert config == Config(keep_days=14, paused=False, site=constants.SITE,
                            skip_versions=[], last_update_check=0.0)
    assert config.retry_generation == 0  # a settings file from before the field loads as 0
    path = app_dir(env) / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken", encoding="utf-8")
    assert Config.load(env=env) == Config()
    config.keep_days = 21
    config.paused = True
    config.skip_versions = ["0.2.0", "0.3.0"]
    config.last_update_check = 1234.5
    config.save(env=env)
    (path.parent / "settings.json.tmp").write_text("broken", encoding="utf-8")
    assert Config.load(env=env) == config
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["future_option"] = "ignored"
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert Config.load(env=env) == config
    assert Config().skip_versions == []


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response or {}
        self.error = error
        self.requests = []
        self.bodies = []

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout > 0
        data = request.data
        self.bodies.append(data if isinstance(data, bytes) else b"".join(data or []))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.response).encode())


class BrokenBody:
    def __init__(self, error):
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        raise self.error


@pytest.mark.parametrize("error", [
    http.client.IncompleteRead(b"par", 10),
    http.client.RemoteDisconnected("closed"),
    http.client.BadStatusLine("junk"),
    http.client.HTTPException("protocol"),
    ConnectionResetError("reset"),
    TimeoutError("slow"),
    ssl.SSLError("tls"),
    OSError("socket"),
])
@pytest.mark.parametrize("call", ["check", "upload", "status", "key_info"])
def test_response_transport_failures_become_offline(tmp_path, error, call):
    path = tmp_path / "sample.vrf"
    path.write_bytes(b"bytes")

    class Opener:
        def open(self, request, timeout):
            data = request.data
            if data is not None and not isinstance(data, bytes):
                b"".join(data)
            return BrokenBody(error)

    client = Client("https://example.invalid", "key", opener=Opener())
    args = {"check": ("a" * 64,), "upload": (path,), "status": ("upload-1",), "key_info": ()}[call]
    with pytest.raises(Offline):
        getattr(client, call)(*args)


def _http_opener(code, body=b"{}", headers=None):
    class Opener:
        def open(self, request, timeout):
            data = request.data
            if data is not None and not isinstance(data, bytes):
                b"".join(data)
            message = Message()
            for key, value in (headers or {}).items():
                message[key] = value
            raise HTTPError("https://example.invalid", code, "synthetic", message, io.BytesIO(body))

    return Opener()


def _call_all(tmp_path, opener):
    path = tmp_path / "sample.vrf"
    path.write_bytes(b"bytes")
    client = Client("https://example.invalid", "key", opener=opener)
    return [lambda: client.check("a" * 64), lambda: client.upload(path),
            lambda: client.status("upload-1"), lambda: client.key_info()]


@pytest.mark.parametrize("code", [402, 403, 405, 409, 410, 413, 415, 422, 451])
def test_unmapped_4xx_is_rejected_with_site_message(tmp_path, code):
    for call in _call_all(tmp_path, _http_opener(code, b'{"error": "nope"}')):
        with pytest.raises(Rejected, match="nope"):
            call()
    for call in _call_all(tmp_path, _http_opener(code, b"not json")):
        with pytest.raises(Rejected):
            call()


@pytest.mark.parametrize("code", [408, 425])
def test_timeout_like_4xx_is_refused_with_retry_after(tmp_path, code):
    for call in _call_all(tmp_path, _http_opener(code, headers={"Retry-After": "42"})):
        with pytest.raises(Refused) as caught:
            call()
        assert caught.value.retry_after_s == 42
    for call in _call_all(tmp_path, _http_opener(code)):
        with pytest.raises(Refused) as caught:
            call()
        assert caught.value.retry_after_s == constants.REFUSED_RETRY_S


@pytest.mark.parametrize("code", [301, 302, 304, 307, 308, 100, 600])
def test_non_error_statuses_reaching_the_handler_are_offline(tmp_path, code):
    for call in _call_all(tmp_path, _http_opener(code)):
        with pytest.raises(Offline):
            call()


@pytest.mark.parametrize("code", list(range(300, 312)) + list(range(400, 430)) + [500, 502, 599])
def test_no_http_error_ever_escapes(tmp_path, code):
    for call in _call_all(tmp_path, _http_opener(code)):
        try:
            call()
        except HTTPError:
            pytest.fail("HTTPError escaped the client")
        except Exception:
            pass


def test_upload_streams_exact_file_bytes_with_auth_and_content_length(tmp_path, monkeypatch):
    payload = bytes(range(256)) * 1024 + b"\r\nlast bytes"
    path = tmp_path / "sample.vrf"
    path.write_bytes(payload)
    original_open = Path.open
    reads = []

    class LimitedRead:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size=-1):
            assert 0 < size <= 65536, "upload must read bounded chunks"
            reads.append(size)
            return self.stream.read(size)

    def bounded_open(self, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        return LimitedRead(stream) if self == path else stream

    monkeypatch.setattr(Path, "open", bounded_open)
    opener = FakeOpener({"upload_id": "upload-1", "status_url": "/unused"})
    client = Client("https://example.invalid/", "synthetic-key", opener=opener)
    assert client.upload(path) == "upload-1"
    request = opener.requests[0]
    assert request.full_url == "https://example.invalid/api/replays/upload"
    assert request.get_header("Authorization") == "Bearer synthetic-key"
    assert request.get_header("User-agent") == "ValoUploader/0.1.0"
    assert not isinstance(request.data, bytes)
    boundary = request.get_header("Content-type").split("boundary=", 1)[1].encode()
    body = opener.bodies[0]
    assert int(request.get_header("Content-length")) == len(body)
    header, file_part = body.split(b"\r\n\r\n", 1)
    assert b'name="file"; filename="sample.vrf"' in header
    assert file_part == payload + b"\r\n--" + boundary + b"--\r\n"
    assert len(reads) > 2


@pytest.mark.parametrize("code, retry, expected", [
    (429, "120", 120), (503, None, 600), (503, "nonsense", 600),
    (429, "-10", 600), (503, "0", 0),
])
def test_refusals_parse_retry_after(code, retry, expected):
    headers = Message()
    if retry is not None:
        headers["Retry-After"] = retry
    error = HTTPError("https://example.invalid", code, "refused", headers, io.BytesIO(b"{}"))
    with pytest.raises(Refused) as caught:
        Client("https://example.invalid", "key", opener=FakeOpener(error=error)).key_info()
    assert caught.value.retry_after_s == expected


def test_retry_after_http_date(monkeypatch):
    import valo_uploader.api as api
    monkeypatch.setattr(api.time, "time", lambda: 1000.0)
    headers = Message()
    headers["Retry-After"] = datetime.fromtimestamp(1120, timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    error = HTTPError("https://example.invalid", 503, "refused", headers, io.BytesIO(b"{}"))
    with pytest.raises(Refused) as caught:
        Client("https://example.invalid", "key", opener=FakeOpener(error=error)).key_info()
    assert caught.value.retry_after_s == 120


@pytest.mark.parametrize("error, expected", [
    (HTTPError("https://example.invalid", 400, "bad", {}, io.BytesIO(b'{"error":"bad replay"}')), Rejected),
    (HTTPError("https://example.invalid", 401, "bad key", {}, io.BytesIO(b"{}")), BadKey),
    (URLError("offline"), Offline), (TimeoutError("timeout"), Offline),
])
def test_api_error_mapping(error, expected):
    client = Client("https://example.invalid", "key", opener=FakeOpener(error=error))
    with pytest.raises(expected) as caught:
        client.key_info()
    if expected is Rejected:
        assert str(caught.value) == "bad replay"


def test_check_posts_json_and_status_and_key_routes():
    opener = FakeOpener({"known": True})
    client = Client("https://example.invalid", "key", opener=opener)
    assert client.check("d" * 64) is True
    assert opener.requests[-1].get_method() == "POST"
    assert opener.requests[-1].full_url.endswith("/api/replays/check")
    assert opener.requests[-1].get_header("Content-type") == "application/json"
    assert json.loads(opener.bodies[-1]) == {"sha256": "d" * 64}
    opener.response = {"known": False}
    assert client.check("e" * 64) is False
    opener.response = {"status": "stored", "error": None}
    assert client.status("upload-1") == opener.response
    assert opener.requests[-1].full_url.endswith("/api/replays/uploads/upload-1/status")
    opener.response = {"ok": True, "label": "test device"}
    assert client.key_info() == opener.response
    assert opener.requests[-1].get_method() == "GET"
    assert opener.requests[-1].full_url.endswith("/api/replays/key")


@pytest.mark.parametrize("response, expected", [
    ({"known": True, "kept": True}, True),
    ({"known": True, "kept": False}, False),
    ({"known": True}, False),  # an older site without the field: nothing is deleted
    ({"known": True, "kept": "yes"}, False),
    ({"known": True, "kept": 1}, False),
    ({"known": False, "kept": None}, False),
])
def test_kept_is_true_only_for_an_explicit_true(response, expected):
    opener = FakeOpener(response)
    client = Client("https://example.invalid", "key", opener=opener)
    assert client.kept("f" * 64) is expected
    assert opener.requests[-1].full_url.endswith("/api/replays/check")
    assert opener.requests[-1].get_method() == "POST"
    assert json.loads(opener.bodies[-1]) == {"sha256": "f" * 64}


@pytest.mark.parametrize("error, expected", [
    (HTTPError("https://example.invalid", 401, "bad key", {}, io.BytesIO(b"{}")), BadKey),
    (HTTPError("https://example.invalid", 429, "slow", {}, io.BytesIO(b"{}")), Refused),
    (HTTPError("https://example.invalid", 503, "deploy", {}, io.BytesIO(b"{}")), Refused),
    (HTTPError("https://example.invalid", 500, "boom", {}, io.BytesIO(b"{}")), Offline),
    (HTTPError("https://example.invalid", 400, "bad", {}, io.BytesIO(b'{"error":"x"}')), Rejected),
    (URLError("offline"), Offline), (TimeoutError("timeout"), Offline),
])
def test_kept_maps_client_errors(error, expected):
    client = Client("https://example.invalid", "key", opener=FakeOpener(error=error))
    with pytest.raises(expected):
        client.kept("a" * 64)


def test_check_still_returns_known_whatever_kept_says():
    client = Client("https://example.invalid", "key", opener=FakeOpener({"known": True, "kept": False}))
    assert client.check("a" * 64) is True


def test_old_state_file_without_next_delete_check_loads_and_marks(env):
    from valo_uploader.state import State
    state = State(env=env)
    state.mark("a" * 64, status="stored")
    assert state.entry("a" * 64)["next_delete_check"] == 0.0
    state._entries["b" * 64] = {"paths": [], "status": "stored"}
    state.mark("b" * 64, next_delete_check=5.0)
    assert state.entry("b" * 64)["next_delete_check"] == 5.0


def test_log_redacts_message_arguments_and_tracebacks(env):
    key = "vwf1_" + "x" * 43
    logger = logging.getLogger("valo_uploader")
    original_level, original_propagate = logger.level, logger.propagate
    original_handlers, original_filters = list(logger.handlers), list(logger.filters)
    # Protect existing handlers from configure_logging's close-and-replace setup.
    for handler in original_handlers:
        logger.removeHandler(handler)
    try:
        configure_logging(env=env)
        logger.warning("key=%s", key)
        try:
            raise ValueError(key)
        except ValueError:
            logger.exception("failed with %s", key)
        for handler in logger.handlers:
            handler.flush()
            assert handler.maxBytes == 1024 * 1024
            assert handler.backupCount == 3
        output = (app_dir(env) / "uploader.log").read_text(encoding="utf-8")
        assert key not in output
        assert "vwf1_***" in output
        record = logging.LogRecord("test", logging.INFO, "", 0, "key=" + key, (), None)
        assert KeyRedactionFilter().filter(record)
        assert record.getMessage() == "key=vwf1_***"
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        for handler in original_handlers:
            logger.addHandler(handler)
        for filter_ in list(logger.filters):
            logger.removeFilter(filter_)
        for filter_ in original_filters:
            logger.addFilter(filter_)
        logger.setLevel(original_level)
        logger.propagate = original_propagate


class CutBody:
    """An error response whose body is cut off mid-read."""

    def read(self, *args):
        raise http.client.IncompleteRead(b"{", 50)

    def close(self):
        pass


@pytest.mark.parametrize("route_call", ["check", "status", "key_info", "upload"])
def test_truncated_400_body_is_rejected_not_an_unexpected_error(tmp_path, route_call):
    class Opener:
        def open(self, request, timeout):
            if request.data is not None and not isinstance(request.data, bytes):
                b"".join(request.data)
            raise HTTPError(request.full_url, 400, "bad", {}, CutBody())

    client = Client("https://example.invalid", "synthetic-key", opener=Opener())
    sample = tmp_path / "a.vrf"
    sample.write_bytes(b"replay")
    calls = {"check": lambda: client.check("a" * 64), "status": lambda: client.status("u1"),
             "key_info": client.key_info, "upload": lambda: client.upload(sample)}
    with pytest.raises(Rejected):
        calls[route_call]()
