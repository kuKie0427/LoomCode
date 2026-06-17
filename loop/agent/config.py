"""Per-project harness.toml loader.

Reads `<workdir>/harness.toml` and produces a `HarnessConfig` with three
injectable knobs:

- `policy` — replaces `DEFAULT_POLICY` from `permissions.py` (or merges
  patterns / appends rules).
- `checkpoint` — replaces `CHECKPOINT_EVERY_TOOL_CALLS` / `_TOKENS`.
- `disabled_tools` — set of tool names that the agent should refuse.

Missing file → `HarnessConfig.from_defaults()`. Malformed file →
`ConfigError` with a line number.

Roadmap promises this in three places:
- Phase 1 §3 — "Permission pipeline generalisation, configurable from harness.toml"
- Phase 3 §3 — "[tools] section lets users disable bash"
- Phase 4 §5 / Q4 — "[checkpoint] every_tool_calls = N, every_tokens = K"

Per the v1 design:
- Root: `<workdir>/harness.toml` (same level as AGENTS.md, init.sh).
- deny_patterns semantics: REPLACE (predictable). For additive, use
  deny_patterns_add.
- Optional: missing file = defaults, no error.
- Loaded once at agent startup; no hot-reload.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from loop.agent.checkpoint import (
    CHECKPOINT_EVERY_TOKENS,
    CHECKPOINT_EVERY_TOOL_CALLS,
)
from loop.agent.permissions import (
    DEFAULT_POLICY,
    PermissionPolicy,
    PermissionRule,
)

CONFIG_FILENAME = "harness.toml"


class ConfigError(Exception):
    """Raised when harness.toml is malformed or fails validation."""


@dataclass(frozen=True)
class CheckpointConfig:
    every_tool_calls: int
    every_tokens: int

    @classmethod
    def from_defaults(cls) -> CheckpointConfig:
        return cls(
            every_tool_calls=CHECKPOINT_EVERY_TOOL_CALLS,
            every_tokens=CHECKPOINT_EVERY_TOKENS,
        )


@dataclass(frozen=True)
class HarnessConfig:
    policy: PermissionPolicy
    checkpoint: CheckpointConfig
    disabled_tools: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_defaults(cls) -> HarnessConfig:
        return cls(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
        )


def _parse_policy_section(section: dict | None) -> PermissionPolicy:
    if not section:
        return DEFAULT_POLICY

    base_deny = DEFAULT_POLICY.deny_patterns
    deny = section.get("deny_patterns")
    deny_add = section.get("deny_patterns_add", [])
    if deny is not None:
        if not isinstance(deny, list) or not all(isinstance(p, str) for p in deny):
            raise ConfigError("[permissions] deny_patterns must be a list of strings")
        base_deny = tuple(deny)
    if deny_add:
        if not isinstance(deny_add, list) or not all(isinstance(p, str) for p in deny_add):
            raise ConfigError("[permissions] deny_patterns_add must be a list of strings")
        base_deny = base_deny + tuple(deny_add)

    rules = list(DEFAULT_POLICY.rules)
    rules_section = section.get("rules")
    if rules_section:
        if not isinstance(rules_section, dict):
            raise ConfigError("[permissions] rules must be a table")
        add_section = rules_section.get("add")
        if add_section is not None:
            if not isinstance(add_section, list):
                raise ConfigError("[permissions.rules] add must be a list of tables")
            for i, raw in enumerate(add_section):
                if not isinstance(raw, dict):
                    raise ConfigError(f"[permissions.rules.add[{i}]] must be a table")
                tools = raw.get("tools", [])
                if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
                    raise ConfigError(f"[permissions.rules.add[{i}].tools] must be a list of strings")
                check_src = raw.get("check")
                if not isinstance(check_src, str):
                    raise ConfigError(f"[permissions.rules.add[{i}].check] must be a string expression")
                message = raw.get("message", "")
                if not isinstance(message, str):
                    raise ConfigError(f"[permissions.rules.add[{i}].message] must be a string")
                rules.append(PermissionRule(
                    tools=tuple(tools),
                    check=_compile_check(check_src, f"permissions.rules.add[{i}].check"),
                    message=message,
                ))

    return PermissionPolicy(deny_patterns=base_deny, rules=tuple(rules))


def _compile_check(expression: str, field_name: str):
    """Compile a Python expression of `args -> bool` from a string.

    The expression is evaluated in a sandbox where `args` is a dict.
    Returns the callable.
    """
    try:
        code = compile(expression, f"<{field_name}>", "eval")
    except SyntaxError as exc:
        raise ConfigError(f"{field_name}: invalid Python expression ({exc.msg})") from exc

    def _check(args: dict) -> bool:
        try:
            return bool(eval(code, {"__builtins__": {}}, {"args": args}))
        except Exception as exc:
            raise ConfigError(f"{field_name}: raised {type(exc).__name__}: {exc}") from exc

    return _check


def _parse_checkpoint_section(section: dict | None) -> CheckpointConfig:
    base = CheckpointConfig.from_defaults()
    if not section:
        return base
    tool_calls = section.get("every_tool_calls", base.every_tool_calls)
    tokens = section.get("every_tokens", base.every_tokens)
    if not isinstance(tool_calls, int) or tool_calls <= 0:
        raise ConfigError("[checkpoint] every_tool_calls must be a positive integer")
    if not isinstance(tokens, int) or tokens <= 0:
        raise ConfigError("[checkpoint] every_tokens must be a positive integer")
    return CheckpointConfig(every_tool_calls=tool_calls, every_tokens=tokens)


def _parse_tools_section(section: dict | None) -> frozenset[str]:
    if not section:
        return frozenset()
    disabled: set[str] = set()
    for name, cfg in section.items():
        if not isinstance(cfg, dict):
            raise ConfigError(f"[tools.{name}] must be a table")
        if cfg.get("enabled") is False:
            disabled.add(name)
    return frozenset(disabled)


def load_config(workdir: Path) -> HarnessConfig:
    """Read `<workdir>/harness.toml` and return a HarnessConfig.

    Missing file → HarnessConfig.from_defaults() (no error).
    Malformed file → ConfigError.
    """
    path = Path(workdir) / CONFIG_FILENAME
    if not path.exists():
        return HarnessConfig.from_defaults()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level must be a table")

    return HarnessConfig(
        policy=_parse_policy_section(data.get("permissions")),
        checkpoint=_parse_checkpoint_section(data.get("checkpoint")),
        disabled_tools=_parse_tools_section(data.get("tools")),
    )


def write_default_config(workdir: Path) -> Path:
    """Write a commented skeleton harness.toml to workdir if not present."""
    path = Path(workdir) / CONFIG_FILENAME
    if path.exists():
        return path
    path.write_text(_SKELETON, encoding="utf-8")
    return path


_SKELETON = """# harness.toml — per-project loop agent config
# Missing or empty file = all defaults used. See docs/harness-roadmap.md.

# [permissions]
# deny_patterns = ["rm -rf /", "sudo"]        # REPLACES defaults
# deny_patterns_add = ["halt"]                # APPENDS to defaults
#
# [permissions.rules.add]
# # Custom rule: require review before writing to templates/
# [[permissions.rules.add]]
# tools = ["write_file", "edit_file"]
# check = '"templates" in args.get("path", "")'
# message = "Modifying templates requires explicit review"

# [checkpoint]
# every_tool_calls = 10    # save every N tool calls
# every_tokens = 5000      # OR when N new tokens accumulated

# [tools.bash]
# enabled = false          # disable the bash tool entirely
"""
