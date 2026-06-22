"""Tests for f-lsp-server-lifecycle (Phase PL-2) — loom.agent.lsp_manager.

All tests are mock-based: no real LSP server is spawned. We patch
``shutil.which`` (so the manager's PATH check passes), ``lsp_client.start``
(so the handshake is a no-op), and ``lsp_client.shutdown`` (so the teardown
is observable). This lets us count spawns and exercise the cache + locking
+ shutdown paths deterministically.
"""

from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from loom.agent import lsp_client as lc
from loom.agent import lsp_manager as lm
from loom.agent.config import HarnessConfig, LSPConfig, LSPServerSpec
from loom.agent.lsp_manager import (
    _ACTIVE_SERVERS,
    _PER_SERVER_LOCKS,
    get_or_start,
    get_server_lock,
    shutdown_all,
)


@pytest.fixture(autouse=True)
def _isolate_manager_state():
    """Wipe the module-level dicts between tests.

    Module-level state means tests can leak servers across cases if
    we don't reset. The fixture runs around every test in this file.
    """
    _ACTIVE_SERVERS.clear()
    _PER_SERVER_LOCKS.clear()
    yield
    _ACTIVE_SERVERS.clear()
    _PER_SERVER_LOCKS.clear()


def _make_cfg(*specs: LSPServerSpec) -> HarnessConfig:
    """Build a HarnessConfig whose LSPConfig carries only `specs`."""
    base = HarnessConfig.from_defaults()
    return HarnessConfig.from_defaults().__class__(
        policy=base.policy,
        checkpoint=base.checkpoint,
        lsp=LSPConfig(servers=tuple(specs)),
    )


def _fake_start(server: lc.LSPServer) -> None:
    """Stand-in for lsp_client.start: pretend handshake succeeded."""
    server.capabilities = {"definitionProvider": True}


@pytest.fixture
def fake_which():
    """Pretend every command exists in PATH so get_or_start's PATH check passes."""
    with patch.object(shutil, "which", return_value="/usr/bin/anything"):
        yield


@pytest.fixture
def fake_start():
    """Bypass the real initialize handshake.

    We patch ``lm.start`` (the binding inside ``lsp_manager``) because
    ``lsp_manager`` does ``from loom.agent.lsp_client import ... start``
    at module load; patching ``lc.start`` afterwards has no effect.
    """
    with patch.object(lm, "start", side_effect=_fake_start):
        yield


def test_no_config_returns_none(tmp_path: Path) -> None:
    """Empty LSPConfig → no server matches any extension → None."""
    cfg = HarnessConfig.from_defaults()
    assert get_or_start(str(tmp_path / "x.py"), cfg) is None


def test_extension_no_match_returns_none(tmp_path: Path) -> None:
    """Python server configured but caller asks for a .ts file → None."""
    spec = LSPServerSpec(name="python", command="pylsp", extensions=(".py",))
    cfg = _make_cfg(spec)
    assert get_or_start(str(tmp_path / "x.ts"), cfg) is None


def test_command_not_in_path_raises_filenotfound(tmp_path: Path) -> None:
    """Configured command doesn't exist → FileNotFoundError before Popen."""
    spec = LSPServerSpec(name="ghost", command="nonexistent-xyz", extensions=(".py",))
    cfg = _make_cfg(spec)
    with pytest.raises(FileNotFoundError, match="nonexistent-xyz"):
        get_or_start(str(tmp_path / "x.py"), cfg)


def test_first_call_starts_server(tmp_path: Path, fake_which: None, fake_start: None) -> None:
    """First call spawns exactly one process and caches the LSPServer."""
    spec = LSPServerSpec(name="python", command="pylsp", extensions=(".py",))
    cfg = _make_cfg(spec)
    server = get_or_start(str(tmp_path / "x.py"), cfg)
    assert isinstance(server, lc.LSPServer)
    assert server.name == "python"
    assert "python" in _ACTIVE_SERVERS
    assert "python" in _PER_SERVER_LOCKS


def test_second_call_reuses_server(tmp_path: Path, fake_which: None, fake_start: None) -> None:
    """Two calls for the same spec → one spawn, cached process reused."""
    spec = LSPServerSpec(name="python", command="pylsp", extensions=(".py",))
    cfg = _make_cfg(spec)
    s1 = get_or_start(str(tmp_path / "a.py"), cfg)
    s2 = get_or_start(str(tmp_path / "b.py"), cfg)
    assert s1 is s2
    assert lm.start.call_count == 1


def test_concurrent_calls_share_one_server(
    tmp_path: Path, fake_which: None, fake_start: None,
) -> None:
    """5 concurrent threads call get_or_start for the same spec → one spawn."""
    spec = LSPServerSpec(name="python", command="pylsp", extensions=(".py",))
    cfg = _make_cfg(spec)
    # Slow the handshake so threads race for the lock instead of stacking up.
    original_side_effect = lm.start.side_effect

    def slow_start(server):
        time.sleep(0.05)
        _fake_start(server)

    lm.start.side_effect = slow_start
    try:
        def call():
            return get_or_start(str(tmp_path / "x.py"), cfg)
        with ThreadPoolExecutor(max_workers=5) as ex:
            results = list(ex.map(lambda _: call(), range(5)))
        assert lm.start.call_count == 1
        assert all(r is results[0] for r in results)
    finally:
        lm.start.side_effect = original_side_effect


def test_shutdown_all_clears_dict(
    tmp_path: Path, fake_which: None, fake_start: None,
) -> None:
    """shutdown_all empties the cache and calls lsp_client.shutdown per server."""
    spec_a = LSPServerSpec(name="a", command="pylsp", extensions=(".a",))
    spec_b = LSPServerSpec(name="b", command="pylsp", extensions=(".b",))
    cfg = _make_cfg(spec_a, spec_b)
    shutdown_calls: list[str] = []

    def fake_shutdown(server):
        shutdown_calls.append(server.name)

    with patch.object(lm, "shutdown", side_effect=fake_shutdown):
        get_or_start(str(tmp_path / "x.a"), cfg)
        get_or_start(str(tmp_path / "x.b"), cfg)
        assert set(_ACTIVE_SERVERS) == {"a", "b"}
        shutdown_all()
    assert set(shutdown_calls) == {"a", "b"}
    assert _ACTIVE_SERVERS == {}
    assert _PER_SERVER_LOCKS == {}


def test_shutdown_all_continues_on_individual_failure(
    tmp_path: Path, fake_which: None, fake_start: None,
) -> None:
    """First shutdown raises → second still runs, dict still cleared."""
    spec_a = LSPServerSpec(name="a", command="pylsp", extensions=(".a",))
    spec_b = LSPServerSpec(name="b", command="pylsp", extensions=(".b",))
    cfg = _make_cfg(spec_a, spec_b)
    shutdown_calls: list[str] = []

    def fake_shutdown(server):
        shutdown_calls.append(server.name)
        if server.name == "a":
            raise RuntimeError("simulated crash")

    with patch.object(lm, "shutdown", side_effect=fake_shutdown):
        get_or_start(str(tmp_path / "x.a"), cfg)
        get_or_start(str(tmp_path / "x.b"), cfg)
        shutdown_all()
    assert set(shutdown_calls) == {"a", "b"}, "second server must still be shut down"
    assert _ACTIVE_SERVERS == {}
    assert _PER_SERVER_LOCKS == {}


def test_per_server_lock_serializes_requests(
    tmp_path: Path, fake_which: None, fake_start: None,
) -> None:
    """Two threads holding the same per-server lock cannot interleave."""
    spec = LSPServerSpec(name="python", command="pylsp", extensions=(".py",))
    cfg = _make_cfg(spec)
    get_or_start(str(tmp_path / "x.py"), cfg)
    lock = get_server_lock("python")

    intervals: list[tuple[int, float, float]] = []
    intervals_lock = threading.Lock()

    def hold(worker_id: int) -> None:
        with intervals_lock:
            intervals.append((worker_id, time.monotonic(), -1.0))
        with lock:
            entry = time.monotonic()
            time.sleep(0.05)
            exit_ = time.monotonic()
        with intervals_lock:
            idx = next(i for i, t in enumerate(intervals) if t[0] == worker_id)
            intervals[idx] = (worker_id, entry, exit_)

    threads = [threading.Thread(target=hold, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    intervals.sort(key=lambda t: t[1])
    a_entry, a_exit = intervals[0][1], intervals[0][2]
    b_entry, b_exit = intervals[1][1], intervals[1][2]
    # Either a finishes before b enters, or b finishes before a enters —
    # never any overlap. The serial invariant is what the lock guarantees.
    assert a_exit <= b_entry or b_exit <= a_entry, (
        f"intervals overlapped: a=[{a_entry:.3f}, {a_exit:.3f}] "
        f"b=[{b_entry:.3f}, {b_exit:.3f}]"
    )
