from pathlib import Path

from loguru import logger

WORKDIR = Path.cwd()

DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda"]

PERMISSION_RULES = [
    {"tools": ["write_file", "edit_file"],
     "check": lambda args: not (WORKDIR / args.get("path", "")).resolve().is_relative_to(WORKDIR),
     "message": "Writing outside workspace"},
    {"tools": ["bash"],
     "check": lambda args: any(kw in args.get("command", "") for kw in ["rm ", "> /etc/", "chmod 777"]),
     "message": "Potentially destructive command"},
]

HOOKS = {"AgentStart": [], "PreToolUse": [], "PostToolUse": [], "AgentStop": []}

class Hooks:

    def register_hook(self, event: str, callback):
        HOOKS[event].append(callback)

    def trigger_hooks(self, event: str, *args):
        for callback in HOOKS[event]:
            result = callback(event, *args)
            if result is not None:
                return result
        return None

    def check_permission_hook(self, event, block) -> str | None:
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
        return None

    def _check_deny_list(self, command: str) -> str | None:
        for pattern in DENY_LIST:
            if pattern in command:
                return f"Blocked: '{pattern}' is on the deny list"
        return None

    def _check_rules(self, tool_name: str, args: dict) -> str | None:
        for rule in PERMISSION_RULES:
            if tool_name in rule["tools"] and rule["check"](args):
                return rule["message"]
        return None

    def _ask_user(self, tool_name: str, args: dict, reason: str) -> str:
        logger.warning("⚠  {}", reason)
        logger.info("   Tool: {} ({})", tool_name, args)
        choice = input("   Allow? [y/N] ").strip().lower()
        return "allow" if choice in ("y", "yes") else "deny"


if __name__ == "__main__":
    hooks = Hooks()
    class Block:
        def __init__(self, name, input):
            self.name = name
            self.input = input

    test_block = Block("bash", {"command": "rm -rf /"})
    result = hooks.trigger_hooks("PreToolUse", test_block)
    logger.info("Hook result: {}", result)
