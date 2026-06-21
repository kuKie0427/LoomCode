"""Tests for f-repomap-p4."""

from __future__ import annotations

from pathlib import Path

from loom.agent.repomap import _iter_python_files, _symbols_in_file, build_repomap


def test_iter_python_files_skips_dotfiles_and_pycache(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".hidden.py").write_text("x = 1\n")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "c.py").write_text("x = 1\n")
    (tmp_path / "b" / "__pycache__").mkdir()
    (tmp_path / "b" / "__pycache__" / "d.pyc").write_text("x")
    files = list(_iter_python_files(tmp_path))
    names = {p.name for p in files}
    assert "a.py" in names
    assert "c.py" in names
    assert ".hidden.py" not in names
    assert "d.pyc" not in names


def test_symbols_in_file_extracts_top_level_functions():
    src = '''
def public_fn():
    pass

def _private_fn():
    pass

class MyClass:
    def method_one(self):
        pass
    def _private_method(self):
        pass

class OtherClass:
    pass
'''
    path = Path("/tmp/test_repomap_unit.py")
    path.write_text(src)
    try:
        symbols = _symbols_in_file(path)
    finally:
        path.unlink()
    assert "public_fn" in symbols
    assert "MyClass" in symbols
    assert "OtherClass" in symbols
    assert "MyClass.method_one" in symbols
    assert "_private_fn" not in symbols
    assert "_private_method" not in symbols


def test_symbols_in_file_handles_syntax_error_gracefully():
    path = Path("/tmp/test_repomap_bad.py")
    path.write_text("def broken(:\n")
    try:
        symbols = _symbols_in_file(path)
    finally:
        path.unlink()
    assert symbols == []


def test_symbols_in_file_handles_missing_file(tmp_path):
    nonexistent = tmp_path / "does_not_exist.py"
    assert _symbols_in_file(nonexistent) == []


def test_build_repomap_returns_empty_for_empty_workspace(tmp_path):
    assert build_repomap(tmp_path) == ""


def test_build_repomap_includes_files_with_symbols(tmp_path):
    (tmp_path / "mod_a.py").write_text("def alpha():\n    pass\n")
    (tmp_path / "mod_b.py").write_text("def beta():\n    pass\nclass Beta:\n    pass\n")
    out = build_repomap(tmp_path)
    assert "mod_a.py" in out
    assert "alpha" in out
    assert "mod_b.py" in out
    assert "beta" in out
    assert "Beta" in out


def test_build_repomap_skips_empty_files(tmp_path):
    (tmp_path / "empty.py").write_text("")
    (tmp_path / "has_content.py").write_text("def f():\n    pass\n")
    out = build_repomap(tmp_path)
    assert "empty.py" not in out
    assert "has_content.py" in out


def test_build_repomap_respects_max_files(tmp_path):
    for i in range(5):
        (tmp_path / f"m{i}.py").write_text(f"def fn_{i}():\n    pass\n" * 10)
    out = build_repomap(tmp_path, max_files=2)
    lines = [line for line in out.splitlines() if ".py:" in line]
    assert len(lines) == 2


def test_build_repomap_respects_max_tokens(tmp_path):
    for i in range(10):
        (tmp_path / f"m{i}.py").write_text(f"def fn_{i}():\n    pass\n" * 50)
    out = build_repomap(tmp_path, max_tokens=10, max_files=100)
    assert len(out) < 200


def test_build_repomap_sorts_by_symbol_count_descending(tmp_path):
    (tmp_path / "small.py").write_text("def a():\n    pass\n")
    (tmp_path / "big.py").write_text("\n".join(f"def fn_{i}():\n    pass\n" for i in range(20)))
    out = build_repomap(tmp_path)
    big_pos = out.find("big.py")
    small_pos = out.find("small.py")
    assert big_pos < small_pos


def test_build_repomap_includes_class_methods(tmp_path):
    (tmp_path / "mod.py").write_text('''
class Service:
    def start(self):
        pass
    def stop(self):
        pass
''')
    out = build_repomap(tmp_path)
    assert "Service" in out
    assert "Service.start" in out
    assert "Service.stop" in out


def test_build_repomap_loop_wired():
    """Static check: system_prompt.py imports + uses build_repomap."""
    from pathlib import Path
    src = Path("loom/agent/system_prompt.py").read_text()
    assert "build_repomap" in src
    assert "from loom.agent.repomap import build_repomap" in src
