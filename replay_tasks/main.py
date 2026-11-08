"""Registers the task bodies with Render Workflows. The workflow's start command:

    /opt/control-venv/bin/python -m replay_tasks.main

Not through replay_worker/entrypoint.sh: that drops to the `worker` user, and registration then fails (Render's
SDK socket is root's; 2026-10-16). The workflow has no disk to hand over, so it needs no drop.

Every task runs on `flex` (spec D7) with its own timeout and retries (D8, D9), except parse: on `pro`, since a
parse's export (~65x the file) ran out of disk on `flex` (2026-10-16).

warm (a map's visibility cache by itself) runs on `flex`, its limit 300 s above the worker's
`REPLAY_WARM_BUILD_TIMEOUT_S`. Each limit sits 300 s above the worker's Workflow limit (`render.yaml`);
tests/replays/test_control_remote.py pins the order."""

import asyncio

from render.workflows import Retry, TaskContext, Workflows

from replay_tasks import disk_probe, tasks

app = Workflows(default_plan="flex")
NO_RETRY = Retry(max_retries=0, wait_duration_ms=1000)


@app.task(name="ping", timeout_seconds=60, retry=NO_RETRY)
def ping(ctx: TaskContext, args: dict) -> dict:
    tasks.ping(args, tasks.link_for(args))
    return {"ok": True}


@app.task(name="control", timeout_seconds=7500, retry=Retry(max_retries=1, wait_duration_ms=5000))
def control(ctx: TaskContext, args: dict) -> dict:
    tasks.control(args, tasks.link_for(args))
    return {"ok": True}


@app.task(name="parse", plan="pro", timeout_seconds=1260, retry=NO_RETRY)
def parse(ctx: TaskContext, args: dict) -> dict:
    tasks.parse(args, tasks.link_for(args))
    return {"ok": True}


@app.task(name="heights", timeout_seconds=11100, retry=NO_RETRY)
def heights(ctx: TaskContext, args: dict) -> dict:
    tasks.heights(args, tasks.link_for(args))
    return {"ok": True}


@app.task(name="warm", timeout_seconds=21900, retry=Retry(max_retries=1, wait_duration_ms=5000))
def warm(ctx: TaskContext, args: dict) -> dict:
    tasks.warm(args, tasks.link_for(args))
    return {"ok": True}


# A one-off disk probe (replay_tasks/disk_probe.py), started by hand from the dashboard: `disk_probe` with input
# `[]` starts 10 `disk` runs on pro (parse's plan) at once and answers with their summary. The worker never starts
# either.
@app.task(name="disk", plan="pro", timeout_seconds=1800, retry=NO_RETRY)
def disk(ctx: TaskContext, args: dict | None = None) -> dict:
    return disk_probe.run(args)


@app.task(name="disk_probe", timeout_seconds=3600, retry=NO_RETRY)
async def probe(ctx: TaskContext, runs: int = 10, write_gb: float = 15) -> dict:
    answers = await asyncio.gather(*(ctx.run(disk, args) for args in disk_probe.plan(runs, write_gb)),
                                   return_exceptions=True)
    return disk_probe.summarize(answers)


# Later steps add their @app.task blocks HERE, above this line: app.start() registers the tasks defined when it
# runs (render/workflows/app.py, start), so a block below it would never be registered.

if __name__ == "__main__":
    app.start()
