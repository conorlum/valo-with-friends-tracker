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
        status = engine.status()
        # Fast scans while the game runs, work is queued, or a .vrf still awaits its second look.
        active = running or status["queue_length"] > 0 or status.get("scanning", False)
        interval = ACTIVE_SCAN_S if active else IDLE_SCAN_S
        now = engine.clock()
        # The game starting or closing ticks at once: a closing game releases its last recording.
        if running != was_running or now - last_tick >= interval:
            tick(now)
            last_tick = now
