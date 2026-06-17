"""Eval cases for telemetry sink configuration and behaviour."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from loop.agent.config import ConfigError, load_config
from loop.agent.trace import Trace
from loop.eval.runner import EvalCase, EvalResult


class TelemetryConfigParsesSinkCommand(EvalCase):
    name = "telemetry-config-parses-sink-command"
    description = "[telemetry] sink_command is parsed and stored"

    def run(self) -> EvalResult:
        wd = Path(tempfile.mkdtemp(prefix="loop-eval-telemetry-"))
        try:
            (wd / "harness.toml").write_text(
                '[telemetry]\nsink_command = "/usr/bin/true"\n',
                encoding="utf-8",
            )
            cfg = load_config(wd)
            if cfg.telemetry.sink_command != "/usr/bin/true":
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"got {cfg.telemetry.sink_command!r}",
                )
            return EvalResult(name=self.name, passed=True, detail="sink_command parsed")
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class TelemetryConfigDefaultNoSink(EvalCase):
    name = "telemetry-config-default-no-sink"
    description = "No [telemetry] section → sink_command is None"

    def run(self) -> EvalResult:
        wd = Path(tempfile.mkdtemp(prefix="loop-eval-telemetry-"))
        try:
            (wd / "harness.toml").write_text("", encoding="utf-8")
            cfg = load_config(wd)
            if cfg.telemetry.sink_command is not None:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"expected None, got {cfg.telemetry.sink_command!r}",
                )
            return EvalResult(name=self.name, passed=True, detail="sink_command is None")
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class TelemetryConfigRejectsNonStringSink(EvalCase):
    name = "telemetry-config-rejects-non-string-sink"
    description = "[telemetry] sink_command = 123 raises ConfigError"

    def run(self) -> EvalResult:
        wd = Path(tempfile.mkdtemp(prefix="loop-eval-telemetry-"))
        try:
            (wd / "harness.toml").write_text(
                "[telemetry]\nsink_command = 123\n",
                encoding="utf-8",
            )
            try:
                load_config(wd)
            except ConfigError as exc:
                msg = str(exc)
                if "sink_command" in msg or "telemetry" in msg:
                    return EvalResult(
                        name=self.name, passed=True,
                        detail=f"raised: {msg[:80]}",
                    )
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"unexpected message: {msg[:80]}",
                )
            except Exception as exc:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"wrong exception: {type(exc).__name__}: {exc}",
                )
            return EvalResult(
                name=self.name, passed=False,
                detail="ConfigError not raised",
            )
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class TelemetryTraceCallsSinkWithStdin(EvalCase):
    name = "telemetry-trace-calls-sink-with-stdin"
    description = "Trace.record() pipes JSON line to sink_command via stdin"

    def run(self) -> EvalResult:
        wd = Path(tempfile.mkdtemp(prefix="loop-eval-telemetry-"))
        marker = wd / "marker.txt"
        sink = wd / "sink.sh"
        sink.write_text(
            f"#!/bin/sh\ncat > {marker}\n",
            encoding="utf-8",
        )
        os.chmod(sink, 0o755)
        try:
            tr = Trace(wd, "test-session", sink_command=str(sink))
            tr.record("test_event", key="value")
            if not marker.exists():
                return EvalResult(
                    name=self.name, passed=False,
                    detail="marker file not created",
                )
            content = marker.read_text(encoding="utf-8")
            if '"event": "test_event"' not in content:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"marker missing event: {content[:120]}",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail=f"marker created: {content[:60]}",
            )
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class TelemetrySinkFailureDoesntBreakTrace(EvalCase):
    name = "telemetry-sink-failure-doesnt-break-trace"
    description = "sink_command points to nonexistent path → warning logged, trace still written"

    def run(self) -> EvalResult:
        wd = Path(tempfile.mkdtemp(prefix="loop-eval-telemetry-"))
        try:
            tr = Trace(wd, "test-session", sink_command="/nonexistent/sink")
            try:
                tr.record("test_event")
            except Exception as exc:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"record() raised: {type(exc).__name__}: {exc}",
                )
            if not tr.path.exists():
                return EvalResult(
                    name=self.name, passed=False,
                    detail="trace file not created",
                )
            content = tr.path.read_text(encoding="utf-8")
            if '"event": "test_event"' not in content:
                return EvalResult(
                    name=self.name, passed=False,
                    detail=f"trace missing event: {content[:120]}",
                )
            return EvalResult(
                name=self.name, passed=True,
                detail="trace persisted despite sink failure",
            )
        finally:
            shutil.rmtree(wd, ignore_errors=True)
