"""Unit tests for the hook module (Hooks class and HOOKS global)."""

from unittest.mock import Mock

import pytest

import hook as hook_module
from hook import HOOKS, Hooks


class MockBlock:
    """Minimal block-like object with ``name`` and ``input`` attributes."""

    def __init__(self, name: str, input: dict):
        self.name = name
        self.input = input


@pytest.fixture(autouse=True)
def hooks():
    """Clear HOOKS and register default log_hook callbacks."""
    hook_module.HOOKS.clear()
    for event in ("AgentStart", "PreToolUse", "PostToolUse", "AgentStop"):
        hook_module.HOOKS[event] = []
    h = Hooks()
    for event in hook_module.HOOKS:
        h.register_hook(event, h.log_hook)
    return h


class TestRegisterHook:
    def test_register_hook_adds_callback(self, hooks):
        """register_hook appends a callback to the event's HOOKS list."""
        cb = Mock()
        hooks.register_hook("PreToolUse", cb)
        assert len(HOOKS["PreToolUse"]) == 2  # default log_hook + cb


class TestTriggerHooks:
    def test_trigger_hooks_calls_all_callbacks(self, hooks):
        """All registered callbacks are invoked during trigger."""
        block = MockBlock("bash", {"command": "ls"})
        cbs = [Mock(return_value=None) for _ in range(3)]
        for cb in cbs:
            hooks.register_hook("PreToolUse", cb)

        result = hooks.trigger_hooks("PreToolUse", block)

        assert result is None
        total_calls = sum(cb.call_count for cb in cbs)
        assert total_calls >= 3

    def test_trigger_hooks_stops_on_non_none(self):
        """When a callback returns non-None, remaining callbacks are not called."""
        hook_module.HOOKS.clear()
        for event in ("AgentStart", "PreToolUse", "PostToolUse", "AgentStop"):
            hook_module.HOOKS[event] = []

        h = Hooks()
        cb1 = Mock(return_value="blocked")
        cb2 = Mock(return_value=None)
        h.register_hook("PreToolUse", cb1)
        h.register_hook("PreToolUse", cb2)
        block = MockBlock("bash", {"command": "ls"})

        result = h.trigger_hooks("PreToolUse", block)

        assert result == "blocked"
        cb1.assert_called_once()
        cb2.assert_not_called()


class TestCheckPermission:
    def test_check_permission_deny_list_blocks(self, hooks, monkeypatch):
        """A command matching the deny list is blocked without user prompt."""
        monkeypatch.setattr(hooks, "_ask_user", lambda *args: "deny")
        block = MockBlock("bash", {"command": "rm -rf /"})
        result = hooks.check_permission_hook("PreToolUse", block)
        assert result == "Permission denied."

    def test_check_permission_allow_safe_command(self, hooks, monkeypatch):
        """A safe command passes the deny list and triggers no rules."""
        monkeypatch.setattr(hooks, "_ask_user", lambda *args: "allow")
        block = MockBlock("bash", {"command": "ls -la"})
        result = hooks.check_permission_hook("PreToolUse", block)
        assert result is None


class TestCheckRules:
    def test_check_rules_outside_workspace(self, hooks, monkeypatch, temp_workdir):
        """Writing outside the workspace is blocked after user denies."""
        monkeypatch.setattr(hook_module, "WORKDIR", temp_workdir)
        monkeypatch.setattr(hooks, "_ask_user", lambda *args: "deny")
        block = MockBlock("write_file", {"path": "/etc/passwd"})
        result = hooks.check_permission_hook("PreToolUse", block)
        assert result == "Permission denied."

    def test_check_rules_inside_workspace(self, hooks, monkeypatch, temp_workdir):
        """Writing inside the workspace passes all rules."""
        monkeypatch.setattr(hook_module, "WORKDIR", temp_workdir)
        monkeypatch.setattr(hooks, "_ask_user", lambda *args: "allow")
        inside_path = temp_workdir / "test.txt"
        block = MockBlock("write_file", {"path": str(inside_path)})
        result = hooks.check_permission_hook("PreToolUse", block)
        assert result is None


class TestLogHook:
    def test_log_hook_returns_none(self):
        """log_hook always returns None for every event type."""
        h = Hooks()
        block = MockBlock("bash", {"command": "ls"})
        assert h.log_hook("AgentStart") is None
        assert h.log_hook("PreToolUse", block) is None
        assert h.log_hook("PostToolUse", block, "output") is None
        assert h.log_hook("AgentStop") is None
