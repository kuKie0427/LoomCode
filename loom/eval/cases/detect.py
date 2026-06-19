from loom.eval._util import make_workdir_with_file
from loom.eval.runner import EvalCase, EvalResult


class DetectPythonPyproject(EvalCase):
    name = "detect-python-pyproject"
    description = "Detect identifies a Python project via pyproject.toml"

    def run(self) -> EvalResult:
        wd = make_workdir_with_file("detect-py", "pyproject.toml", "[project]\nname = 'x'\n")
        from loom.detect import detect_project
        info = detect_project(wd)
        if info.stack != "python":
            return EvalResult(name=self.name, passed=False, detail=f"got stack={info.stack}")
        return EvalResult(name=self.name, passed=True, detail=f"stack={info.stack}")


class DetectPythonRequirementsTxt(EvalCase):
    name = "detect-python-requirements-txt"
    description = "Detect identifies Python via requirements.txt fallback"

    def run(self) -> EvalResult:
        wd = make_workdir_with_file("detect-pip", "requirements.txt", "flask\n")
        from loom.detect import detect_project
        info = detect_project(wd)
        if info.stack != "python":
            return EvalResult(name=self.name, passed=False, detail=f"got stack={info.stack}")
        return EvalResult(name=self.name, passed=True, detail=f"stack={info.stack}")


class DetectNodePackageJson(EvalCase):
    name = "detect-node-package-json"
    description = "Detect identifies Node.js via package.json"

    def run(self) -> EvalResult:
        wd = make_workdir_with_file("detect-node", "package.json", '{"name":"x","version":"0.0.1"}')
        from loom.detect import detect_project
        info = detect_project(wd)
        if info.stack not in ("node", "typescript", "typescript-react"):
            return EvalResult(name=self.name, passed=False, detail=f"got stack={info.stack}")
        return EvalResult(name=self.name, passed=True, detail=f"stack={info.stack}")


class DetectGo(EvalCase):
    name = "detect-go"
    description = "Detect identifies Go via go.mod"

    def run(self) -> EvalResult:
        wd = make_workdir_with_file("detect-go", "go.mod", "module x\n")
        from loom.detect import detect_project
        info = detect_project(wd)
        if info.stack != "go":
            return EvalResult(name=self.name, passed=False, detail=f"got stack={info.stack}")
        return EvalResult(name=self.name, passed=True, detail=f"stack={info.stack}")


class DetectRust(EvalCase):
    name = "detect-rust"
    description = "Detect identifies Rust via Cargo.toml"

    def run(self) -> EvalResult:
        wd = make_workdir_with_file("detect-rust", "Cargo.toml", '[package]\nname = "x"\n')
        from loom.detect import detect_project
        info = detect_project(wd)
        if info.stack != "rust":
            return EvalResult(name=self.name, passed=False, detail=f"got stack={info.stack}")
        return EvalResult(name=self.name, passed=True, detail=f"stack={info.stack}")


class DetectMaven(EvalCase):
    name = "detect-maven"
    description = "Detect identifies Java/Maven via pom.xml"

    def run(self) -> EvalResult:
        wd = make_workdir_with_file("detect-mvn", "pom.xml", "<project/>")
        from loom.detect import detect_project
        info = detect_project(wd)
        if info.stack != "java-maven":
            return EvalResult(name=self.name, passed=False, detail=f"got stack={info.stack}")
        return EvalResult(name=self.name, passed=True, detail=f"stack={info.stack}")


class DetectEmptyIsGeneric(EvalCase):
    name = "detect-empty-is-generic"
    description = "Detect falls back to generic for empty directory"

    def run(self) -> EvalResult:
        from loom.eval._util import make_empty_workdir
        wd = make_empty_workdir("detect-empty")
        from loom.detect import detect_project
        info = detect_project(wd)
        if info.stack != "generic":
            return EvalResult(name=self.name, passed=False, detail=f"got stack={info.stack}")
        return EvalResult(name=self.name, passed=True, detail=f"stack={info.stack}")


