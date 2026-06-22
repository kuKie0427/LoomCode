"""Harness eval cases for f-harness-as-product-polish-p4."""

from __future__ import annotations

import tempfile
from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class HarnessPolishWorkflowTemplateExists(EvalCase):
    name = "harness-polish-workflow-template-exists"
    description = "loom init scaffolds a valid .github/workflows/loom-eval.yml"

    def run(self) -> EvalResult:
        from loom.init_cmd import init
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)
            init(target)
            wf = target / ".github" / "workflows" / "loom-eval.yml"
            if not wf.exists():
                return EvalResult(name=self.name, passed=False, detail="workflow not created")
            content = wf.read_text()
            if "name: loom eval" not in content:
                return EvalResult(name=self.name, passed=False, detail="missing workflow name")
            if "loom.cli eval" not in content:
                return EvalResult(name=self.name, passed=False, detail="missing eval invocation")
            if "--fail-under" not in content:
                return EvalResult(name=self.name, passed=False, detail="missing fail-under flag")
        return EvalResult(name=self.name, passed=True, detail="workflow template scaffolds correctly")


class HarnessPolishWorkflowTriggers(EvalCase):
    name = "harness-polish-workflow-triggers"
    description = "workflow fires on push + pull_request to main"

    def run(self) -> EvalResult:
        from loom.init_cmd import init
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)
            init(target)
            wf = target / ".github" / "workflows" / "loom-eval.yml"
            content = wf.read_text()
            for trigger in ("push:", "pull_request:", "branches: [main]"):
                if trigger not in content:
                    return EvalResult(name=self.name, passed=False, detail=f"missing {trigger}")
        return EvalResult(name=self.name, passed=True, detail="workflow triggers correct")


class HarnessPolishWorkflowParsesAsYaml(EvalCase):
    name = "harness-polish-workflow-parses-as-yaml"
    description = "workflow template parses as valid YAML with expected structure"

    def run(self) -> EvalResult:
        from loom.init_cmd import init
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml is None:
            return EvalResult(name=self.name, passed=True, detail="PyYAML unavailable; skipped")
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)
            init(target)
            wf = target / ".github" / "workflows" / "loom-eval.yml"
            data = yaml.safe_load(wf.read_text())
            if not isinstance(data, dict):
                return EvalResult(name=self.name, passed=False, detail="not a YAML mapping")
            if data.get("name") != "loom eval":
                return EvalResult(name=self.name, passed=False, detail=f"name={data.get('name')}")
            jobs = data.get("jobs", {})
            if "eval" not in jobs:
                return EvalResult(name=self.name, passed=False, detail="missing 'eval' job")
        return EvalResult(name=self.name, passed=True, detail="YAML parses; eval job present")