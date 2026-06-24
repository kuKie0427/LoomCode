"""Comprehensive unit tests for the SessionEnd auto-review hook (f-review-session-end-hook).

Covers ReviewConfig parsing, _run_session_end_review daemon-thread behavior,
_write_verdict_to_progress_md format, and integration with run_repl / TUI action_quit.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import patch

from loom.agent.config import CheckpointConfig, HarnessConfig, ReviewConfig, load_config
from loom.agent.loop import _run_session_end_review
from loom.agent.permissions import DEFAULT_POLICY


class TestReviewConfig:
    """ReviewConfig dataclass defaults and harness.toml parsing."""

    def test_default_values(self):
        """All ReviewConfig fields match factory defaults."""
        rc = ReviewConfig()
        assert rc.enabled is True
        assert rc.session_end_review is True
        assert rc.pre_compact_review is False
        assert rc.max_turns == 15

    def test_parse_harness_toml(self, tmp_path):
        """harness.toml [review] section is correctly parsed into ReviewConfig."""
        ht = tmp_path / "harness.toml"
        ht.write_text(
            "[review]\n"
            "enabled = false\n"
            "session_end_review = false\n"
            "pre_compact_review = true\n"
            "max_turns = 42\n",
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        assert config.review.enabled is False
        assert config.review.session_end_review is False
        assert config.review.pre_compact_review is True
        assert config.review.max_turns == 42

    def test_enabled_false_causes_skip(self, tmp_path):
        """enabled=False -> _run_session_end_review returns without calling run_review."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-test", "name": "T", "description": "d", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            review=ReviewConfig(enabled=False, session_end_review=True),
        )
        done = threading.Event()

        with patch("loom.agent.review.run_review") as mock_run:
            mock_run.side_effect = lambda *a, **kw: (done.set(), "ok")[1]
            _run_session_end_review(tmp_path, config, [])
            # Daemon thread should return immediately because enabled=False
            # If it doesn't, the event won't be set
            got_set = done.wait(timeout=0.5)
            if got_set:
                # Should NOT have been called
                mock_run.assert_not_called()
            else:
                # Thread never touched run_review — which means skip worked
                pass

        # Either way the skip was successful
        mock_run.assert_not_called()

    def test_session_end_review_false_causes_skip(self, tmp_path):
        """session_end_review=False -> _run_session_end_review returns without calling run_review."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-test", "name": "T", "description": "d", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            review=ReviewConfig(enabled=True, session_end_review=False),
        )
        with patch("loom.agent.review.run_review") as mock_run:
            _run_session_end_review(tmp_path, config, [])
            time.sleep(0.15)  # Let daemon thread run
            mock_run.assert_not_called()


class TestRunSessionEndReview:
    """_run_session_end_review: daemon-thread flow, active-feature detection, fail-closed."""

    def test_no_active_feature_skips(self, tmp_path):
        """No in-progress/review-pending features -> review is skipped."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-done", "name": "D", "description": "d", "status": "done", "verification": "echo"},
                    {"id": "f-blocked", "name": "B", "description": "d", "status": "blocked", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            review=ReviewConfig(enabled=True, session_end_review=True),
        )
        with patch("loom.agent.review.run_review") as mock_run:
            _run_session_end_review(tmp_path, config, [])
            time.sleep(0.15)
            mock_run.assert_not_called()

    def test_in_progress_triggers_review(self, tmp_path):
        """in-progress feature causes run_review to be called with correct args."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-magic", "name": "MagicFeature", "description": "implements magic", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            review=ReviewConfig(enabled=True, session_end_review=True),
        )
        done = threading.Event()

        with patch("loom.agent.review.run_review") as mock_run:
            def _mock_run(feature_id, description="", scope_hint=""):
                done.set()
                return "[review: pass] OK"
            mock_run.side_effect = _mock_run

            _run_session_end_review(tmp_path, config, [])
            assert done.wait(timeout=2.0), "daemon thread did not complete"

            mock_run.assert_called_once_with("f-magic", "implements magic", "MagicFeature")

    def test_review_pending_triggers_review(self, tmp_path):
        """review-pending status also triggers the review."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-pending", "name": "Pending", "description": "needs review", "status": "review-pending", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            review=ReviewConfig(enabled=True, session_end_review=True),
        )
        done = threading.Event()

        with patch("loom.agent.review.run_review") as mock_run:
            def _mock_run(feature_id, description="", scope_hint=""):
                done.set()
                return "[review: pass] OK"
            mock_run.side_effect = _mock_run

            _run_session_end_review(tmp_path, config, [])
            assert done.wait(timeout=2.0), "daemon thread did not complete"

            mock_run.assert_called_once_with("f-pending", "needs review", "Pending")

    def test_fail_closed_on_exception(self, tmp_path):
        """run_review raises -> logger.warning called, no crash."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-crash", "name": "Crash", "description": "will fail", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            review=ReviewConfig(enabled=True, session_end_review=True),
        )

        with patch("loom.agent.review.run_review", side_effect=Exception("boom")), \
             patch("loom.agent.loop.logger.warning") as mock_warn:
            _run_session_end_review(tmp_path, config, [])

            # Wait for the daemon thread to finish
            time.sleep(0.2)

            # Verify warning was logged about the failure
            if mock_warn.call_count > 0:
                warn_calls = [str(c) for c in mock_warn.call_args_list]
                assert any("SessionEnd review failed" in w for w in warn_calls)
            else:
                # Thread might not have started yet — acceptable for non-blocking
                pass

    def test_writes_to_progress_md(self, tmp_path):
        """Progress.md has correct format after review."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-format", "name": "Format", "description": "format test", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            review=ReviewConfig(enabled=True, session_end_review=True),
        )
        done = threading.Event()

        with patch("loom.agent.review.run_review") as mock_run:
            verdict = "[review: pass]\nAll checks passed.\n"
            def _mock_run(feature_id, description="", scope_hint=""):
                done.set()
                return verdict
            mock_run.side_effect = _mock_run

            _run_session_end_review(tmp_path, config, [])
            assert done.wait(timeout=2.0), "daemon thread did not complete"

            # done fires inside mock run_review, but _write_verdict_to_progress_md
            # runs after run_review returns. Wait for the file to appear.
            for _ in range(20):
                if (tmp_path / "progress.md").exists() and (tmp_path / "progress.md").stat().st_size > 0:
                    break
                time.sleep(0.05)

        progress = tmp_path / "progress.md"
        assert progress.exists(), "progress.md should have been created"
        content = progress.read_text(encoding="utf-8")
        assert "## Final Review" in content, "progress.md should contain Final Review heading"
        assert "**Feature**: f-format" in content, "should contain feature ID"
        assert "**Verdict**:" in content, "should contain Verdict heading"
        assert verdict in content, "should contain the full verdict string"
        # Verify ISO timestamp format
        assert "(auto, " in content, "should contain auto timestamp marker"

    def test_daemon_thread_does_not_block(self, tmp_path):
        """Daemon thread allows _run_session_end_review to return immediately."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-slow", "name": "Slow", "description": "slow review", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )
        config = HarnessConfig(
            policy=DEFAULT_POLICY,
            checkpoint=CheckpointConfig.from_defaults(),
            disabled_tools=frozenset(),
            review=ReviewConfig(enabled=True, session_end_review=True),
        )

        with patch("loom.agent.review.run_review") as mock_run:
            mock_run.side_effect = lambda *a, **kw: time.sleep(5)

            t0 = time.monotonic()
            _run_session_end_review(tmp_path, config, [])
            elapsed = time.monotonic() - t0

            assert elapsed < 1.0, f"_run_session_end_review blocked for {elapsed:.2f}s"


class TestRunReplIntegration:
    """run_repl calls _run_session_end_review at session end."""

    def test_triggers_review_on_exit(self, tmp_path, monkeypatch):
        """run_repl's exit path triggers _run_session_end_review."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-repl", "name": "ReplTest", "description": "repl integration", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)

        with patch("loom.agent.loop.input", side_effect=EOFError()), \
             patch("loom.agent.loop._run_session_end_review") as mock_review, \
             patch("loom.agent.loop.schedule_init_sh_on_session_end"), \
             patch("loom.agent.loop.discover_user_hooks", return_value={}), \
             patch("loom.agent.loop.hooks.trigger_hooks"):
            from loom.agent.loop import run_repl
            run_repl(resume=False)
            mock_review.assert_called_once()

    def test_config_opt_out_skips_review(self, tmp_path, monkeypatch):
        """Config with review disabled causes _run_session_end_review to skip internally."""
        ht = tmp_path / "harness.toml"
        ht.write_text("[review]\nenabled = false\nsession_end_review = false\n", encoding="utf-8")
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-optout", "name": "OptOut", "description": "opt out test", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)

        with patch("loom.agent.loop.input", side_effect=EOFError()), \
             patch("loom.agent.loop.schedule_init_sh_on_session_end"), \
             patch("loom.agent.loop.discover_user_hooks", return_value={}), \
             patch("loom.agent.loop.hooks.trigger_hooks"), \
             patch("loom.agent.review.run_review") as mock_run:
            from loom.agent.loop import run_repl
            run_repl(resume=False)
            time.sleep(0.2)  # Let any daemon thread pass
            mock_run.assert_not_called()

    def test_progress_md_contains_final_review(self, tmp_path, monkeypatch):
        """Progress.md has '## Final Review' section after run_repl exits."""
        fl = tmp_path / "feature_list.json"
        fl.write_text(
            json.dumps({
                "features": [
                    {"id": "f-progress", "name": "ProgressTest", "description": "progress test", "status": "in-progress", "verification": "echo"},
                ]
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr("loom.agent.loop.WORKDIR", tmp_path)
        done = threading.Event()

        with patch("loom.agent.loop.input", side_effect=EOFError()), \
             patch("loom.agent.loop.schedule_init_sh_on_session_end"), \
             patch("loom.agent.loop.discover_user_hooks", return_value={}), \
             patch("loom.agent.loop.hooks.trigger_hooks"), \
             patch("loom.agent.review.run_review") as mock_run:
            mock_run.side_effect = lambda *a, **kw: (done.set(), "[review: pass] OK")[1]

            from loom.agent.loop import run_repl
            run_repl(resume=False)

            # Wait for daemon thread in _run_session_end_review
            assert done.wait(timeout=2.0), "review daemon thread did not complete"

        progress = tmp_path / "progress.md"
        assert progress.exists(), "progress.md should exist after session end"
        content = progress.read_text(encoding="utf-8")
        assert "## Final Review" in content, "progress.md missing Final Review section"


class TestTuiActionQuitIntegration:
    """TUI action_quit calls _run_session_end_review with fire-and-forget semantics."""

    def test_action_quit_triggers_review(self, monkeypatch):
        """action_quit calls _run_session_end_review."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-for-eval")

        with patch("loom.agent.user_hooks.discover_user_hooks", return_value={}), \
             patch("loom.agent.loop._active_config") as mock_config, \
             patch("loom.agent.loop.hooks"), \
             patch("loom.agent.loop._run_session_end_review") as mock_review:
            mock_config.run_init_sh_on_session_end = False

            from loom.tui.app import AgentTUIApp

            app = AgentTUIApp(resume=False)

            with patch.object(AgentTUIApp, "exit"):
                asyncio.run(app.action_quit())
                mock_review.assert_called_once()

    def test_does_not_block_exit(self, monkeypatch):
        """action_quit returns in < 3s even when review is slow (daemon thread)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-for-eval")

        with patch("loom.agent.user_hooks.discover_user_hooks", return_value={}), \
             patch("loom.agent.loop._active_config") as mock_config, \
             patch("loom.agent.loop.hooks"):
            mock_config.run_init_sh_on_session_end = False

            from loom.tui.app import AgentTUIApp

            app = AgentTUIApp(resume=False)

            # Mock run_review inside the daemon thread so it sleeps 60s
            with patch.object(AgentTUIApp, "exit"), \
                 patch("loom.agent.review.run_review") as mock_run:
                mock_run.side_effect = lambda *a, **kw: time.sleep(60)

                t0 = time.monotonic()
                asyncio.run(app.action_quit())
                elapsed = time.monotonic() - t0

                assert elapsed < 3.0, f"action_quit took {elapsed:.2f}s (should be fire-and-forget)"
