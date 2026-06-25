import threading
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from loom.agent.permissions import (
    DEFAULT_POLICY,
    PermissionPolicy,
    _mcp_pattern_matches,
)

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
            HOOKS.setdefault(event, []).append(callback)

    def trigger_hooks(self, event: str, *args):
        with HOOKS_LOCK:
            callbacks = list(HOOKS.get(event, []))
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
            # P2-1: three-state bash permission gate (deny → allow → rules).
            # Order mirrors Claude Code and opencode v2:
            #   1. deny_patterns (substring, bypass-immune) — hard block
            #   2. allow_patterns (wildcard, silent) — auto-allow, no prompt
            #   3. rules (interactive) — _ask_user y/N
            # deny is always checked first and cannot be overridden by allow.
            command = block.input.get("command", "")
            reason = self._check_deny_list(command)
            if reason:
                logger.warning("⛔ {}", reason)
                return "Permission denied."
            # allow_patterns check: silent allow, skip the rules prompt.
            allow_match = self.policy.matches_allow(command)
            if allow_match is not None:
                return None
        # PM-2: MCP 3-state permission gate (M5). Runs BEFORE the generic
        # rule check so mcp__* tools are NOT subject to the unrelated
        # write_file / bash / lsp_rename rules below. A return value of
        # None means "allow"; a non-empty string means "deny".
        #
        # The isinstance guard handles the case where callers (typically
        # in tests) pass a MagicMock for the tool block; ``block.name``
        # then auto-attributes to a truthy MagicMock, which would
        # otherwise wrongly enter the MCP gate and fall through to
        # ``_ask_user`` (causing an EOFError on captured stdin). Real
        # blocks from ``_run_tool_block`` always carry a ``str`` name.
        if isinstance(block.name, str) and block.name.startswith("mcp__"):
            reason = self._check_mcp_permissions(block.name, block.input)
            if reason is not None:
                return reason
        reason = self._check_rules(block.name, block.input)
        if reason:
            from loom.agent import permission_store

            if permission_store.is_granted(WORKDIR, block.name, block.input):
                return None
            decision = self._ask_user(block.name, block.input, reason)
            if decision == "deny":
                return "Permission denied."
            if decision == "allow_always":
                permission_store.grant(WORKDIR, block.name, block.input)
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

    def _check_mcp_permissions(self, tool_name: str, args: dict) -> str | None:
        """3-state MCP permission gate (M5). Returns None to allow, string to block.

        Order matters: deny patterns hard-block first (no user override),
        auto_approve patterns silent-allow second, and ONLY when both
        lists miss does the call fall through to a y/N prompt via
        ``self._ask_user``. There is no "allow by default" path — the
        M5 invariant is that every ``mcp__*`` call ends up in exactly
        one of deny / auto_approve / prompt.

        The active config is read from ``loop._active_config`` (lazy
        import) so a stale module-level snapshot at agent startup does
        not freeze the rule set.
        """
        from loom.agent.loop import _active_config

        perms = _active_config.mcp.permissions
        for pattern in perms.deny:
            if _mcp_pattern_matches(pattern, tool_name):
                reason = (
                    f"Permission denied: MCP tool '{tool_name}' matches "
                    f"deny pattern '{pattern}'"
                )
                logger.warning("⛔ {}", reason)
                return reason
        for pattern in perms.auto_approve:
            if _mcp_pattern_matches(pattern, tool_name):
                return None
        decision = self._ask_user(
            tool_name, args,
            f"MCP tool '{tool_name}' is calling an external server. Allow?",
        )
        if decision == "deny":
            return "Permission denied by user."
        return None

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
