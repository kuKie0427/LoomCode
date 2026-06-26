"""Integration tests for background subagent execution.

Tests the full flow: task(background=true) → immediate placeholder →
background thread runs → subagent_poll retrieves result.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from loom.agent.background_registry import get_registry


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the global registry before and after each test."""
    get_registry().clear()
    yield
    get_registry().clear()


# ---------------------------------------------------------------------------
# run_task(background=True) — immediate return
# ---------------------------------------------------------------------------


def test_run_task_background_returns_immediately() -> None:
    """task(background=true) returns a placeholder in < 1 second."""
    from loom.agent.tools import run_task

    t0 = time.monotonic()
    result = run_task("test task", background=True)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"Background task took {elapsed:.1f}s — should be instant"
    assert "Background subagent started" in result
    assert "subagent_poll" in result


def test_run_task_background_registers_in_registry() -> None:
    """Background task registers an entry in the BackgroundRegistry."""
    from loom.agent.tools import run_task

    result = run_task("investigate something", background=True)
    # Extract subagent_id from the placeholder message
    # "Background subagent started (id: bg_xxx)."
    assert "id:" in result
    sid = result.split("id: ")[1].split(")")[0]
    entry = get_registry().get(sid)
    assert entry is not None
    assert entry.status == "running"
    assert entry.description == "investigate something"


def test_run_task_background_with_subagent_id() -> None:
    """When _subagent_id is provided, it's used as the registry key."""
    from loom.agent.tools import run_task

    result = run_task("custom id task", background=True, _subagent_id="toolu_custom123")
    assert "toolu_custom123" in result
    entry = get_registry().get("toolu_custom123")
    assert entry is not None
    assert entry.description == "custom id task"


# ---------------------------------------------------------------------------
# subagent_poll — status checking
# ---------------------------------------------------------------------------


def test_subagent_poll_unknown_id() -> None:
    from loom.agent.tools import run_subagent_poll

    result = run_subagent_poll("nonexistent_id")
    assert "Unknown" in result


def test_subagent_poll_running_status() -> None:
    from loom.agent.tools import run_subagent_poll

    get_registry().register("sa-running", "test running task")
    result = run_subagent_poll("sa-running")
    assert "Running" in result
    assert "test running task" in result


def test_subagent_poll_done_returns_full_result() -> None:
    from loom.agent.tools import run_subagent_poll

    get_registry().register("sa-done", "completed task")
    get_registry().complete(
        "sa-done", "[done: 5 turns, 10 tool calls]\nTask completed successfully",
        turns=5, tool_calls=10,
    )
    result = run_subagent_poll("sa-done")
    assert "[done: 5 turns" in result
    assert "Task completed successfully" in result


def test_subagent_poll_error_returns_error_message() -> None:
    from loom.agent.tools import run_subagent_poll

    get_registry().register("sa-err", "failed task")
    get_registry().complete("sa-err", "boom", error="RuntimeError: something broke")
    result = run_subagent_poll("sa-err")
    assert "Error" in result
    assert "RuntimeError" in result


def test_subagent_poll_wait_blocks_until_done() -> None:
    """subagent_poll(wait=True) blocks until the subagent completes."""
    from loom.agent.tools import run_subagent_poll

    get_registry().register("sa-wait", "waiting task")

    # Complete after a short delay in a background thread
    import threading

    def _complete_after_delay():
        time.sleep(0.3)
        get_registry().complete("sa-wait", "[done: 1 turns, 0 tool calls]\nDone!")

    t = threading.Thread(target=_complete_after_delay)
    t.start()

    result = run_subagent_poll("sa-wait", wait=True, timeout=5.0)
    t.join()

    assert "Done!" in result


def test_subagent_poll_wait_times_out() -> None:
    """subagent_poll(wait=True, timeout=0.5) returns 'Running' if not done."""
    from loom.agent.tools import run_subagent_poll

    get_registry().register("sa-timeout", "slow task")
    result = run_subagent_poll("sa-timeout", wait=True, timeout=0.5)
    assert "Running" in result


# ---------------------------------------------------------------------------
# subagent_list — listing
# ---------------------------------------------------------------------------


def test_subagent_list_empty() -> None:
    from loom.agent.tools import run_subagent_list

    result = run_subagent_list()
    assert "No background subagents" in result


def test_subagent_list_shows_all() -> None:
    from loom.agent.tools import run_subagent_list

    get_registry().register("sa-a", "task A")
    get_registry().register("sa-b", "task B")
    get_registry().complete("sa-b", "done", turns=2, tool_calls=3)

    result = run_subagent_list()
    assert "sa-a" in result
    assert "sa-b" in result
    assert "running" in result
    assert "done" in result


# ---------------------------------------------------------------------------
# _run_background_subagent — background execution
# ---------------------------------------------------------------------------


def test_background_subagent_executes_and_completes() -> None:
    """A background subagent actually runs spawn_subagent and completes."""
    from loom.agent.tools import _run_background_subagent

    # Must register before calling _run_background_subagent, because
    # complete() is a no-op on unknown ids.
    get_registry().register("sa-exec", "test execution")

    # Mock spawn_subagent to return a known result
    with patch("loom.agent.tools.spawn_subagent", return_value="[done: 2 turns, 3 tool calls]\nHello from subagent"):
        _run_background_subagent("sa-exec", "test execution")

    entry = get_registry().get("sa-exec")
    assert entry is not None
    assert entry.status == "done"
    assert entry.turns == 2
    assert entry.tool_calls == 3
    assert "Hello from subagent" in entry.result


def test_background_subagent_handles_exception() -> None:
    """If spawn_subagent raises, the registry entry is marked as error."""
    from loom.agent.tools import _run_background_subagent

    get_registry().register("sa-fail", "doomed task")

    with patch("loom.agent.tools.spawn_subagent", side_effect=RuntimeError("LLM failed")):
        _run_background_subagent("sa-fail", "doomed task")

    entry = get_registry().get("sa-fail")
    assert entry is not None
    assert entry.status == "error"
    assert "LLM failed" in entry.error


# ---------------------------------------------------------------------------
# Tool registration — schema checks
# ---------------------------------------------------------------------------


def test_subagent_poll_registered_in_tool_registry() -> None:
    """subagent_poll is in the live tool registry."""
    from loom.agent.tools import get_tool_handlers, get_tools

    tool_names = {t["name"] for t in get_tools()}
    assert "subagent_poll" in tool_names
    assert "subagent_list" in tool_names
    assert "subagent_poll" in get_tool_handlers()
    assert "subagent_list" in get_tool_handlers()


def test_task_tool_schema_includes_background() -> None:
    """The task tool schema includes the 'background' property."""
    from loom.agent.tools import get_tools

    task_tool = next(t for t in get_tools() if t["name"] == "task")
    props = task_tool["input_schema"]["properties"]
    assert "background" in props
    assert props["background"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# Loop integration — _run_tool_block background path
# ---------------------------------------------------------------------------


def test_run_tool_block_background_skips_on_subagent_end() -> None:
    """_run_tool_block for background task fires on_subagent_start but
    NOT on_subagent_end (the background thread fires it later)."""
    from loom.agent.hooks import Hooks
    from loom.agent.loop import _run_tool_block, clear_active_callbacks, set_active_callbacks

    hooks = Hooks()
    start_calls: list[tuple] = []
    end_calls: list[tuple] = []

    cb = {
        "on_subagent_start": lambda sid, desc: start_calls.append((sid, desc)),
        "on_subagent_end": lambda sid, elapsed, state: end_calls.append((sid, elapsed, state)),
    }
    set_active_callbacks(cb)

    try:
        block = MagicMock()
        block.name = "task"
        block.id = "toolu_test_bg"
        block.input = {"description": "bg task", "background": True}

        # Mock the executor so the background thread doesn't actually run
        # (which would fire on_subagent_end from the thread and race
        # with our assertion).
        mock_executor = MagicMock()

        with patch("loom.agent.tools._get_bg_executor", return_value=mock_executor):
            result = _run_tool_block(block, hooks)

        assert result["tool_use_id"] == "toolu_test_bg"
        assert "Background subagent started" in result["content"]
        assert len(start_calls) == 1
        assert start_calls[0][0] == "toolu_test_bg"
        # on_subagent_end should NOT be called for background tasks
        # (the loop skips it; the background thread fires it later)
        assert len(end_calls) == 0
        # Verify the executor was used (background thread was submitted)
        mock_executor.submit.assert_called_once()
    finally:
        clear_active_callbacks()


def test_run_tool_block_foreground_fires_both_callbacks() -> None:
    """_run_tool_block for foreground task fires both on_subagent_start
    and on_subagent_end (existing behavior, regression guard)."""
    from loom.agent.hooks import Hooks
    from loom.agent.loop import _run_tool_block, clear_active_callbacks, set_active_callbacks

    hooks = Hooks()
    start_calls: list[tuple] = []
    end_calls: list[tuple] = []

    cb = {
        "on_subagent_start": lambda sid, desc: start_calls.append((sid, desc)),
        "on_subagent_end": lambda sid, elapsed, state: end_calls.append((sid, elapsed, state)),
    }
    set_active_callbacks(cb)

    try:
        block = MagicMock()
        block.name = "task"
        block.id = "toolu_test_fg"
        block.input = {"description": "fg task"}  # no background=True

        with patch("loom.agent.tools.spawn_subagent", return_value="[done: 1 turns, 0 tool calls]\nOK"):
            result = _run_tool_block(block, hooks)

        assert "OK" in result["content"]
        assert len(start_calls) == 1
        assert len(end_calls) == 1
        assert end_calls[0][0] == "toolu_test_fg"
        assert end_calls[0][2] == "done"
    finally:
        clear_active_callbacks()


# ---------------------------------------------------------------------------
# get_active_callbacks
# ---------------------------------------------------------------------------


def test_get_active_callbacks_returns_none_when_no_loop() -> None:
    from loom.agent.loop import get_active_callbacks

    assert get_active_callbacks() is None


def test_get_active_callbacks_returns_dict_when_set() -> None:
    from loom.agent.loop import clear_active_callbacks, get_active_callbacks, set_active_callbacks

    test_cb = {"on_message_start": lambda: None}
    set_active_callbacks(test_cb)
    try:
        assert get_active_callbacks() is test_cb
    finally:
        clear_active_callbacks()
