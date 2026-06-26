"""Tests for the BackgroundRegistry (background subagent management)."""

from __future__ import annotations

import threading
import time

from loom.agent.background_registry import (
    BackgroundRegistry,
    BackgroundSubagent,
    get_registry,
)

# ---------------------------------------------------------------------------
# Basic register / complete / get
# ---------------------------------------------------------------------------


def test_register_returns_running_entry() -> None:
    reg = BackgroundRegistry()
    entry = reg.register("sa-1", "investigate code")
    assert entry.subagent_id == "sa-1"
    assert entry.description == "investigate code"
    assert entry.status == "running"
    assert entry.result is None
    assert entry.finished_at is None


def test_complete_sets_done_state() -> None:
    reg = BackgroundRegistry()
    reg.register("sa-2", "fix bug")
    reg.complete("sa-2", "[done: 3 turns]\nfixed", turns=3, tool_calls=5)
    entry = reg.get("sa-2")
    assert entry is not None
    assert entry.status == "done"
    assert entry.result == "[done: 3 turns]\nfixed"
    assert entry.turns == 3
    assert entry.tool_calls == 5
    assert entry.finished_at is not None
    assert entry.error is None


def test_complete_with_error_sets_error_state() -> None:
    reg = BackgroundRegistry()
    reg.register("sa-3", "risky task")
    reg.complete("sa-3", "boom", error="ValueError: bad input")
    entry = reg.get("sa-3")
    assert entry is not None
    assert entry.status == "error"
    assert entry.error == "ValueError: bad input"


def test_complete_unknown_id_is_noop() -> None:
    reg = BackgroundRegistry()
    reg.complete("nonexistent", "result")
    assert reg.get("nonexistent") is None


def test_get_nonexistent_returns_none() -> None:
    reg = BackgroundRegistry()
    assert reg.get("nope") is None


# ---------------------------------------------------------------------------
# list_running / list_all
# ---------------------------------------------------------------------------


def test_list_running_filters_by_status() -> None:
    reg = BackgroundRegistry()
    reg.register("r1", "running 1")
    reg.register("r2", "running 2")
    reg.register("d1", "done 1")
    reg.complete("d1", "result")

    running = reg.list_running()
    running_ids = {e.subagent_id for e in running}
    assert running_ids == {"r1", "r2"}


def test_list_all_returns_everything() -> None:
    reg = BackgroundRegistry()
    reg.register("a", "task a")
    reg.register("b", "task b")
    reg.complete("b", "done")
    all_entries = reg.list_all()
    assert len(all_entries) == 2


# ---------------------------------------------------------------------------
# elapsed property
# ---------------------------------------------------------------------------


def test_elapsed_running_increases() -> None:
    reg = BackgroundRegistry()
    entry = reg.register("sa-t", "task")
    e1 = entry.elapsed
    time.sleep(0.02)
    e2 = entry.elapsed
    assert e2 > e1


def test_elapsed_done_is_fixed() -> None:
    reg = BackgroundRegistry()
    reg.register("sa-t2", "task")
    time.sleep(0.02)
    reg.complete("sa-t2", "done")
    entry = reg.get("sa-t2")
    assert entry is not None
    e1 = entry.elapsed
    time.sleep(0.02)
    e2 = entry.elapsed
    assert abs(e1 - e2) < 0.001  # frozen


# ---------------------------------------------------------------------------
# cleanup_stale
# ---------------------------------------------------------------------------


def test_cleanup_removes_old_done_entries() -> None:
    reg = BackgroundRegistry()
    reg.register("old", "old task")
    reg.complete("old", "done")
    # Manually backdate finished_at
    entry = reg.get("old")
    assert entry is not None
    entry.finished_at = time.monotonic() - 700  # > 600s stale threshold

    reg.register("fresh", "fresh task")
    reg.complete("fresh", "done")

    reg.register("running", "still going")

    pruned = reg.cleanup_stale(max_age_seconds=600)
    assert pruned == 1
    assert reg.get("old") is None
    assert reg.get("fresh") is not None
    assert reg.get("running") is not None


def test_cleanup_does_not_remove_running() -> None:
    reg = BackgroundRegistry()
    reg.register("forever", "long task")
    pruned = reg.cleanup_stale()
    assert pruned == 0
    assert reg.get("forever") is not None


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_register_and_complete() -> None:
    reg = BackgroundRegistry()
    n = 50

    def worker(i: int) -> None:
        sid = f"sa-{i}"
        reg.register(sid, f"task {i}")
        reg.complete(sid, f"result {i}", turns=i, tool_calls=i * 2)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    all_entries = reg.list_all()
    assert len(all_entries) == n
    for e in all_entries:
        assert e.status == "done"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_registry_returns_same_instance() -> None:
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_removes_all_entries() -> None:
    reg = BackgroundRegistry()
    reg.register("a", "a")
    reg.register("b", "b")
    reg.clear()
    assert reg.list_all() == []


# ---------------------------------------------------------------------------
# BackgroundSubagent dataclass
# ---------------------------------------------------------------------------


def test_background_subagent_defaults() -> None:
    sa = BackgroundSubagent(subagent_id="x", description="d")
    assert sa.status == "running"
    assert sa.result is None
    assert sa.turns == 0
    assert sa.tool_calls == 0
    assert sa.error is None
    assert sa.finished_at is None
