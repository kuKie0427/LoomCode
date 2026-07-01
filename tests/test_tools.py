"""Integration tests for all tool functions in main.py."""

import subprocess

import pytest

import loom.agent.tools as main

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
    """run_read returns the file's lines numbered 1..N (cat -n style, no offset)."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    file_path = temp_workdir / "read_test.txt"
    file_path.write_text("line1\nline2\nline3")

    result = main.run_read("read_test.txt")
    assert result == "1: line1\n2: line2\n3: line3"


def test_run_read_missing_file(monkeypatch, temp_workdir):
    """run_read returns an Error string for a nonexistent file."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    result = main.run_read("nonexistent.txt")
    assert "Error:" in result


def test_run_read_with_limit(monkeypatch, temp_workdir):
    """run_read with limit=3 on 10-line file returns first 3 numbered lines + '... (7 more lines)' footer."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    lines = [f"line{i}" for i in range(10)]
    file_path = temp_workdir / "many_lines.txt"
    file_path.write_text("\n".join(lines))

    result = main.run_read("many_lines.txt", limit=3)
    result_lines = result.splitlines()
    assert result_lines == [" 1: line0", " 2: line1", " 3: line2", "... (7 more lines)"]


def test_run_read_path_escape(monkeypatch, temp_workdir):
    """safe_path raises ValueError for ../ escapes; run_read wraps it as Error."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    with pytest.raises(ValueError):
        main.safe_path("../outside")

    result = main.run_read("../outside")
    assert "Error:" in result


def test_run_read_with_offset(monkeypatch, temp_workdir):
    """offset is 1-indexed: offset=4 on 10-line file returns lines 4..10 numbered 4..10."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    lines = [f"line{i}" for i in range(10)]
    file_path = temp_workdir / "offset_test.txt"
    file_path.write_text("\n".join(lines))

    result = main.run_read("offset_test.txt", offset=4)
    result_lines = result.splitlines()
    assert result_lines == [f"{i:>2}: line{i - 1}" for i in range(4, 11)]


def test_run_read_offset_and_limit(monkeypatch, temp_workdir):
    """offset+limit returns a 1-indexed window: offset=3, limit=3 on 10-line file → lines 3..5 + footer."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    lines = [f"line{i}" for i in range(10)]
    file_path = temp_workdir / "offset_limit_test.txt"
    file_path.write_text("\n".join(lines))

    result = main.run_read("offset_limit_test.txt", limit=3, offset=3)
    result_lines = result.splitlines()
    assert result_lines == [" 3: line2", " 4: line3", " 5: line4", "... (5 more lines)"]


def test_run_read_offset_at_end(monkeypatch, temp_workdir):
    """offset > total returns 'past end' marker (no IndexError, no exception)."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    lines = [f"line{i}" for i in range(5)]
    file_path = temp_workdir / "short.txt"
    file_path.write_text("\n".join(lines))

    result = main.run_read("short.txt", offset=10)
    assert "past end" in result
    assert "5 lines" in result


def test_run_read_offset_at_total(monkeypatch, temp_workdir):
    """offset == total returns the last line (boundary, not past-end)."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    lines = [f"line{i}" for i in range(5)]
    file_path = temp_workdir / "boundary.txt"
    file_path.write_text("\n".join(lines))

    result = main.run_read("boundary.txt", offset=5)
    assert result == "5: line4"


def test_run_read_negative_offset(monkeypatch, temp_workdir):
    """run_read with a negative `offset` returns an explicit Error string (does not raise)."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    file_path = temp_workdir / "x.txt"
    file_path.write_text("only line")

    result = main.run_read("x.txt", offset=-1)
    assert result.startswith("Error:")
    assert "offset must be >= 0" in result


def test_run_read_line_number_alignment(monkeypatch, temp_workdir):
    """Line-number column is right-aligned to len(str(total)) so columns line up across windows."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    file_path = temp_workdir / "wide.txt"
    file_path.write_text("\n".join(f"row{i}" for i in range(150)))

    result = main.run_read("wide.txt", limit=5, offset=10)
    lines = result.splitlines()
    widths = {len(line.split(": ", 1)[0]) for line in lines if ": " in line}
    assert widths == {3}, f"expected width=3 for 150-line file, got {widths}"


def test_run_read_offset_passes_through_as_first_line_number(monkeypatch, temp_workdir):
    """The first line's number in the output equals the offset (1-indexed, cat -n invariant)."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    file_path = temp_workdir / "page.txt"
    file_path.write_text("\n".join(f"L{i}" for i in range(50)))

    result = main.run_read("page.txt", limit=3, offset=42)
    first_line = result.splitlines()[0]
    assert first_line.startswith("42: "), f"first line should be '42: ...', got {first_line!r}"


def test_run_read_empty_file(monkeypatch, temp_workdir):
    """Empty file returns '(no content in window)' without crashing."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    file_path = temp_workdir / "empty.txt"
    file_path.write_text("")

    result = main.run_read("empty.txt")
    assert "no content" in result.lower()


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
    """run_edit returns an error when old_text is not found in the file (and too short for fuzzy)."""
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)

    file_path = temp_workdir / "edit_test.txt"
    file_path.write_text("sample content")

    result = main.run_edit("edit_test.txt", "nonexistent", "replacement")
    assert "Error:" in result
    assert "not_found" in result


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


def test_run_task_does_not_fire_subagent_callback(mocker, monkeypatch, temp_workdir):
    monkeypatch.setattr(main, "WORKDIR", temp_workdir)
    mocker.patch.object(main, "spawn_subagent", return_value="subagent result")

    captured: list[tuple] = []
    callbacks = {
        "on_subagent_start": lambda sid, desc, agent_name="织针": captured.append(("start", sid)),
        "on_subagent_end": lambda sid, elapsed, state: captured.append(("end", sid, state)),
    }
    loop_mod = __import__("loom.agent.loop", fromlist=["set_active_callbacks", "clear_active_callbacks"])
    loop_mod.set_active_callbacks(callbacks)
    try:
        result = main.run_task("do the thing")
        assert result == "subagent result"
        assert captured == [], (
            f"run_task should NOT fire subagent callbacks (moved to _run_tool_block); got {captured}"
        )
    finally:
        loop_mod.clear_active_callbacks()
