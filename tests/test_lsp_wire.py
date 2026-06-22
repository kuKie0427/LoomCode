"""Tests for f-lsp-wire-tool-registry (Phase PL-1).

Covers:
- HarnessConfig.lsp dataclass (default empty, parse minimal, parse error)
- LSPConfig.find_for routing (extension match + unknown extension)
- run_lsp_* handler fail-closed behavior (no config → error string, no raise)
- safe_path integration (escape attempts return "Error: ...")
- TOOL_REGISTRY membership (3 tools present) and SUB_TOOLS exclusion (PL-4)
- _coerce_lsp_line R6 mitigation (passthrough, auto-decrement, unreadable file)

No real LSP server is spawned; lsp_manager.get_or_start always raises
NotImplementedError in PL-1 and the run_lsp_* handlers convert that to a
"LSP unavailable: ..." string. The ConfigError path uses a tmpdir harness.toml.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loom.agent.config import (
    ConfigError,
    HarnessConfig,
    LSPConfig,
    LSPServerSpec,
    load_config,
)
from loom.agent.tools import (
    SUB_TOOLS,
    TOOL_REGISTRY,
    _coerce_lsp_line,
    run_lsp_find_references,
    run_lsp_goto_definition,
    run_lsp_rename_symbol,
)


def _set_workdir(monkeypatch, p: Path) -> None:
    monkeypatch.setattr("loom.agent.tools.WORKDIR", p)


class TestLSPConfigDefaults:
    def test_lsp_config_default_empty(self) -> None:
        cfg = HarnessConfig.from_defaults()
        assert isinstance(cfg.lsp, LSPConfig)
        assert cfg.lsp.servers == ()

    def test_lsp_config_from_defaults_is_empty_tuple(self) -> None:
        assert LSPConfig.from_defaults().servers == ()


class TestLSPConfigParse:
    def test_lsp_config_parse_minimal(self, tmp_path: Path) -> None:
        (tmp_path / "harness.toml").write_text(
            '[lsp.python]\n'
            'command = "pylsp"\n'
            'extensions = [".py"]\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert len(cfg.lsp.servers) == 1
        spec = cfg.lsp.servers[0]
        assert spec.name == "python"
        assert spec.command == "pylsp"
        assert spec.extensions == (".py",)
        assert spec.args == ()

    def test_lsp_config_parse_with_args(self, tmp_path: Path) -> None:
        (tmp_path / "harness.toml").write_text(
            '[lsp.typescript]\n'
            'command = "typescript-language-server"\n'
            'args = ["--stdio"]\n'
            'extensions = [".ts", ".tsx"]\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        spec = cfg.lsp.servers[0]
        assert spec.args == ("--stdio",)
        assert spec.extensions == (".ts", ".tsx")

    def test_lsp_config_parse_missing_command_errors(self, tmp_path: Path) -> None:
        (tmp_path / "harness.toml").write_text(
            '[lsp.python]\n'
            'extensions = [".py"]\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match=r"\[lsp\.python\]\.command must be"):
            load_config(tmp_path)

    def test_lsp_config_parse_missing_extensions_errors(self, tmp_path: Path) -> None:
        (tmp_path / "harness.toml").write_text(
            '[lsp.python]\n'
            'command = "pylsp"\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match=r"\[lsp\.python\]\.extensions"):
            load_config(tmp_path)

    def test_lsp_config_parse_missing_section_uses_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "harness.toml").write_text(
            '[permissions]\ndeny_patterns = []\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert cfg.lsp.servers == ()


class TestLSPConfigFindFor:
    def test_find_for_extension(self) -> None:
        spec = LSPServerSpec(
            name="python",
            command="pylsp",
            extensions=(".py",),
        )
        cfg = LSPConfig(servers=(spec,))
        assert cfg.find_for("/some/path/x.py") is spec

    def test_find_for_unknown_returns_none(self) -> None:
        spec = LSPServerSpec(
            name="python",
            command="pylsp",
            extensions=(".py",),
        )
        cfg = LSPConfig(servers=(spec,))
        assert cfg.find_for("/some/path/x.unknown") is None

    def test_find_for_first_match_wins(self) -> None:
        a = LSPServerSpec(name="a", command="x", extensions=(".py",))
        b = LSPServerSpec(name="b", command="y", extensions=(".py",))
        cfg = LSPConfig(servers=(a, b))
        assert cfg.find_for("/x.py") is a


class TestRunLSPFailClosed:
    def test_run_lsp_goto_when_no_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        _set_workdir(monkeypatch, tmp_path)
        (tmp_path / "x.py").write_text("x = 1\n")
        out = run_lsp_goto_definition(path="x.py", line=0, character=0)
        # PL-1: lsp_manager stub raises NotImplementedError → "LSP unavailable"
        # OR PL-2 wired up and config is empty → "No LSP server configured".
        # Either is a fail-closed string return (no exception).
        assert "LSP unavailable" in out or "No LSP server configured" in out

    def test_run_lsp_find_references_when_no_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        _set_workdir(monkeypatch, tmp_path)
        (tmp_path / "x.py").write_text("x = 1\n")
        out = run_lsp_find_references(path="x.py", line=0, character=0)
        assert "LSP unavailable" in out or "No LSP server configured" in out

    def test_run_lsp_rename_when_no_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        _set_workdir(monkeypatch, tmp_path)
        (tmp_path / "x.py").write_text("x = 1\n")
        out = run_lsp_rename_symbol(path="x.py", line=0, character=0, new_name="y")
        assert "LSP unavailable" in out or "No LSP server configured" in out

    def test_run_lsp_goto_safe_path_violation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        _set_workdir(monkeypatch, tmp_path)
        # /etc/passwd escapes WORKDIR=tmp_path → safe_path raises ValueError
        out = run_lsp_goto_definition(path="/etc/passwd", line=0, character=0)
        assert out.startswith("Error:")
        assert "escapes" in out.lower() or "outside" in out.lower() or "workspace" in out.lower()

    def test_run_lsp_find_references_safe_path_violation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        _set_workdir(monkeypatch, tmp_path)
        out = run_lsp_find_references(path="/etc/passwd", line=0, character=0)
        assert out.startswith("Error:")

    def test_run_lsp_rename_safe_path_violation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        _set_workdir(monkeypatch, tmp_path)
        out = run_lsp_rename_symbol(path="/etc/passwd", line=0, character=0, new_name="y")
        assert out.startswith("Error:")


class TestToolRegistration:
    def test_lsp_tools_in_tool_registry(self) -> None:
        names = set(TOOL_REGISTRY.names())
        assert "lsp_goto_definition" in names
        assert "lsp_find_references" in names
        assert "lsp_rename_symbol" in names

    def test_lsp_tools_NOT_in_sub_tools(self) -> None:
        sub_names = {t["name"] for t in SUB_TOOLS}
        assert "lsp_goto_definition" not in sub_names
        assert "lsp_find_references" not in sub_names
        assert "lsp_rename_symbol" not in sub_names

    def test_lsp_tool_read_only_flags(self) -> None:
        assert TOOL_REGISTRY.get("lsp_goto_definition").is_read_only is True
        assert TOOL_REGISTRY.get("lsp_find_references").is_read_only is True
        assert TOOL_REGISTRY.get("lsp_rename_symbol").is_read_only is False


class TestCoerceLspLine:
    def test_coerce_lsp_line_passthrough(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        f = tmp_path / "f.py"
        f.write_text("\n".join(f"line {i}" for i in range(100)))
        out = _coerce_lsp_line(f, 50)
        assert out == 50

    def test_coerce_lsp_line_auto_decrement(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # 10-line file: line=10 is exactly len(file_lines), so 1-indexed → 0-indexed.
        f = tmp_path / "small.py"
        f.write_text("\n".join(f"line {i}" for i in range(10)))
        out = _coerce_lsp_line(f, 10)
        assert out == 9

    def test_coerce_lsp_line_unreadable_file_passthrough(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # Path does not exist → read_text raises FileNotFoundError (an OSError)
        missing = tmp_path / "missing.py"
        out = _coerce_lsp_line(missing, 42)
        assert out == 42

    def test_coerce_lsp_line_zero_passthrough(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # line=0 is valid 0-indexed; do not touch it.
        f = tmp_path / "f.py"
        f.write_text("a\nb\nc\n")
        out = _coerce_lsp_line(f, 0)
        assert out == 0