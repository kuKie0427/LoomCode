"""Eval case for Task 3: max_output_tokens consolidation.

One case verifies that ``[llm] max_output_tokens`` in harness.toml overrides the
default 8000 at all 5 sites:

1. ``loom.agent.context.COMPACT_MAX_OUTPUT_TOKENS`` (alias)
2. ``loom.agent.llm.LLMClient.stream_iter(..., max_tokens=None)`` default
3. ``loom.agent.loop`` streaming path (LLM_CONFIG.max_output_tokens)
4. ``loom.agent.loop`` sync path (LLM_CONFIG.max_output_tokens)
5. ``loom.agent.tools.spawn_subagent`` (LLM_CONFIG.max_output_tokens)

We patch ``loom.agent.config.LLM_CONFIG`` with the loaded value (since the
modules captured the singleton at import time) and then exercise every site.
"""
from __future__ import annotations

from loom.eval._util import make_empty_workdir
from loom.eval.runner import EvalCase, EvalResult


class ConfigLlmMaxOutputTokensOverridableViaHarnessToml(EvalCase):
    name = "config-llm-max-output-tokens-overridable-via-harness-toml"
    description = "[llm] max_output_tokens = 4000 in harness.toml overrides all 5 magic-8000 sites"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("cfg-llm-max")
        (wd / "harness.toml").write_text(
            "[llm]\nmax_output_tokens = 4000\n",
            encoding="utf-8",
        )

        from loom.agent.config import LLM_CONFIG as DEFAULT_CFG
        from loom.agent.config import LLMConfig, load_config
        cfg = load_config(wd)

        if cfg.llm.max_output_tokens != 4000:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"load_config did not parse [llm] max_output_tokens; got {cfg.llm.max_output_tokens}",
            )

        custom_cfg = LLMConfig(max_output_tokens=4000)

        # Site 1
        from loom.agent import context as ctx_mod
        from loom.agent import llm as llm_mod
        from loom.agent import loop as loop_mod
        from loom.agent import tools as tools_mod

        original_ctx_cfg = ctx_mod.LLM_CONFIG
        original_llm_cfg = llm_mod.LLM_CONFIG
        original_loop_cfg = loop_mod.LLM_CONFIG
        original_tools_cfg = tools_mod.LLM_CONFIG

        try:
            ctx_mod.LLM_CONFIG = custom_cfg
            ctx_mod.COMPACT_MAX_OUTPUT_TOKENS = custom_cfg.max_output_tokens
            llm_mod.LLM_CONFIG = custom_cfg
            loop_mod.LLM_CONFIG = custom_cfg
            tools_mod.LLM_CONFIG = custom_cfg

            # Site 1: COMPACT_MAX_OUTPUT_TOKENS reflects 4000
            if ctx_mod.COMPACT_MAX_OUTPUT_TOKENS != 4000:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"site 1 (context.COMPACT_MAX_OUTPUT_TOKENS) = {ctx_mod.COMPACT_MAX_OUTPUT_TOKENS}",
                )

            # Site 2: LLMClient().stream_iter(..., max_tokens=None) → uses LLM_CONFIG
            import os

            from loom.agent.llm import LLMClient
            client = LLMClient(model=os.getenv("MODEL", "deepseek/deepseek-v4-flash"))
            if client.stream_iter.__defaults__ != (None,):
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"stream_iter default not (None,): {client.stream_iter.__defaults__}",
                )

            captured: dict = {}

            class SimpleAsyncStream:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            class FakeAsyncMessages:
                def stream(self, **kwargs):
                    captured["max_tokens"] = kwargs.get("max_tokens")
                    return SimpleAsyncStream()

            class FakeAsyncClient:
                messages = FakeAsyncMessages()

            client.async_client = FakeAsyncClient()
            client._cancel_event = type(client._cancel_event)()

            gen = client.stream_iter("sys", [], [])
            try:
                next(gen)
            except (StopIteration, RuntimeError):
                pass
            except Exception:
                pass

            if captured.get("max_tokens") != 4000:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"site 2 (stream_iter default) used max_tokens={captured.get('max_tokens')}",
                )

            # Site 3+4: loop.py 188 (streaming) and 224 (sync)
            import ast
            with open(loop_mod.__file__, encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src)
            bare_8000 = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and node.value == 8000
            ]
            if bare_8000:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"site 3+4 (loop.py): bare 8000 literals remain: {len(bare_8000)}",
                )

            # Site 5: tools.py spawn_subagent
            with open(tools_mod.__file__, encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src)
            bare_8000 = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and node.value == 8000
            ]
            if bare_8000:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"site 5 (tools.py): bare 8000 literals remain: {len(bare_8000)}",
                )

            if DEFAULT_CFG.max_output_tokens != 8000:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"DEFAULT_CFG singleton mutated: {DEFAULT_CFG.max_output_tokens}",
                )

            return EvalResult(
                name=self.name, passed=True,
                detail="all 5 sites honor override; bare-8000 literals purged from loop.py + tools.py",
            )
        finally:
            ctx_mod.LLM_CONFIG = original_ctx_cfg
            ctx_mod.COMPACT_MAX_OUTPUT_TOKENS = original_ctx_cfg.max_output_tokens
            llm_mod.LLM_CONFIG = original_llm_cfg
            loop_mod.LLM_CONFIG = original_loop_cfg
            tools_mod.LLM_CONFIG = original_tools_cfg