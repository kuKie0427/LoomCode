from __future__ import annotations

from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult

REPO_ROOT = Path(__file__).resolve().parents[3]


class CIWorkflowExists(EvalCase):
    name = "ci-workflow-exists"
    description = ".github/workflows/ci.yml exists and is non-empty"

    def run(self) -> EvalResult:
        path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        if not path.exists():
            return EvalResult(name=self.name, passed=False, detail=f"missing: {path}")
        text = path.read_text(encoding="utf-8")
        if len(text) < 100:
            return EvalResult(name=self.name, passed=False, detail=f"too short: {len(text)} chars")
        return EvalResult(name=self.name, passed=True, detail=f"{len(text)} chars")


class CIWorkflowRunsInitSh(EvalCase):
    name = "ci-workflow-runs-init-sh"
    description = "CI workflow invokes ./init.sh as its static+unit+integration gate"

    def run(self) -> EvalResult:
        path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        text = path.read_text(encoding="utf-8")
        if "./init.sh" not in text:
            return EvalResult(name=self.name, passed=False, detail="./init.sh not invoked in workflow")
        return EvalResult(name=self.name, passed=True, detail="./init.sh wired")


class CIWorkflowRunsEval(EvalCase):
    name = "ci-workflow-runs-eval"
    description = "CI workflow runs the eval suite (Phase 5 acceptance: 'loom eval runs in CI')"

    def run(self) -> EvalResult:
        path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        text = path.read_text(encoding="utf-8")
        if "loom.cli eval" not in text:
            return EvalResult(name=self.name, passed=False, detail="loom eval not invoked")
        if "--fail-under" not in text:
            return EvalResult(name=self.name, passed=False, detail="eval has no --fail-under gate")
        return EvalResult(name=self.name, passed=True, detail="eval + --fail-under wired")


class CIWorkflowTriggersOnPushAndPR(EvalCase):
    name = "ci-workflow-triggers-on-push-and-pr"
    description = "CI workflow triggers on push + pull_request (both branches)"

    def run(self) -> EvalResult:
        path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        text = path.read_text(encoding="utf-8")
        if "push:" not in text:
            return EvalResult(name=self.name, passed=False, detail="missing push trigger")
        if "pull_request:" not in text:
            return EvalResult(name=self.name, passed=False, detail="missing pull_request trigger")
        return EvalResult(name=self.name, passed=True, detail="push + pull_request wired")


class CIWorkflowRunsAudit(EvalCase):
    name = "ci-workflow-runs-audit"
    description = "CI workflow runs `loom audit` to track the 5-dimension score over time"

    def run(self) -> EvalResult:
        path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        text = path.read_text(encoding="utf-8")
        if "loom.cli audit" not in text:
            return EvalResult(name=self.name, passed=False, detail="loom audit not invoked")
        return EvalResult(name=self.name, passed=True, detail="audit wired")