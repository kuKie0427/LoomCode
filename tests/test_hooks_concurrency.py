from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from loop.agent.hooks import HOOKS, HOOKS_LOCK, Hooks


def _reset_hooks():
    HOOKS["AgentStart"].clear()
    HOOKS["PreToolUse"].clear()
    HOOKS["PostToolUse"].clear()
    HOOKS["AgentStop"].clear()


def test_register_hook_is_thread_safe():
    """Concurrent register_hook calls don't lose callbacks or corrupt the list."""
    _reset_hooks()
    h = Hooks()
    errors = []

    def make_callback(idx):
        def cb(event, *args):
            return None
        return cb

    def register_many():
        try:
            for i in range(100):
                h.register_hook("AgentStart", make_callback(i))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=register_many) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(HOOKS["AgentStart"]) == 1000


def test_trigger_hooks_is_thread_safe():
    """Concurrent trigger_hooks calls don't iterate over a mutating list."""
    _reset_hooks()
    h = Hooks()

    def cb(event, *args):
        return None

    h.register_hook("AgentStart", cb)
    h.register_hook("AgentStart", cb)
    h.register_hook("AgentStart", cb)

    def trigger_many():
        for _ in range(1000):
            result = h.trigger_hooks("AgentStart", "arg")
            assert result is None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(trigger_many) for _ in range(8)]
        for f in futures:
            f.result()


def test_hook_lock_exists():
    assert HOOKS_LOCK is not None
    assert hasattr(HOOKS_LOCK, "acquire")
