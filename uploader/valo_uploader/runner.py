"""Process polling, game-based folder scans, and independent queue work."""

import logging

from .constants import (CLOSE_SCAN_DELAY_S, GAME_SCAN_S, PROCESS_POLL_S,
                        STABLE_GAP_S, WORK_POLL_S)


def run(engine, stop, after_tick=None, on_poll=None):
    def tick(now, scan):
        try:
            engine.tick(now, scan=scan)
        except Exception:
            logging.getLogger("valo_uploader.runner").exception("Engine tick failed")

    def update(now):
        if after_tick is not None:
            try:
                after_tick(now)
            except Exception:
                logging.getLogger("valo_uploader.runner").exception("After-tick callback failed")

    def poll(now):
        if on_poll is not None:
            try:
                on_poll(now)
            except Exception:
                logging.getLogger("valo_uploader.runner").exception("Process-poll callback failed")

    if stop.is_set():
        return
    running = engine.procs()
    now = engine.clock()
    next_scan = now + GAME_SCAN_S if running else None
    second_look = None
    scan_owed = False
    last_tick = None
    # The update gate also gets a chance at startup, independently of replay work.
    poll(now)
    update(now)
    while not stop.is_set():
        status = engine.status()
        scan_owed = scan_owed or (next_scan is not None and now >= next_scan)
        if not status.get("paused", False):
            scheduled = scan_owed
            scan = scheduled or (second_look is not None and now >= second_look)
            work = status["queue_length"] > 0 or status.get("deletion_pending", False)
            if scan or (work and (last_tick is None or now - last_tick >= WORK_POLL_S)):
                tick(now, scan=scan)
                last_tick = now
                if scheduled:
                    # Keep the opening-time cadence, coalescing all missed scans into one.
                    if next_scan is not None and now >= next_scan:
                        next_scan = (next_scan + ((now - next_scan) // GAME_SCAN_S + 1) * GAME_SCAN_S
                                     if running else None)
                    scan_owed = False
                    second_look = now + STABLE_GAP_S if engine.watcher.awaiting else None
                elif scan:
                    # Even a growing recording gets only one follow-up look.
                    second_look = None
        if stop.is_set():
            break
        engine.sleep(PROCESS_POLL_S)
        if stop.is_set():
            break
        was_running = running
        running = engine.procs()
        now = engine.clock()
        if running != was_running:
            next_scan = now + (GAME_SCAN_S if running else CLOSE_SCAN_DELAY_S)
            second_look = None
        poll(now)
        if not running:
            update(now)
