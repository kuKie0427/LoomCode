"""Harness eval cases for f-lsp-subagent-docs (Phase PL-4).

Two product-behavior guarantees:
1. lsp-subagent-tools-exposed — SUB_TOOLS contains the 3 lsp_* entries
   (by name). Subagents (`spawn_subagent`) consume SUB_TOOLS as their tool
   schema; if the LSP tools aren't there, the subagent cannot ask "where
   is this defined?" or "rename this symbol".
2. lsp-subagent-handlers-routed — SUB_HANDLERS contains 3 lsp_* keys
   that each map to a callable. The dispatch in `spawn_subagent` uses
   `SUB_HANDLERS.get(block.name)`; a missing handler silently produces
   `"Unknown: <name>"` and the agent loses the round.
"""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class LSPSubagentToolsExposed(EvalCase):
    name = "lsp-subagent-tools-exposed"
    description = (
        "SUB_TOOLS contains 3 lsp_* entries by name "
        "(goto_definition, find_references, rename_symbol). "
        "Subagents consume SUB_TOOLS as their tool schema."
    )

    def run(self) -> EvalResult:
        from loom.agent.tools import SUB_TOOLS

        sub_names = {t["name"] for t in SUB_TOOLS}
        wanted = {"lsp_goto_definition", "lsp_find_references", "lsp_rename_symbol"}
        missing = wanted - sub_names
        if missing:
            return EvalResult(name=self.name, passed=False,
                              detail=f"SUB_TOOLS missing: {sorted(missing)}")
        return EvalResult(name=self.name, passed=True,
                          detail=f"all 3 lsp_* tools present in SUB_TOOLS (total {len(sub_names)})")


class LSPSubagentHandlersRouted(EvalCase):
    name = "lsp-subagent-handlers-routed"
    description = (
        "SUB_HANDLERS contains 3 lsp_* keys, each mapping to a callable. "
        "Routed to the same run_lsp_* handler (no copy-paste)."
    )

    def run(self) -> EvalResult:
        from loom.agent import tools

        wanted = ("lsp_goto_definition", "lsp_find_references", "lsp_rename_symbol")
        problems: list[str] = []
        for name in wanted:
            handler = tools.SUB_HANDLERS.get(name)
            if handler is None:
                problems.append(f"missing key: {name}")
                continue
            if not callable(handler):
                problems.append(f"{name} → non-callable: {type(handler).__name__}")
                continue
            # Identity check: ensure SUB_HANDLERS points to the same module-level
            # function, not a copy or wrapper.
            module_fn = getattr(tools, f"run_{name}", None)
            if module_fn is None:
                problems.append(f"tools.run_{name} not found (handler drift)")
                continue
            if handler is not module_fn:
                problems.append(f"{name} → not identity-equal to tools.run_{name}")
        if problems:
            return EvalResult(name=self.name, passed=False,
                              detail="; ".join(problems))
        return EvalResult(name=self.name, passed=True,
                          detail="all 3 lsp_* handlers routed to run_lsp_* (identity-equal)")
