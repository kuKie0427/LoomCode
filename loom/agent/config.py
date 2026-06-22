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

import ast
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from loom.agent.checkpoint import (
    CHECKPOINT_EVERY_TOKENS,
    CHECKPOINT_EVERY_TOOL_CALLS,
)
from loom.agent.permissions import (
    DEFAULT_POLICY,
    PermissionPolicy,
    PermissionRule,
)

CONFIG_FILENAME = "harness.toml"

# Whitelist for permission-check expressions in harness.toml.
# A rule expression is a Python expression of `args -> bool` where `args`
# is a dict. The AST is validated against this whitelist BEFORE eval, so a
# malicious rule cannot escape the `{"__builtins__": {}}` sandbox via
# tricks like `().__class__.__bases__[0].__subclasses__()`.
ALLOWED_FUNCS: frozenset[str] = frozenset({
    "len", "str", "int", "any", "all",
    "isinstance", "startswith", "endswith",
})
_DENIED_ATTRS: frozenset[str] = frozenset({
    "__class__", "__bases__", "__subclasses__", "__globals__", "__dict__",
    "__mro__", "__init_subclass__", "__import__",
})
# Node types that are never valid inside a permission check expression.
_BLOCKED_NODES: tuple[type[ast.AST], ...] = (
    ast.Lambda,
    ast.FunctionDef, ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Import, ast.ImportFrom,
    ast.Global, ast.Nonlocal,
    ast.Return, ast.Yield, ast.YieldFrom,
    ast.Starred,
    ast.NamedExpr,         # walrus operator
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    ast.IfExp,             # overkill but safe — avoids `X if Y else Z` with Y=__import__
)


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
class LLMConfig:
    """LLM client tuning knobs.

    max_output_tokens: ceiling on tokens the model may emit per response.
    Default 8000. Overridable via ``[llm] max_output_tokens`` in harness.toml.
    """

    max_output_tokens: int = 8000

    @classmethod
    def from_defaults(cls) -> LLMConfig:
        return cls(max_output_tokens=8000)


# Module-level singleton — callers reference this directly for the default
# (when no HarnessConfig is in scope). Tests use harness.toml overrides.
LLM_CONFIG: LLMConfig = LLMConfig.from_defaults()


@dataclass(frozen=True)
class TelemetryConfig:
    """Telemetry sink configuration.

    sink_command: Path to a command that receives JSON events via stdin.
    If None, telemetry is disabled.
    """

    sink_command: str | None = None


@dataclass(frozen=True)
class LSPServerSpec:
    """Configuration for one LSP server entry in harness.toml.

    Example:
        [lsp.python]
        command = "pylsp"
        args = ["--check-parent-process"]
        extensions = [".py"]

    PL-2 (lsp_manager) uses these specs to lazily spawn / cache subprocesses
    for matching file extensions. PL-1 only validates the schema; no server
    is started at config-load time.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()

    def matches(self, file_path: str | Path) -> bool:
        suffix = Path(file_path).suffix
        if not suffix:
            return False
        return suffix in self.extensions


@dataclass(frozen=True)
class LSPConfig:
    """Per-project LSP server map. Empty tuple = no servers configured.

    `find_for(file_path)` returns the first server whose `extensions`
    matches the file's suffix, or None. Used by PL-2's lsp_manager.
    """

    servers: tuple[LSPServerSpec, ...] = ()

    @classmethod
    def from_defaults(cls) -> LSPConfig:
        return cls(servers=())

    def find_for(self, file_path: str | Path) -> LSPServerSpec | None:
        for spec in self.servers:
            if spec.matches(file_path):
                return spec
        return None


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for one MCP server entry in harness.toml.

    Example:
        [mcp.servers.github]
        command = "npx"
        args = ["-y", "@modelcontextprotocol/server-github"]
        env = {GITHUB_TOKEN = "ghp_..."}
        subagent_access = false

    PM-1: config-only; lifecycle handled by mcp_manager.
    PM-2: will add per-server permission gate.
    PM-4: subagent_access gates whether subagents can see this server's tools.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    subagent_access: bool = False


@dataclass(frozen=True)
class MCPConfig:
    """Per-project MCP server map. Empty tuple = no servers configured.

    PM-1: only `servers` exists. PM-2 will add a `permissions` field for
    per-server auto_approve / deny lists (the M5 gate).
    """

    servers: tuple[MCPServerConfig, ...] = ()

    @classmethod
    def from_defaults(cls) -> MCPConfig:
        return cls(servers=())


@dataclass(frozen=True)
class HarnessConfig:
    policy: PermissionPolicy
    checkpoint: CheckpointConfig
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    disabled_tools: frozenset[str] = field(default_factory=frozenset)
    run_init_sh_on_session_end: bool = True
    llm: LLMConfig = field(default_factory=LLMConfig.from_defaults)
    max_turns: int = 100
    lsp: LSPConfig = field(default_factory=LSPConfig.from_defaults)
    mcp: MCPConfig = field(default_factory=MCPConfig.from_defaults)

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
                check_fn = _compile_check(check_src, f"permissions.rules.add[{i}].check")
                if check_fn is None:
                    raise ConfigError(
                        f"[permissions.rules.add[{i}].check] expression rejected "
                        f"by AST whitelist — see warning above"
                    )
                rules.append(PermissionRule(
                    tools=tuple(tools),
                    check=check_fn,
                    message=message,
                ))

    return PermissionPolicy(deny_patterns=base_deny, rules=tuple(rules))


def _validate_check_ast(code: str) -> tuple[bool, int | None]:
    """Walk an `eval`-mode AST and verify it matches the permission-check whitelist.

    Returns (ok, line_number). On rejection, line_number points to the first
    offending node (or the SyntaxError line if parsing failed).
    """
    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError as exc:
        return False, exc.lineno
    return _check_ast_node(tree)


def _check_ast_node(node: ast.AST) -> tuple[bool, int | None]:
    if isinstance(node, _BLOCKED_NODES):
        return False, getattr(node, "lineno", None)
    if isinstance(node, ast.Name):
        if node.id != "args" and node.id not in ALLOWED_FUNCS:
            return False, node.lineno
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("__") or node.attr in _DENIED_ATTRS:
            return False, node.lineno
    if isinstance(node, ast.Subscript):
        if not isinstance(node.value, ast.Name) or node.value.id != "args":
            return False, node.lineno
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for elt in node.elts:
            if not isinstance(elt, ast.Constant):
                return False, getattr(elt, "lineno", None)
    if isinstance(node, ast.Dict):
        for k in node.keys:
            if k is not None and not isinstance(k, ast.Constant):
                return False, getattr(k, "lineno", None)
        for v in node.values:
            if not isinstance(v, ast.Constant):
                return False, getattr(v, "lineno", None)
    for child in ast.iter_child_nodes(node):
        ok, line = _check_ast_node(child)
        if not ok:
            return False, line
    return True, None


def _compile_check(expression: str, field_name: str):
    """Compile a Python expression of `args -> bool` from a string.

    The expression is first validated against the AST whitelist
    (`_validate_check_ast`) and then evaluated in a sandbox where
    `args` is a dict. Returns the callable, or None if the AST
    whitelist rejected the expression (logged as a warning).
    """
    try:
        code = compile(expression, f"<{field_name}>", "eval")
    except SyntaxError as exc:
        raise ConfigError(f"{field_name}: invalid Python expression ({exc.msg})") from exc

    ok, line = _validate_check_ast(expression)
    if not ok:
        logger.warning(
            "{}: expression rejected by AST whitelist at line {} (sandbox violation)",
            field_name, line or "?",
        )
        return None

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


DEFAULT_MAX_TURNS = 100


def _parse_agent_section(section: dict | None) -> int:
    if not section:
        return DEFAULT_MAX_TURNS
    if not isinstance(section, dict):
        raise ConfigError("[agent] must be a table")
    raw = section.get("max_turns", DEFAULT_MAX_TURNS)
    if not isinstance(raw, int) or raw < 1:
        raise ConfigError("[agent] max_turns must be a positive integer")
    return raw


def _parse_telemetry_section(section: dict | None) -> TelemetryConfig:
    if not section:
        return TelemetryConfig()
    sink = section.get("sink_command")
    if sink is not None and not isinstance(sink, str):
        raise ConfigError("[telemetry] sink_command must be a string or absent")
    return TelemetryConfig(sink_command=sink)


def _parse_llm_section(section: dict | None) -> LLMConfig:
    base = LLMConfig.from_defaults()
    if not section:
        return base
    max_tokens = section.get("max_output_tokens", base.max_output_tokens)
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ConfigError("[llm] max_output_tokens must be a positive integer")
    return LLMConfig(max_output_tokens=max_tokens)


def _parse_lsp_section(section: dict | None) -> LSPConfig:
    """Parse a top-level [lsp.*] block from harness.toml.

    Schema (per [lsp.<name>] subtable):
      command    : str (required)
      extensions : list[str] (required, e.g. [".py"])
      args       : list[str] (optional)

    PL-1 only validates and exposes the spec. PL-2 wires the lifecycle.
    """
    if not section:
        return LSPConfig.from_defaults()
    if not isinstance(section, dict):
        raise ConfigError("[lsp] must be a table of server specs")
    servers: list[LSPServerSpec] = []
    for name, cfg in section.items():
        if not isinstance(cfg, dict):
            raise ConfigError(f"[lsp.{name}] must be a table")
        command = cfg.get("command")
        if not isinstance(command, str) or not command:
            raise ConfigError(f"[lsp.{name}].command must be a non-empty string")
        extensions = cfg.get("extensions")
        if not isinstance(extensions, list) or not all(isinstance(e, str) for e in extensions):
            raise ConfigError(f"[lsp.{name}].extensions must be a list of strings")
        if not extensions:
            raise ConfigError(f"[lsp.{name}].extensions must be a non-empty list of strings")
        raw_args = cfg.get("args", [])
        if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
            raise ConfigError(f"[lsp.{name}].args must be a list of strings")
        servers.append(LSPServerSpec(
            name=name,
            command=command,
            args=tuple(raw_args),
            extensions=tuple(extensions),
        ))
    return LSPConfig(servers=tuple(servers))


def _parse_mcp_section(section: dict | None) -> MCPConfig:
    """Parse a top-level [mcp.servers.*] block from harness.toml.

    Schema (per [mcp.servers.<name>] subtable):
      command          : str (required)
      args             : list[str] (optional, default [])
      env              : dict[str, str] (optional, default {}) — explicit env
      cwd              : str (optional, default None)
      subagent_access  : bool (optional, default false)

    PM-1: only validates and exposes the spec. mcp_manager wires the lifecycle.
    """
    if not section:
        return MCPConfig.from_defaults()
    if not isinstance(section, dict):
        raise ConfigError("[mcp] must be a table")
    servers_raw = section.get("servers", {})
    if not isinstance(servers_raw, dict):
        raise ConfigError("[mcp.servers] must be a table of server specs")
    servers: list[MCPServerConfig] = []
    seen: set[str] = set()  # M3: duplicate name guard
    for name, cfg in servers_raw.items():
        if not isinstance(cfg, dict):
            raise ConfigError(f"[mcp.servers.{name}] must be a table")
        if name in seen:  # M3
            raise ConfigError(f"duplicate MCP server name '{name}'")
        seen.add(name)
        command = cfg.get("command")
        if not isinstance(command, str) or not command:
            raise ConfigError(f"[mcp.servers.{name}].command must be a non-empty string")
        raw_args = cfg.get("args", [])
        if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
            raise ConfigError(f"[mcp.servers.{name}].args must be a list of strings")
        env_raw = cfg.get("env", {})
        if not isinstance(env_raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()
        ):
            raise ConfigError(f"[mcp.servers.{name}].env must be a table of strings")
        cwd = cfg.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise ConfigError(f"[mcp.servers.{name}].cwd must be a string or absent")
        subagent = cfg.get("subagent_access", False)
        if not isinstance(subagent, bool):
            raise ConfigError(f"[mcp.servers.{name}].subagent_access must be a boolean")
        servers.append(MCPServerConfig(
            name=name,
            command=command,
            args=tuple(raw_args),
            env=dict(env_raw),
            cwd=cwd,
            subagent_access=subagent,
        ))
    return MCPConfig(servers=tuple(servers))


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
        telemetry=_parse_telemetry_section(data.get("telemetry")),
        disabled_tools=_parse_tools_section(data.get("tools")),
        llm=_parse_llm_section(data.get("llm")),
        max_turns=_parse_agent_section(data.get("agent")),
        lsp=_parse_lsp_section(data.get("lsp")),
        mcp=_parse_mcp_section(data.get("mcp")),
    )


def write_default_config(workdir: Path) -> Path:
    """Write a commented skeleton harness.toml to workdir if not present."""
    path = Path(workdir) / CONFIG_FILENAME
    if path.exists():
        return path
    path.write_text(_SKELETON, encoding="utf-8")
    return path


_SKELETON = """# harness.toml — per-project loom agent config
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

# [telemetry]
# sink_command = "/usr/local/bin/loom-collector"  # Receives JSON events via stdin

# [llm]
# max_output_tokens = 8000  # ceiling on tokens emitted per response (default 8000)

# [lsp.python]
# command = "pylsp"
# args = ["--check-parent-process"]
# extensions = [".py"]

# [lsp.typescript]
# command = "typescript-language-server"
# args = ["--stdio"]
# extensions = [".ts", ".tsx", ".js", ".jsx"]

# [mcp.servers.github]
# command = "npx"
# args = ["-y", "@modelcontextprotocol/server-github"]
# env = {GITHUB_TOKEN = "ghp_..."}  # 注意:不继承 loom 进程环境,只传显式声明的
# subagent_access = false   # PM-4:子智能体是否可用此 server 的工具
"""
