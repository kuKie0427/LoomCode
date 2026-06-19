from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from loom.eval.runner import EvalCase, EvalResult

REPO_ROOT = Path(__file__).resolve().parents[3]


class HarnessTomlMissingUsesDefaults(EvalCase):
    name = "harness-toml-missing-uses-defaults"
    description = "load_config() with no harness.toml returns HarnessConfig.from_defaults() — no error"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.config import load_config
        from loom.agent.permissions import DEFAULT_POLICY
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("cfg-missing")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        assert not (wd / "harness.toml").exists(), "precondition"

        cfg = load_config(wd)
        if cfg.policy is not DEFAULT_POLICY:
            return EvalResult(name=self.name, passed=False, detail="policy not DEFAULT_POLICY")
        if cfg.disabled_tools:
            return EvalResult(name=self.name, passed=False, detail=f"disabled_tools={cfg.disabled_tools}")
        if cfg.checkpoint.every_tool_calls != 10:
            return EvalResult(name=self.name, passed=False, detail=f"every_tool_calls={cfg.checkpoint.every_tool_calls}")
        return EvalResult(name=self.name, passed=True, detail="missing file → defaults")


class HarnessTomlDenyPatternsReplace(EvalCase):
    name = "harness-toml-deny-patterns-replace"
    description = "[permissions] deny_patterns REPLACES (not appends) DEFAULT_POLICY.deny_patterns"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.config import load_config
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("cfg-replace")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "harness.toml").write_text(
            '[permissions]\ndeny_patterns = ["unique-replace-pattern"]\n',
            encoding="utf-8",
        )

        cfg = load_config(wd)
        if "unique-replace-pattern" not in cfg.policy.deny_patterns:
            return EvalResult(name=self.name, passed=False, detail="custom pattern not present")
        if "sudo" in cfg.policy.deny_patterns:
            return EvalResult(name=self.name, passed=False, detail="default 'sudo' still present (should be REPLACED)")
        return EvalResult(name=self.name, passed=True, detail=f"{len(cfg.policy.deny_patterns)} patterns, sudo gone")


class HarnessTomlDenyPatternsAddMerges(EvalCase):
    name = "harness-toml-deny-patterns-add-merges"
    description = "[permissions] deny_patterns_add APPENDS to DEFAULT_POLICY.deny_patterns"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.config import load_config
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("cfg-add")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "harness.toml").write_text(
            '[permissions]\ndeny_patterns_add = ["custom-add-pattern"]\n',
            encoding="utf-8",
        )

        cfg = load_config(wd)
        deny = cfg.policy.deny_patterns
        if "custom-add-pattern" not in deny:
            return EvalResult(name=self.name, passed=False, detail="custom pattern missing")
        if "sudo" not in deny or "dd if=" not in deny:
            return EvalResult(name=self.name, passed=False, detail="defaults not preserved")
        return EvalResult(name=self.name, passed=True, detail=f"{len(deny)} patterns (default + add)")


class HarnessTomlCheckpointThresholdsOverride(EvalCase):
    name = "harness-toml-checkpoint-thresholds-override"
    description = "[checkpoint] every_tool_calls = 5 → is_due fires at N=5, not at N=10"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.checkpoint import is_due
        from loom.agent.config import load_config
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("cfg-checkpoint")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "harness.toml").write_text(
            "[checkpoint]\nevery_tool_calls = 5\nevery_tokens = 1000\n",
            encoding="utf-8",
        )
        cfg = load_config(wd)
        if cfg.checkpoint.every_tool_calls != 5:
            return EvalResult(name=self.name, passed=False, detail=f"got {cfg.checkpoint.every_tool_calls}")
        if cfg.checkpoint.every_tokens != 1000:
            return EvalResult(name=self.name, passed=False, detail=f"got {cfg.checkpoint.every_tokens}")
        if is_due(5, 0, cfg.checkpoint.every_tool_calls, cfg.checkpoint.every_tokens) is not True:
            return EvalResult(name=self.name, passed=False, detail="is_due didn't fire at N=5")
        if is_due(4, 0, cfg.checkpoint.every_tool_calls, cfg.checkpoint.every_tokens) is not False:
            return EvalResult(name=self.name, passed=False, detail="is_due fired at N=4")
        return EvalResult(name=self.name, passed=True, detail="threshold=5 enforced")


class HarnessTomlToolDisableBlocksCall(EvalCase):
    name = "harness-toml-tool-disable-blocks-call"
    description = "[tools.bash] enabled = false → Hooks rejects bash tool calls"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.config import load_config
        from loom.agent.hooks import Hooks
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("cfg-tools")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "harness.toml").write_text(
            "[tools.bash]\nenabled = false\n",
            encoding="utf-8",
        )
        cfg = load_config(wd)
        if "bash" not in cfg.disabled_tools:
            return EvalResult(name=self.name, passed=False, detail=f"disabled_tools={cfg.disabled_tools}")
        hooks = Hooks(disabled_tools=cfg.disabled_tools)
        block = SimpleNamespace(name="bash", input={"command": "ls"})
        result = hooks.check_permission_hook("PreToolUse", block)
        if result is None:
            return EvalResult(name=self.name, passed=False, detail="bash not blocked")
        return EvalResult(name=self.name, passed=True, detail=f"bash blocked: {result[:60]}")


class HarnessTomlInvalidRaisesClearError(EvalCase):
    name = "harness-toml-invalid-raises-clear-error"
    description = "Malformed TOML raises ConfigError with line number (not silent skip)"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.config import ConfigError, load_config
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("cfg-invalid")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "harness.toml").write_text(
            "[permissions\ndeny_patterns = ['missing-bracket']\n",
            encoding="utf-8",
        )

        try:
            load_config(wd)
        except ConfigError as exc:
            msg = str(exc)
            if "harness.toml" in msg and ("line" in msg or "Expected" in msg):
                return EvalResult(name=self.name, passed=True, detail=f"raised: {msg[:80]}")
            return EvalResult(name=self.name, passed=False, detail=f"unexpected message: {msg[:80]}")
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"wrong exception: {type(exc).__name__}: {exc}",
            )
        return EvalResult(name=self.name, passed=False, detail="ConfigError not raised")


class HarnessTomlPartialOverridesKeepOtherDefaults(EvalCase):
    name = "harness-toml-partial-overrides-keep-other-defaults"
    description = "Only [permissions] set → [checkpoint] and [tools] keep defaults"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.config import load_config
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("cfg-partial")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "harness.toml").write_text(
            '[permissions]\ndeny_patterns_add = ["custom-only"]\n',
            encoding="utf-8",
        )
        cfg = load_config(wd)
        if "custom-only" not in cfg.policy.deny_patterns:
            return EvalResult(name=self.name, passed=False, detail="custom not present")
        if cfg.checkpoint.every_tool_calls != 10:
            return EvalResult(name=self.name, passed=False, detail=f"ckpt={cfg.checkpoint.every_tool_calls}")
        if cfg.disabled_tools:
            return EvalResult(name=self.name, passed=False, detail=f"disabled={cfg.disabled_tools}")
        return EvalResult(name=self.name, passed=True, detail="partial override, others default")


class HarnessTomlInitScaffoldsSkeleton(EvalCase):
    name = "harness-toml-init-scaffolds-skeleton"
    description = "loop init writes a commented harness.toml skeleton (helps users discover the config)"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.config import load_config
        from loom.eval._util import make_empty_workdir, run_loop_cli

        wd = make_empty_workdir("cfg-init-scaffold")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        r = run_loop_cli("init", existing_workdir=str(wd), target_name="cfg-init-scaffold-2")
        if r.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"init exit {r.returncode}: {r.stderr[:120]}")
        harness_path = wd / "harness.toml"
        if not harness_path.exists():
            return EvalResult(name=self.name, passed=False, detail="harness.toml not scaffolded")
        text = harness_path.read_text(encoding="utf-8")
        if "[permissions]" not in text:
            return EvalResult(name=self.name, passed=False, detail="skeleton missing [permissions]")
        if "[checkpoint]" not in text:
            return EvalResult(name=self.name, passed=False, detail="skeleton missing [checkpoint]")
        if "[tools." not in text:
            return EvalResult(name=self.name, passed=False, detail="skeleton missing [tools]")

        cfg = load_config(wd)
        if "sudo" not in cfg.policy.deny_patterns:
            return EvalResult(name=self.name, passed=False, detail="defaults not preserved in scaffold")
        return EvalResult(name=self.name, passed=True, detail=f"skeleton {len(text)} chars; defaults preserved")