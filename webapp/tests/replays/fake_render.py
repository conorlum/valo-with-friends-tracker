"""A localhost fake of Render's task-run REST routes (POST/GET/DELETE /v1/task-runs, GET /v1/task-runs).
Each start runs `runner(kind, args)` on a thread, the way a real run would call back into the worker."""

import itertools
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeRender:
    def __init__(self, runner=None):
        self.started: list[dict] = []
        self.status: dict[str, str] = {}
        self.cancelled: list[str] = []
        self.auth: list[str] = []
        self.slug_of: dict[str, str] = {}
        self.list_calls: list[str] = []
        self.get_calls: list[str] = []
        self.rate_limit_next = 0
        self.finish = True              # False: a started run stays "running" until a test sets its status
        self.runner = runner or (lambda kind, args: None)
        ids = itertools.count(1)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                fake.auth.append(self.headers.get("Authorization", ""))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if fake.rate_limit_next:
                    fake.rate_limit_next -= 1
                    return self._send(429, {"message": "slow down"})
                run_id = f"trn-{next(ids)}"
                kind = body["task"].split("/", 1)[1]
                fake.started.append({"id": run_id, "task": body["task"], "input": body["input"]})
                fake.slug_of[run_id] = body["task"]
                fake.status[run_id] = "running"

                def go():
                    try:
                        fake.runner(kind, body["input"][0])
                        if fake.finish and fake.status[run_id] == "running":
                            fake.status[run_id] = "completed"
                    except Exception:  # noqa: BLE001 - a failing task is a failed run
                        if fake.finish and fake.status[run_id] == "running":
                            fake.status[run_id] = "failed"
                threading.Thread(target=go, daemon=True).start()
                return self._send(202, {"id": run_id, "status": "pending"})     # Render answers 202

            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/v1/task-runs":
                    fake.list_calls.append(self.path)
                    query = urllib.parse.parse_qs(parsed.query)
                    slugs = set(query.get("taskSlug", []))
                    limit = int(query.get("limit", ["100"])[0])
                    rows = [(i, s) for i, s in reversed(list(fake.status.items()))
                            if not slugs or fake.slug_of[i] in slugs]
                    start = int(query.get("cursor", ["0"])[0])
                    page = rows[start:start + limit]
                    items = [{"taskRun": {"id": i, "status": s}, "cursor": str(start + n + 1)}
                             for n, (i, s) in enumerate(page)]
                    return self._send(200, items)
                fake.get_calls.append(self.path)
                run_id = parsed.path.rsplit("/", 1)[1]
                if run_id not in fake.status:
                    return self._send(404, {"message": "not found"})
                return self._send(200, {"id": run_id, "status": fake.status[run_id]})

            def do_DELETE(self):  # noqa: N802
                run_id = self.path.rsplit("/", 1)[1]
                fake.cancelled.append(run_id)
                fake.status[run_id] = "canceled"
                return self._send(204)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()     # releases the listening socket, not just the serve loop


def closed_port() -> int:
    """A localhost port nothing listens on: bound, then closed."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
