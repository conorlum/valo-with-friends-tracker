"""Synthetic archive downloads, with both an offline transport and a loopback HTTP site."""

import hashlib
import io
import json
import threading
import urllib.error
import urllib.request
from collections import Counter
from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from scripts import fetch_archived_replay as fetch


UUID_A = "aaaaaaaa-0000-4000-8000-000000000001"
UUID_B = "bbbbbbbb-0000-4000-8000-000000000002"
TOKEN = "synthetic-download-admin-secret"
REFUSED = "/admin/replays/refused"
INDEX = "/admin/replays/archive"
BODY = b"synthetic replay\0" * 80000  # More than one 1 MiB chunk.


def sha(body):
    return hashlib.sha256(body).hexdigest()


def download_path(uuid):
    return f"{INDEX}/{uuid}.vrf"


@pytest.fixture(params=["offline", "http"])
def archive_site(request, monkeypatch, capsys):
    monkeypatch.setenv("REPLAY_ADMIN_TOKEN", TOKEN)
    state = SimpleNamespace(routes={}, requests=Counter(), auth=[], responses=[], reads=[])

    def put_json(path, body, status=200):
        state.routes[path] = (status, {"Content-Type": "application/json"}, json.dumps(body).encode())

    state.put_json = put_json
    put_json(REFUSED, {"replays": []})
    put_json(INDEX, {"files": [{"match_uuid": UUID_A, "sha256": sha(BODY)},
                              {"match_uuid": UUID_B, "sha256": sha(BODY)}], "status": {}})
    for uuid in (UUID_A, UUID_B):
        state.routes[download_path(uuid)] = (200, {"X-Archive-Sha256": sha(BODY),
                                                  "Content-Length": str(len(BODY))}, BODY)

    def route(path, auth):
        state.requests[path] += 1
        state.auth.append(auth)
        return state.routes.get(path, (404, {}, b'{"detail": "Not Found"}'))

    if request.param == "offline":
        class Response(io.BytesIO):
            def read(self, size=-1):
                state.reads.append(size)
                return super().read(size)

        def urlopen(req, timeout):
            assert req.get_method() == "GET"
            status, headers, body = route(urlparse(req.full_url).path,
                                           req.get_header("Authorization"))
            if status >= 400:
                raise urllib.error.HTTPError(req.full_url, status, "synthetic error", headers, io.BytesIO(body))
            response = Response(body)
            response.headers = Message()
            for name, value in headers.items():
                response.headers[name] = value
            state.responses.append(response)
            return response

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        state.url = "https://synthetic.invalid"
        yield state
        assert all(response.closed for response in state.responses)
    else:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, headers, body = route(self.path, self.headers.get("Authorization"))
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        state.url = f"http://127.0.0.1:{server.server_port}"
        try:
            yield state
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    assert state.auth and set(state.auth) == {f"Bearer {TOKEN}"}
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err


def run(site, out, *uuids):
    return fetch.main([*uuids, "--site", site.url, "--out", str(out)])


def test_downloads_and_verifies(archive_site, tmp_path, capsys):
    out = tmp_path / "new" / "directory"
    assert run(archive_site, out, UUID_A.upper()) == 0
    assert (out / f"{UUID_A}.vrf").read_bytes() == BODY
    assert not list(out.glob("*.part"))
    assert archive_site.requests == {REFUSED: 1, INDEX: 1, download_path(UUID_A): 1}
    captured = capsys.readouterr()
    assert "downloaded" in captured.out and UUID_A in captured.out
    assert TOKEN not in captured.out + captured.err
    if archive_site.reads:
        assert max(archive_site.reads) == 1024 * 1024


@pytest.mark.parametrize("problem", ["sha", "short", "missing_sha"])
def test_bad_download_is_removed_and_next_one_runs(archive_site, tmp_path, problem, capsys):
    status, headers, body = archive_site.routes[download_path(UUID_A)]
    headers = dict(headers)
    if problem == "sha":
        headers["X-Archive-Sha256"] = "0" * 64
    elif problem == "short":
        headers["Content-Length"] = str(len(body) + 1)
    else:
        del headers["X-Archive-Sha256"]
    archive_site.routes[download_path(UUID_A)] = status, headers, body
    # An unsuccessful replacement must preserve the previous final file.
    existing = tmp_path / f"{UUID_A}.vrf"
    existing.write_bytes(b"previous recording")
    assert run(archive_site, tmp_path, UUID_A, UUID_B) == 1
    assert existing.read_bytes() == b"previous recording"
    assert not list(tmp_path.glob("*.part"))
    assert (tmp_path / f"{UUID_B}.vrf").read_bytes() == BODY
    captured = capsys.readouterr()
    assert "failed" in captured.out and UUID_A in captured.out
    assert TOKEN not in captured.out + captured.err


def test_no_length_header_is_allowed(archive_site, tmp_path):
    del archive_site.routes[download_path(UUID_A)][1]["Content-Length"]
    assert run(archive_site, tmp_path, UUID_A) == 0
    assert (tmp_path / f"{UUID_A}.vrf").read_bytes() == BODY


def test_existing_good_file_is_skipped(archive_site, tmp_path, capsys):
    (tmp_path / f"{UUID_A}.vrf").write_bytes(BODY)
    assert run(archive_site, tmp_path, UUID_A) == 0
    assert archive_site.requests == {REFUSED: 1, INDEX: 1}
    assert "skipped" in capsys.readouterr().out


@pytest.mark.parametrize("code", ["not_in_archive", "deleted", "bad_uuid", "tombstoned",
                                 "missing_on_disk", "archive_off"])
def test_coded_404_continues(archive_site, tmp_path, capsys, code):
    archive_site.put_json(download_path(UUID_A), {"detail": {"code": code, "reason": "synthetic refusal"}}, 404)
    assert run(archive_site, tmp_path, UUID_A, UUID_B) == 0
    assert not (tmp_path / f"{UUID_A}.vrf").exists()
    assert not list(tmp_path.glob("*.part"))
    assert (tmp_path / f"{UUID_B}.vrf").read_bytes() == BODY
    captured = capsys.readouterr()
    assert "not in archive" in captured.out and code in captured.out and "synthetic refusal" in captured.out
    assert TOKEN not in captured.out + captured.err


def test_refused_uses_rows_and_prints_map_and_reasons(archive_site, tmp_path, capsys):
    archive_site.put_json(REFUSED, {"replays": [
        {"match_uuid": UUID_A, "map": "Haven", "check": "eligibility", "reason": "fallback hidden",
         "reasons": ["synthetic first reason", "synthetic second reason"], "created_at": "2026-11-01T00:00:00Z"},
        {"match_uuid": UUID_B, "map": "Ascent", "check": None, "reason": "synthetic fallback",
         "reasons": [], "created_at": "2026-10-31T00:00:00Z"},
    ]})
    assert run(archive_site, tmp_path, "--refused") == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{UUID_A}.vrf", f"{UUID_B}.vrf"]
    captured = capsys.readouterr()
    assert all(value in captured.out for value in ["Haven", "Ascent", "synthetic first reason",
                                                  "synthetic second reason", "synthetic fallback"])
    assert "fallback hidden" not in captured.out
    assert TOKEN not in captured.out + captured.err


@pytest.mark.parametrize("detail", ["Not Found", "", None])
def test_bare_preflight_404_exits_2(archive_site, tmp_path, capsys, detail):
    archive_site.put_json(REFUSED, {"detail": detail}, 404)
    assert run(archive_site, tmp_path, UUID_A) == 2
    assert archive_site.requests == {REFUSED: 1}
    captured = capsys.readouterr()
    assert "the admin routes are off or the token is wrong" in captured.err
    assert "older build" in captured.err
    assert TOKEN not in captured.out + captured.err


def test_index_failure_exits_before_download(archive_site, tmp_path, capsys):
    archive_site.put_json(INDEX, {"detail": f"synthetic worker down {TOKEN}"}, 502)
    assert run(archive_site, tmp_path, UUID_A) == 2
    assert archive_site.requests == {REFUSED: 1, INDEX: 1}
    captured = capsys.readouterr()
    assert "could not read the archive index:" in captured.err
    assert "502" in captured.err
    assert TOKEN not in captured.out + captured.err


@pytest.mark.parametrize("args", [[], [UUID_A, "--refused"]])
def test_exactly_one_selection_mode(args):
    with pytest.raises(SystemExit) as error:
        fetch.main(args)
    assert error.value.code == 2
