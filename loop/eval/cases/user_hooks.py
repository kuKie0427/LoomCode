"""Eval cases for user hook discovery and execution."""

from __future__ import annotations

import stat
import tempfile
from pathlib import Path

from loop.agent.user_hooks import HOOK_EVENTS, discover_user_hooks, make_shell_callback
from loop.eval.runner import EvalCase, EvalResult


class UserHooksDiscoveryEmptyWorkdir(EvalCase):
    name = "user-hooks-discovery-empty-workdir"
    description = "No .minicode/hooks/ dir — discover_user_hooks returns 4 empty lists"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = discover_user_hooks(Path(tmpdir))
            if set(result.keys()) != set(HOOK_EVENTS):
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Expected keys {set(HOOK_EVENTS)!r}, got {set(result.keys())!r}",
                )
            for event in HOOK_EVENTS:
                if result[event]:
                    return EvalResult(
                        name=self.name,
                        passed=False,
                        detail=f"Expected empty list for {event!r}, got {result[event]!r}",
                    )
            return EvalResult(
                name=self.name,
                passed=True,
                detail="All 4 events returned empty lists",
            )


class UserHooksDiscoveryFindsShScript(EvalCase):
    name = "user-hooks-discovery-finds-sh-script"
    description = "Place session_start.sh with chmod +x, gets discovered"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / ".minicode" / "hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            script = hooks_dir / "session_start.sh"
            script.write_text("#!/bin/sh\necho hello")
            script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            result = discover_user_hooks(Path(tmpdir))
            paths = result.get("session_start", [])
            if not paths:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail="session_start.sh not discovered",
                )
            if str(script) not in {str(p) for p in paths}:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Expected {script!s} in paths, got {paths!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail=f"Discovered session_start.sh at {script}",
            )


class UserHooksDiscoveryFindsPyScript(EvalCase):
    name = "user-hooks-discovery-finds-py-script"
    description = "Place pre_compact.py with chmod +x, gets discovered"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / ".minicode" / "hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            script = hooks_dir / "pre_compact.py"
            script.write_text("#!/usr/bin/env python3\nprint('compact')")
            script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            result = discover_user_hooks(Path(tmpdir))
            paths = result.get("pre_compact", [])
            if not paths:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail="pre_compact.py not discovered",
                )
            if str(script) not in {str(p) for p in paths}:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Expected {script!s} in paths, got {paths!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail=f"Discovered pre_compact.py at {script}",
            )


class UserHooksDiscoverySkipsNonExecutable(EvalCase):
    name = "user-hooks-discovery-skips-non-executable"
    description = "Place file with chmod 644 (non-executable), not discovered"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / ".minicode" / "hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            script = hooks_dir / "session_start.sh"
            script.write_text("#!/bin/sh\necho hello")
            script.chmod(0o644)

            result = discover_user_hooks(Path(tmpdir))
            paths = result.get("session_start", [])
            if paths:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Non-executable script was discovered: {paths!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail="Non-executable script correctly skipped",
            )


class UserHooksCallbackRunsScriptOnEvent(EvalCase):
    name = "user-hooks-callback-runs-script-on-event"
    description = "Place session_end.sh that writes to tmpdir, make_shell_callback runs and file exists"

    def run(self) -> EvalResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / ".minicode" / "hooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            marker = Path(tmpdir) / "marker.txt"
            script = hooks_dir / "session_end.sh"
            script.write_text(f"#!/bin/sh\necho 'session ended' > {marker}")
            script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            callback = make_shell_callback(script)
            callback("session_end", [], 5)

            if not marker.exists():
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Marker file not created at {marker}",
                )
            content = marker.read_text().strip()
            if "session ended" not in content:
                return EvalResult(
                    name=self.name,
                    passed=False,
                    detail=f"Marker content unexpected: {content!r}",
                )
            return EvalResult(
                name=self.name,
                passed=True,
                detail=f"Callback executed script, marker created with: {content!r}",
            )
