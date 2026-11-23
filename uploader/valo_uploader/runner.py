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
                logging.getLogger("valo_uploader.runner").exception("Wake callback failed")

    if stop.is_set():
        return
    running = engine.procs()
    now = engine.clock()
    previous_wake = now
    next_process = now + PROCESS_POLL_S
    next_scan = now + GAME_SCAN_S if running else None
    second_look = None
    scan_owed = False
    last_tick = None
    # Refresh at every wake; updates only get closed-game process samples.
    poll(now)
    if not running:
        update(now)
    while not stop.is_set():
        now = engine.clock()
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
                    second_look = engine.clock() + STABLE_GAP_S if engine.watcher.awaiting else None
                elif scan:
                    # Even a growing recording gets only one follow-up look.
                    second_look = None
        if stop.is_set():
            break
        # Work and second looks may wake sooner without sampling the process again.
        now = engine.clock()
        status = engine.status()
        deadlines = [next_process]
        if not status.get("paused", False):
            if next_scan is not None:
                deadlines.append(next_scan)
            if second_look is not None:
                deadlines.append(second_look)
            if status["queue_length"] > 0 or status.get("deletion_pending", False):
                deadlines.append(now if last_tick is None else last_tick + WORK_POLL_S)
        engine.sleep(min(PROCESS_POLL_S, max(0, min(deadlines) - now)))
        if stop.is_set():
            break
        now = engine.clock()
        if now < previous_wake:
            difference = now - previous_wake
            next_process += difference
            if next_scan is not None:
                next_scan += difference
            if second_look is not None:
                second_look += difference
            if last_tick is not None:
                last_tick += difference
            try:
                engine.clock_rewound(difference)
            except Exception:  # a failed state write must not end the app
                logging.getLogger("valo_uploader.runner").exception("Could not shift saved retry times")
        previous_wake = now
        process_due = now >= next_process
        if process_due:
            was_running = running
            running = engine.procs()
            now = engine.clock()
            # Missed checks coalesce; a slow tick must not cause a burst of tasklist calls.
            next_process = now + PROCESS_POLL_S
            if running != was_running:
                next_scan = now + (GAME_SCAN_S if running else CLOSE_SCAN_DELAY_S)
                second_look = None
        poll(now)
        if process_due and not running:
            update(now)
