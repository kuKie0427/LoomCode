"""Harness eval cases for f-lsp-wire-tool-registry (Phase PL-1).

Three product-behavior guarantees:
1. All 3 LSP tools are registered in TOOL_REGISTRY (lsp_goto_definition,
   lsp_find_references, lsp_rename_symbol).
2. The LSP tools fail-closed when no server is configured — they return a
   structured string, they never raise. This is the regression net for the
   PL-1 contract that the agent does not crash when LSP is unavailable.
3. Config parsing is backward-compatible: harness.toml with NO [lsp] section
   loads cleanly and yields LSPConfig(servers=()).

Mirrors the eval-case shape used by cold_archive.py / lsp_client.py.
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class LSPWireToolsRegistered(EvalCase):
    name = "lsp-wire-tools-registered"
    description = "3 LSP tools (goto_definition, find_references, rename_symbol) are in TOOL_REGISTRY"

    def run(self) -> EvalResult:
        from loom.agent.tools import TOOL_REGISTRY

        names = set(TOOL_REGISTRY.names())
        missing = [n for n in ("lsp_goto_definition", "lsp_find_references", "lsp_rename_symbol")
                   if n not in names]
        if missing:
            return EvalResult(name=self.name, passed=False,
                              detail=f"missing tools: {missing}")
        return EvalResult(name=self.name, passed=True,
                          detail="all 3 lsp_* tools registered")


class LSPWireFailClosedNoConfig(EvalCase):
    name = "lsp-wire-fail-closed-no-config"
    description = (
        "run_lsp_goto_definition returns a fail-closed string when no LSP "
        "server is configured; never raises"
    )

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path

        from loom.agent import tools

        original_workdir = tools.WORKDIR
        try:
            with tempfile.TemporaryDirectory() as d:
                wd = Path(d)
                tools.WORKDIR = wd
                (wd / "x.py").write_text("x = 1\n")
                out = tools.run_lsp_goto_definition(path="x.py", line=0, character=0)
                if not isinstance(out, str):
                    return EvalResult(name=self.name, passed=False,
                                      detail=f"non-string return: {type(out).__name__}")
                # PL-1 stub raises NotImplementedError → "LSP unavailable".
                # PL-2 + empty config → "No LSP server configured".
                # Either branch satisfies the fail-closed contract.
                if "LSP unavailable" not in out and "No LSP server configured" not in out:
                    return EvalResult(name=self.name, passed=False,
                                      detail=f"unexpected return: {out!r}")
        finally:
            tools.WORKDIR = original_workdir
        return EvalResult(name=self.name, passed=True,
                          detail=f"fail-closed: returned {out!r}")


class LSPWireConfigParseSurvivesMissingSection(EvalCase):
    name = "lsp-wire-config-parse-survives-missing-section"
    description = "harness.toml without [lsp] section → load_config returns LSPConfig(servers=())"

    def run(self) -> EvalResult:
        import shutil
        from pathlib import Path

        from loom.agent.config import LSPConfig, load_config
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("lsp-wire-missing-section")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "harness.toml").write_text(
            '[permissions]\ndeny_patterns = []\n',
            encoding="utf-8",
        )

        cfg = load_config(Path(wd))
        if not isinstance(cfg.lsp, LSPConfig):
            return EvalResult(name=self.name, passed=False,
                              detail=f"lsp is {type(cfg.lsp).__name__}, not LSPConfig")
        if cfg.lsp.servers != ():
            return EvalResult(name=self.name, passed=False,
                              detail=f"servers = {cfg.lsp.servers!r}, want ()")
        return EvalResult(name=self.name, passed=True,
                          detail="missing [lsp] section → LSPConfig(servers=())")