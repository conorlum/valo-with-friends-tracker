"""Span-based request instrumentation for Step 0 of docs/player_page_render_speed.txt.

Records start/end timestamps -- SPANS, not summed durations -- for the
request, each executor task, connection acquisition (pool wait + physical
connect), each SQL statement, cache decode / live compute, and template
render. `summarize()` then derives the doc's four accounting numbers (S1.1):

    S  SQL statements issued           len(sql spans)
    C  longest serial chain            see below
    K  physical connections opened     count of engine "connect" events
    W  total request wall time         trace.wall_end - trace.wall_start

C is NOT derived by subtracting summed SQL time from wall time -- with
concurrent workers the sum can exceed wall clock and go negative (doc S0).
Instead, every span that matters to the critical path is wrapped in a `span()`
tagged with a `phase` name matching the app's own concurrency structure (e.g.
"pre_executor", "executor:profile", "executor:econ", "render"). Phases
sharing an "executor:" prefix ran concurrently (the router's
ThreadPoolExecutor fan-out), so only the slowest of them counts toward C;
every other phase is serial and its duration adds in full. This mirrors
exactly the by-hand accounting in the doc's section 1.2 (Q1 -> Q2 ->
max(A,B,C) -> Q3 -> Q4), just computed from measured spans instead of static
source reading.

Zero-cost / inert when no trace is active: the SQLAlchemy event hooks below
check the contextvar first and no-op if unset, so this module is safe to
import unconditionally (it is not gated behind a debug flag) and safe to call
from scripts/tests that never start a trace.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import statistics
import threading
import time
from concurrent.futures import Executor, Future
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


@dataclass
class Span:
    name: str
    start: float
    end: float
    meta: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class RequestTrace:
    request_id: str
    tags: dict = field(default_factory=dict)
    spans: list[Span] = field(default_factory=list)
    connects: int = 0
    wall_start: float = field(default_factory=time.perf_counter)
    wall_end: float | None = None
    # Wall-clock (epoch) start, distinct from the monotonic perf_counter pair
    # above -- perf_counter is only meaningful as a duration within one
    # process run, so this is what lets 1.5's "is the career request
    # overlapping or subsequent" question be read straight off two different
    # requests' log lines instead of re-deriving it from relative offsets.
    epoch_start: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, name: str, start: float, end: float, **meta) -> None:
        with self._lock:
            self.spans.append(Span(name, start, end, meta))

    def record_connect(self) -> None:
        with self._lock:
            self.connects += 1

    def finish(self) -> None:
        self.wall_end = time.perf_counter()


_current_trace: ContextVar[RequestTrace | None] = ContextVar("_current_trace", default=None)
# Thread-local, NOT the contextvar above: the "checkout fires after
# acquisition" mechanic (doc S0) means the checkout listener needs to know
# when *this thread* started asking for a connection, which is naturally
# thread-scoped -- a contextvar copied into a submitted task's Context would
# work too, but thread-local is simpler and every task already runs on its
# own dedicated thread for the lifetime of one span.
_task_start = threading.local()


def get_current_trace() -> RequestTrace | None:
    return _current_trace.get()


def start_trace(request_id: str, **tags) -> RequestTrace:
    trace = RequestTrace(request_id=request_id, tags=tags)
    _current_trace.set(trace)
    return trace


def mark_task_start(phase: str) -> None:
    """Arms the pool-wait detector for the *current thread*: the next engine
    `checkout` event on this thread will be timed from now. Called
    automatically by `span.__enter__` when the span is given a `phase`."""
    _task_start.t = time.perf_counter()
    _task_start.phase = phase


class span:
    """`with span("cache_lookup", phase="pre_executor"): ...` records a Span
    on the currently-active trace. A no-op (only the `with` overhead) if no
    trace is active -- e.g. in scripts/tests that never call start_trace.

    `phase` is optional and drives C's critical-path accounting (see module
    docstring); spans recorded without one (in particular the per-statement
    "sql" spans from the engine hooks below) still count toward S/L/union but
    are not part of the phase-bucketed C sum -- their duration is already
    included inside whichever phase-tagged span wraps them.
    """

    def __init__(self, name: str, phase: str | None = None, **meta):
        self.name = name
        self.phase = phase
        self.meta = dict(meta)
        if phase:
            self.meta["phase"] = phase
        self._start: float | None = None

    def __enter__(self) -> "span":
        self._start = time.perf_counter()
        if self.phase:
            mark_task_start(self.phase)
        return self

    def __exit__(self, *exc) -> bool:
        trace = get_current_trace()
        if trace is not None and self._start is not None:
            trace.record(self.name, self._start, time.perf_counter(), **self.meta)
        return False


def submit_traced(executor: Executor, fn, *args, **kwargs) -> Future:
    """`executor.submit(fn, *args, **kwargs)`, but propagating the current
    trace (and any other active contextvars) into the submitted task.

    ThreadPoolExecutor.submit does not carry contextvars across the thread
    boundary on its own, and a single Context object cannot be entered by
    two threads at once (it raises) -- so every call here makes its OWN
    `copy_context()`, not a context shared across multiple submitted tasks.
    """
    ctx = copy_context()
    bound = functools.partial(fn, *args, **kwargs)
    return executor.submit(ctx.run, bound)


@event.listens_for(Engine, "connect")
def _on_connect(dbapi_connection, connection_record) -> None:
    trace = get_current_trace()
    if trace is not None:
        trace.record_connect()


@event.listens_for(Engine, "checkout")
def _on_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
    trace = get_current_trace()
    if trace is None:
        return
    t0 = getattr(_task_start, "t", None)
    if t0 is None:
        return
    # Consume: a session that reuses an already-checked-out connection for a
    # second query triggers no further checkout event, so an unconsumed mark
    # must not silently attach to some later, unrelated checkout on this
    # thread.
    _task_start.t = None
    trace.record("pool_wait", t0, time.perf_counter(), thread_phase=getattr(_task_start, "phase", None))


@event.listens_for(Engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
    context._trace_sql_start = time.perf_counter()


@event.listens_for(Engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
    trace = get_current_trace()
    if trace is None:
        return
    start = getattr(context, "_trace_sql_start", None)
    if start is None:
        return
    trace.record("sql", start, time.perf_counter(), statement=_short_statement(statement))


def _short_statement(statement: str) -> str:
    return " ".join(statement.split())[:80]


def _union_duration(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    intervals = sorted(intervals)
    total = 0.0
    cur_start, cur_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    total += cur_end - cur_start
    return total


def summarize(trace: RequestTrace) -> dict:
    sql_spans = [s for s in trace.spans if s.name == "sql"]
    S = len(sql_spans)
    total_sql = sum(s.duration for s in sql_spans)
    sql_union = _union_duration([(s.start, s.end) for s in sql_spans])
    L = statistics.median(s.duration for s in sql_spans) if sql_spans else None
    K = trace.connects
    W = (trace.wall_end - trace.wall_start) if trace.wall_end is not None else None

    by_phase: dict[str, list[Span]] = {}
    for s in trace.spans:
        phase = s.meta.get("phase")
        if phase:
            by_phase.setdefault(phase, []).append(s)
    phase_duration = {phase: sum(s.duration for s in spans) for phase, spans in by_phase.items()}

    executor_phases = {p: d for p, d in phase_duration.items() if p.startswith("executor:")}
    serial_phases = {p: d for p, d in phase_duration.items() if not p.startswith("executor:")}
    C = sum(serial_phases.values())
    if executor_phases:
        C += max(executor_phases.values())

    total_pool_wait = sum(s.duration for s in trace.spans if s.name == "pool_wait")

    return {
        "S": S,
        "C": round(C, 4),
        "K": K,
        "W": round(W, 4) if W is not None else None,
        "L": round(L, 4) if L is not None else None,
        "total_sql": round(total_sql, 4),
        "sql_union": round(sql_union, 4),
        "total_pool_wait": round(total_pool_wait, 4),
        "phase_duration": {p: round(d, 4) for p, d in phase_duration.items()},
    }


_TRACE_LOG_ENV_VAR = "PLAYER_PAGE_TRACE_LOG"


def log_trace(trace: RequestTrace) -> dict:
    """Logs (INFO) and returns the summary dict. If PLAYER_PAGE_TRACE_LOG
    points at a file path, also appends one JSON line to it -- opt-in, used
    by the Step 0 benchmark to collect exact numbers instead of transcribing
    them from console log lines by hand."""
    if trace.wall_end is None:
        trace.finish()
    summary = summarize(trace)
    record = {
        "request_id": trace.request_id,
        "tags": trace.tags,
        "epoch_start": round(trace.epoch_start, 4),
        **summary,
    }
    logger.info("player_page_trace %s", json.dumps(record))

    log_path = os.environ.get(_TRACE_LOG_ENV_VAR)
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            logger.exception("Failed to append to %s", log_path)

    return summary
