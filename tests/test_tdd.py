"""Tests for f-tdd-agent-mode-p4 (loom.agent.tdd)."""

from __future__ import annotations

from pathlib import Path

from loom.agent.tdd import (
    PytestRun,
    build_focused_prompt,
    is_test_file,
    run_pytest,
)


def test_is_test_file_top_level_tests_dir():
    assert is_test_file(Path("tests/test_foo.py"))


def test_is_test_file_test_prefix():
    assert is_test_file(Path("test_foo.py"))
    assert is_test_file(Path("foo_test.py"))


def test_is_test_file_nested_in_tests():
    assert is_test_file(Path("loom/agent/tests/test_x.py"))


def test_is_test_file_rejects_source_files():
    assert not is_test_file(Path("loom/agent/loop.py"))
    assert not is_test_file(Path("loom/eval/runner.py"))
    assert not is_test_file(Path("README.md"))


def test_is_test_file_rejects_test_named_dirs_without_py():
    assert not is_test_file(Path("tests/fixtures/data.json"))


def test_run_pytest_passing_test(tmp_path):
    test = tmp_path / "test_pass.py"
    test.write_text("def test_ok(): assert True\n")
    result = run_pytest(test, cwd=tmp_path)
    assert isinstance(result, PytestRun)
    assert result.passed
    assert result.exit_code == 0
    assert not result.timed_out


def test_run_pytest_failing_test(tmp_path):
    test = tmp_path / "test_fail.py"
    test.write_text("def test_boom(): assert False\n")
    result = run_pytest(test, cwd=tmp_path)
    assert not result.passed
    assert result.exit_code != 0
    assert "assert False" in result.stdout or "assert False" in result.stderr


def test_run_pytest_captures_tail(tmp_path):
    test = tmp_path / "test_fail.py"
    test.write_text("def test_boom(): assert False\n")
    result = run_pytest(test, cwd=tmp_path)
    assert isinstance(result.tail, str)
    assert len(result.tail) > 0


def test_run_pytest_timeout_handled(tmp_path):
    test = tmp_path / "test_sleep.py"
    test.write_text("import time\ndef test_slow(): time.sleep(5)\n")
    result = run_pytest(test, cwd=tmp_path, timeout=0.5)
    assert result.timed_out
    assert result.exit_code == -1


def test_run_pytest_reports_command(tmp_path):
    test = tmp_path / "test_x.py"
    test.write_text("def test_x(): pass\n")
    result = run_pytest(test, cwd=tmp_path)
    assert result.command[0] == "python"
    assert "-m" in result.command
    assert "pytest" in result.command


def test_build_focused_prompt_includes_test_path():
    prompt = build_focused_prompt("tests/test_x.py", "AssertionError")
    assert "tests/test_x.py" in prompt
    assert "AssertionError" in prompt
    assert "MINIMAL" in prompt


def test_build_focused_prompt_includes_reward_hacking_guard():
    prompt = build_focused_prompt("tests/test_x.py", "fail")
    assert "MUST NOT edit" in prompt
    assert "ANTI-REWARD-HACKING" in prompt


def test_build_focused_prompt_iteration_count():
    prompt = build_focused_prompt("tests/test_x.py", "fail", max_iterations=3)
    assert "3 times" in prompt


def test_tdd_module_public_api():
    from loom.agent import tdd
    for name in ("PytestRun", "is_test_file", "run_pytest", "build_focused_prompt"):
        assert hasattr(tdd, name), f"missing {name}"