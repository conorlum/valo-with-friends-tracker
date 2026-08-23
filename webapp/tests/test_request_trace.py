import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine

from app.services import request_trace as rt


def _make_engine():
    # A fresh in-memory sqlite engine per test -- request_trace's hooks are
    # registered on the base sqlalchemy.engine.Engine class (process-wide),
    # so any engine works as a stand-in for app.db.engine here.
    return create_engine("sqlite:///:memory:")


def _run_query(engine, sleep_s: float = 0.0):
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    if sleep_s:
        time.sleep(sleep_s)


# ---------------------------------------------------------------------------
# span() is inert with no active trace
# ---------------------------------------------------------------------------

def test_span_is_a_noop_without_an_active_trace():
    assert rt.get_current_trace() is None
    engine = _make_engine()
    with rt.span("some_work", phase="pre_executor"):
        _run_query(engine)
    assert rt.get_current_trace() is None


# ---------------------------------------------------------------------------
# Basic span recording + summarize()
# ---------------------------------------------------------------------------

def test_summarize_counts_statements_and_connections_for_a_serial_trace():
    engine = _make_engine()
    trace = rt.start_trace("t1")
    try:
        with rt.span("q1", phase="pre_executor"):
            _run_query(engine)
        with rt.span("q2", phase="pre_executor"):
            _run_query(engine)
    finally:
        trace.finish()

    summary = rt.summarize(trace)
    assert summary["S"] == 2
    assert summary["K"] >= 1
    assert summary["W"] is not None and summary["W"] > 0
    # Both spans share the "pre_executor" phase -> serial, durations sum.
    assert summary["phase_duration"]["pre_executor"] > 0


def test_summarize_returns_none_latency_and_zero_statements_with_no_sql():
    trace = rt.start_trace("t-empty")
    trace.finish()
    summary = rt.summarize(trace)
    assert summary["S"] == 0
    assert summary["L"] is None
    assert summary["total_sql"] == 0
    assert summary["sql_union"] == 0


# ---------------------------------------------------------------------------
# Phase accounting: "executor:" phases take max(), everything else sums.
# ---------------------------------------------------------------------------

def test_executor_phases_contribute_max_not_sum_to_C():
    engine = _make_engine()
    trace = rt.start_trace("t2")
    try:
        with rt.span("pre", phase="pre_executor"):
            time.sleep(0.03)
            _run_query(engine)

        with ThreadPoolExecutor(max_workers=2) as executor:
            def worker(name: str, sleep_s: float):
                with rt.span(f"executor:{name}", phase=f"executor:{name}"):
                    _run_query(engine, sleep_s)

            fast = rt.submit_traced(executor, worker, "fast", 0.01)
            slow = rt.submit_traced(executor, worker, "slow", 0.08)
            fast.result()
            slow.result()

        with rt.span("post", phase="post_query"):
            time.sleep(0.02)
            _run_query(engine)
    finally:
        trace.finish()

    summary = rt.summarize(trace)
    phases = summary["phase_duration"]
    expected_C = phases["pre_executor"] + max(phases["executor:fast"], phases["executor:slow"]) + phases["post_query"]
    # phase_duration's entries are themselves already rounded to 4dp, so
    # re-summing them and rounding again can land a float ulp away from
    # summary["C"] (computed once from the unrounded raw durations) --
    # compare with a tolerance rather than exact equality.
    assert summary["C"] == pytest.approx(expected_C, abs=1e-3)
    # The concurrent block genuinely overlapped, so the serial reconstruction
    # (C) must be meaningfully less than just summing every phase -- that sum
    # would double-count the overlap between the two workers.
    naive_sum = sum(phases.values())
    assert summary["C"] < naive_sum


# ---------------------------------------------------------------------------
# submit_traced propagates the active trace into the worker thread
# ---------------------------------------------------------------------------

def test_submit_traced_propagates_trace_into_worker_thread():
    engine = _make_engine()
    trace = rt.start_trace("t3")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            def worker():
                # A fresh OS thread has no trace of its own -- if submit_traced
                # didn't propagate it via copy_context(), this span would
                # silently vanish instead of landing on `trace`.
                with rt.span("executor:worker", phase="executor:worker"):
                    _run_query(engine)

            rt.submit_traced(executor, worker).result()
    finally:
        trace.finish()

    sql_spans = [s for s in trace.spans if s.name == "sql"]
    assert len(sql_spans) == 1


def test_plain_executor_submit_without_propagation_loses_the_trace():
    """Negative control: proves submit_traced's context propagation is doing
    real work, not just matching a lucky default."""
    engine = _make_engine()
    trace = rt.start_trace("t4")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            def worker():
                with rt.span("executor:worker", phase="executor:worker"):
                    _run_query(engine)

            executor.submit(worker).result()
    finally:
        trace.finish()

    assert trace.spans == []


# ---------------------------------------------------------------------------
# Pool-wait spans: armed by mark_task_start (via a phased span), consumed by
# the next checkout, and NOT double-armed by a second query on an
# already-checked-out connection.
# ---------------------------------------------------------------------------

def test_pool_wait_is_recorded_once_per_new_checkout():
    engine = _make_engine()
    trace = rt.start_trace("t5")
    try:
        with rt.span("work", phase="pre_executor"):
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
                conn.exec_driver_sql("SELECT 1")  # reuses the same checked-out connection
    finally:
        trace.finish()

    pool_waits = [s for s in trace.spans if s.name == "pool_wait"]
    assert len(pool_waits) == 1
    assert pool_waits[0].meta["thread_phase"] == "pre_executor"
