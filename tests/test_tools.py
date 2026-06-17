"""Integration tests for all tool functions in main.py."""

import subprocess

import pytest

import loop.agent.tools as main

# ── run_bash ──────────────────────────────────────────────────────────────

def test_run_bash_safe_command(monkeypatch, temp_workdir):
    """run_bash returns the output of a safe shell command (stripped)."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    result = main.run_bash("echo hello")
    assert result == "hello"


def test_run_bash_dangerous_blocked(monkeypatch, temp_workdir):
    """run_bash blocks commands matching the policy deny_patterns and returns an error."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    for cmd in ("rm -rf /", "sudo rm file", "shutdown now", "reboot", "dd if=/dev/zero of=/tmp/x"):
        result = main.run_bash(cmd)
        assert "Dangerous command blocked" in result, f"expected block for {cmd!r}, got {result!r}"


def test_run_bash_timeout(monkeypatch):
    """run_bash returns a timeout error when subprocess times out."""

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="sleep 5", timeout=0.1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = main.run_bash("sleep 5")
    assert "Timeout" in result


def test_run_bash_missing_command(monkeypatch, temp_workdir):
    """run_bash returns an Error string when the command binary is not found."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("No such file: 'nonexistent_cmd'")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = main.run_bash("nonexistent_cmd")
    assert "Error:" in result


# ── run_read ──────────────────────────────────────────────────────────────

def test_run_read_existing_file(monkeypatch, temp_workdir):
    """run_read returns the full content of an existing file."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    file_path = temp_workdir / "read_test.txt"
    file_path.write_text("line1\nline2\nline3")

    result = main.run_read("read_test.txt")
    assert result == "line1\nline2\nline3"


def test_run_read_missing_file(monkeypatch, temp_workdir):
    """run_read returns an Error string for a nonexistent file."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    result = main.run_read("nonexistent.txt")
    assert "Error:" in result


def test_run_read_with_limit(monkeypatch, temp_workdir):
    """run_read respects the limit parameter and adds a truncation hint."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    lines = [f"line{i}" for i in range(10)]
    file_path = temp_workdir / "many_lines.txt"
    file_path.write_text("\n".join(lines))

    result = main.run_read("many_lines.txt", limit=3)
    result_lines = result.splitlines()
    # limit lines + 1 truncation hint line (or fewer if file is smaller)
    assert len(result_lines) <= 3 + 1
    assert "more lines)" in result


def test_run_read_path_escape(monkeypatch, temp_workdir):
    """safe_path raises ValueError for ../ escapes; run_read wraps it as Error."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    with pytest.raises(ValueError):
        main.safe_path("../outside")

    result = main.run_read("../outside")
    assert "Error:" in result


# ── run_write ─────────────────────────────────────────────────────────────

def test_run_write_creates_file(monkeypatch, temp_workdir):
    """run_write creates a file with the given content inside the workspace."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    result = main.run_write("write_test.txt", "hello world")
    assert "Wrote" in result

    written = (temp_workdir / "write_test.txt").read_text()
    assert written == "hello world"


def test_run_write_creates_parent_dirs(monkeypatch, temp_workdir):
    """run_write creates parent directories automatically for nested paths."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    result = main.run_write("subdir/nested/file.txt", "nested content")
    assert "Wrote" in result

    file_path = temp_workdir / "subdir" / "nested" / "file.txt"
    assert file_path.exists()
    assert file_path.read_text() == "nested content"


def test_run_write_path_escape(monkeypatch, temp_workdir):
    """run_write returns an Error for paths that escape the workspace."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    result = main.run_write("../outside", "escape")
    assert "Error:" in result


# ── run_edit ──────────────────────────────────────────────────────────────

def test_run_edit_replaces_text(monkeypatch, temp_workdir):
    """run_edit replaces the first occurrence of old_text with new_text."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    file_path = temp_workdir / "edit_test.txt"
    file_path.write_text("before old after")

    result = main.run_edit("edit_test.txt", "old", "new")
    assert "Edited" in result

    updated = file_path.read_text()
    assert updated == "before new after"


def test_run_edit_text_not_found(monkeypatch, temp_workdir):
    """run_edit returns an error when old_text is not found in the file."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    file_path = temp_workdir / "edit_test.txt"
    file_path.write_text("sample content")

    result = main.run_edit("edit_test.txt", "nonexistent", "replacement")
    assert "Error: text not found" in result


def test_run_edit_path_escape(monkeypatch, temp_workdir):
    """run_edit returns an Error for paths that escape the workspace."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    result = main.run_edit("../outside", "old", "new")
    assert "Error:" in result


# ── run_glob ──────────────────────────────────────────────────────────────

def test_run_glob_matches_files(monkeypatch, temp_workdir):
    """run_glob returns a newline-separated list of matching filenames."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    (temp_workdir / "alpha.txt").write_text("a")
    (temp_workdir / "beta.txt").write_text("b")
    (temp_workdir / "gamma.log").write_text("c")

    result = main.run_glob("*.txt")
    assert "alpha.txt" in result
    assert "beta.txt" in result
    assert "gamma.log" not in result


def test_run_glob_no_matches(monkeypatch, temp_workdir):
    """run_glob returns '(no matches)' when no files match the pattern."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    result = main.run_glob("nonexistent*.xyz")
    assert result == "(no matches)"


# ── run_todo_write ────────────────────────────────────────────────────────

def test_run_todo_write_valid(monkeypatch, temp_workdir):
    """run_todo_write accepts a valid todo list and returns an updated count."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    todos = [
        {"content": "task one", "status": "pending"},
        {"content": "task two", "status": "in_progress"},
        {"content": "task three", "status": "completed"},
    ]
    result = main.run_todo_write(todos)
    assert "Updated 3 tasks" in result


def test_run_todo_write_missing_field(monkeypatch, temp_workdir):
    """run_todo_write returns an error when a todo is missing 'content' or 'status'."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    result = main.run_todo_write([{"content": "task only"}])
    assert "Error: todos[0] missing 'content' or 'status'" in result


def test_run_todo_write_invalid_status(monkeypatch, temp_workdir):
    """run_todo_write returns an error when a todo has an invalid status."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    result = main.run_todo_write(
        [{"content": "bad task", "status": "invalid_status"}]
    )
    assert "Error: todos[0] has invalid status" in result
