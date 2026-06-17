import threading
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from loop.agent.permissions import DEFAULT_POLICY, PermissionPolicy

WORKDIR = Path.cwd()

DENY_LIST = list(DEFAULT_POLICY.deny_patterns)


def _rule_to_dict(rule) -> dict:
    return {"tools": list(rule.tools), "check": rule.check, "message": rule.message}


PERMISSION_RULES = [_rule_to_dict(r) for r in DEFAULT_POLICY.rules]

HOOKS = {"SessionStart": [], "AgentStart": [], "PreToolUse": [], "PostToolUse": [], "PreCompact": [], "AgentStop": [], "SessionEnd": []}
HOOKS_LOCK = threading.Lock()


class Hooks:

    def __init__(
        self,
        policy: PermissionPolicy | None = None,
        disabled_tools: frozenset[str] | None = None,
        asker: Callable[[str, dict, str], str] | None = None,
    ) -> None:
        self.policy = policy if policy is not None else DEFAULT_POLICY
        self.disabled_tools = disabled_tools if disabled_tools is not None else frozenset()
        self._asker = asker if asker is not None else self._default_asker

    def register_hook(self, event: str, callback):
        with HOOKS_LOCK:
            HOOKS[event].append(callback)

    def trigger_hooks(self, event: str, *args):
        with HOOKS_LOCK:
            callbacks = list(HOOKS[event])
        for callback in callbacks:
            result = callback(event, *args)
            if result is not None:
                return result
        return None

    def check_permission_hook(self, event, block) -> str | None:
        if block.name in self.disabled_tools:
            logger.warning("⛔ Tool '{}' disabled by harness.toml", block.name)
            return f"Tool '{block.name}' disabled by harness.toml"
        if block.name == "bash":
            reason = self._check_deny_list(block.input.get("command", ""))
            if reason:
                logger.warning("⛔ {}", reason)
                return "Permission denied."
        reason = self._check_rules(block.name, block.input)
        if reason:
            decision = self._ask_user(block.name, block.input, reason)
            if decision == "deny":
                return "Permission denied."
        return None

    def log_hook(self, event, *args):
        if event == "PreToolUse":
            block = args[0]
            logger.info("> {} {}", block.name, block.input)
        elif event == "PostToolUse":
            block, output = args[0], args[1]
            logger.info("\n--- {}:\n {}\n---", block.name, str(output)[:200])
        elif event == "AgentStart":
            logger.info("[Agent spawned]")
        elif event == "AgentStop":
            logger.info("[Agent done]")
        elif event == "PreCompact":
            messages, last_input_tokens = args
            logger.info(f"[PreCompact: {len(messages)} messages, {last_input_tokens} tokens]")
        elif event == "SessionStart":
            logger.info("[Session started]")
        elif event == "SessionEnd":
            messages, tool_call_count = args
            logger.info(f"[Session ended: {tool_call_count} tool calls, {len(messages)} messages]")
        return None

    def _check_deny_list(self, command: str) -> str | None:
        pattern = self.policy.matches_deny(command)
        if pattern is not None:
            return f"Blocked: '{pattern}' is on the deny list"
        return None

    def _check_rules(self, tool_name: str, args: dict) -> str | None:
        rule = self.policy.find_rule(tool_name, args)
        return rule.message if rule is not None else None

    def _default_asker(self, tool_name: str, args: dict, reason: str) -> str:
        logger.warning("⚠  {}", reason)
        logger.info("   Tool: {} ({})", tool_name, args)
        choice = input("   Allow? [y/N] ").strip().lower()
        return "allow" if choice in ("y", "yes") else "deny"

    def _ask_user(self, tool_name: str, args: dict, reason: str) -> str:
        return self._asker(tool_name, args, reason)


if __name__ == "__main__":
    hooks = Hooks()
    class Block:
        def __init__(self, name, input):
            self.name = name
            self.input = input

    test_block = Block("bash", {"command": "rm -rf /"})
    result = hooks.trigger_hooks("PreToolUse", test_block)
    logger.info("Hook result: {}", result)
