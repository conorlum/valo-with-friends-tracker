"""Registers the task bodies with Render Workflows. The image's start command:

    sh /srv/replay_worker/entrypoint.sh /opt/control-venv/bin/python -m replay_tasks.main

Every task runs on `flex` (spec D7) with its own timeout and retries (D8, D9)."""

from render.workflows import Retry, TaskContext, Workflows

from replay_tasks import tasks

app = Workflows(default_plan="flex")
NO_RETRY = Retry(max_retries=0, wait_duration_ms=1000)


@app.task(name="ping", timeout_seconds=60, retry=NO_RETRY)
def ping(ctx: TaskContext, args: dict) -> dict:
    tasks.ping(args, tasks.link_for(args))
    return {"ok": True}


@app.task(name="control", timeout_seconds=1800, retry=Retry(max_retries=1, wait_duration_ms=5000))
def control(ctx: TaskContext, args: dict) -> dict:
    tasks.control(args, tasks.link_for(args))
    return {"ok": True}


# Later steps add their @app.task blocks HERE, above this line: app.start() registers the tasks defined when it
# runs (render/workflows/app.py, start), so a block below it would never be registered.

if __name__ == "__main__":
    app.start()
