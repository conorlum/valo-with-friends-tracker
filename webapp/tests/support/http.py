"""A cookie-aware ASGI client: real routing/forms/middleware without a runtime dependency."""
import asyncio
import json as jsonlib
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import unquote, urlencode


@dataclass
class Response:
    status_code: int
    headers: dict
    content: bytes

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return jsonlib.loads(self.content)


class Client:
    def __init__(self, app, scheme="http"):
        self.app, self.scheme, self.cookies = app, scheme, {}

    def request(self, method, target, *, data=None, json=None, headers=None):
        assert data is None or json is None, 'choose form data or JSON'
        path, _, query = target.partition("?")
        body = jsonlib.dumps(json).encode() if json is not None else urlencode(data).encode() if data is not None else b""
        request_headers = {"host": "testserver", **(headers or {})}
        if data is not None:
            request_headers.update({"content-type": "application/x-www-form-urlencoded", "content-length": str(len(body))})
        elif json is not None:
            request_headers.update({"content-type": "application/json", "content-length": str(len(body))})
        if self.cookies:
            request_headers.setdefault("cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
                 "method": method, "scheme": self.scheme, "path": unquote(path), "raw_path": path.encode(),
                 "query_string": query.encode(), "root_path": "", "client": ("127.0.0.1", 1),
                 "server": ("testserver", 80), "headers": [(k.lower().encode(), v.encode()) for k,v in request_headers.items()]}
        messages = []

        async def run():
            complete, received = asyncio.Event(), False
            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": body, "more_body": False}
                await complete.wait()
                return {"type": "http.disconnect"}
            async def send(message):
                messages.append(message)
                if message["type"] == "http.response.body" and not message.get("more_body", False): complete.set()
            await self.app(scope, receive, send)
        asyncio.run(run())
        start = next(m for m in messages if m["type"] == "http.response.start")
        for key, val in start["headers"]:
            if key == b"set-cookie":
                parsed = SimpleCookie(); parsed.load(val.decode())
                for name, cookie in parsed.items():
                    if cookie["max-age"] == "0" or cookie.value == "null": self.cookies.pop(name, None)
                    else: self.cookies[name] = cookie.value
        return Response(start["status"], {k.decode(): v.decode() for k,v in start["headers"]},
                        b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))

    def get(self, path, **kw): return self.request("GET", path, **kw)
    def post(self, path, **kw): return self.request("POST", path, **kw)
