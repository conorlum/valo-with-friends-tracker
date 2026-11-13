"""The site's upload counter (app/services/upload_activity.py): an ASGI middleware that counts
`POST /replays/upload` from request start to response end. A Starlette app whose route awaits an event."""

import asyncio

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.services import upload_activity


async def call(app, method, path):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "client": ("127.0.0.1", 1), "server": ("testserver", 80),
             "headers": [(b"host", b"testserver")]}
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


def make(gate):
    async def upload(request):
        await gate.wait()
        return PlainTextResponse("ok")

    async def other(request):
        await gate.wait()
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/replays/upload", upload, methods=["POST", "GET"]),
                            Route("/replays/other", other, methods=["POST"])])
    return upload_activity.UploadActivityMiddleware(app)


def test_counts_one_during_a_slow_upload_and_zero_after():
    upload_activity.reset()

    async def scenario():
        gate = asyncio.Event()
        app = make(gate)
        assert upload_activity.receiving() == 0 and upload_activity.last_end_age_s() is None
        task = asyncio.ensure_future(call(app, "POST", "/replays/upload"))
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert upload_activity.receiving() == 1
        assert upload_activity.last_end_age_s() is None
        gate.set()
        messages = await task
        assert messages[0]["status"] == 200
        assert upload_activity.receiving() == 0
        assert isinstance(upload_activity.last_end_age_s(), float)

    asyncio.run(scenario())


def test_other_paths_and_methods_are_not_counted():
    upload_activity.reset()

    async def scenario():
        gate = asyncio.Event()
        app = make(gate)
        tasks = [asyncio.ensure_future(call(app, "POST", "/replays/other")),
                 asyncio.ensure_future(call(app, "GET", "/replays/upload"))]
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert upload_activity.receiving() == 0
        gate.set()
        await asyncio.gather(*tasks)
        assert upload_activity.last_end_age_s() is None

    asyncio.run(scenario())


def test_a_failing_route_still_ends_the_count():
    upload_activity.reset()

    async def boom(request):
        raise RuntimeError("synthetic")

    app = upload_activity.UploadActivityMiddleware(Starlette(routes=[Route("/replays/upload", boom, methods=["POST"])]))

    async def scenario():
        try:
            await call(app, "POST", "/replays/upload")
        except RuntimeError:
            pass
        assert upload_activity.receiving() == 0
        assert upload_activity.last_end_age_s() is not None

    asyncio.run(scenario())
