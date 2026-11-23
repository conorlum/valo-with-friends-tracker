"""Single-threaded process polling and active/idle folder scan cadence."""

import logging

from .constants import ACTIVE_SCAN_S, IDLE_SCAN_S, PROCESS_POLL_S


def run(engine, stop, after_tick=None):
    def tick(now):
        try:
            engine.tick(now)
        except Exception:
            logging.getLogger("valo_uploader.runner").exception("Engine tick failed")
        if after_tick is not None:
            try:
                after_tick(now)
            except Exception:
                logging.getLogger("valo_uploader.runner").exception("After-tick callback failed")

    if stop.is_set():
        return
    running = engine.procs()
    last_tick = engine.clock()
    tick(last_tick)
    while not stop.is_set():
        engine.sleep(PROCESS_POLL_S)
        if stop.is_set():
            break
        was_running = running
        running = engine.procs()
        active = running or engine.status()["queue_length"] > 0
        interval = ACTIVE_SCAN_S if active else IDLE_SCAN_S
        now = engine.clock()
        if (running and not was_running) or now - last_tick >= interval:
            tick(now)
            last_tick = now
