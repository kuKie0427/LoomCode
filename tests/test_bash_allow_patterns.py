"""P2-1: bash 三态权限模型测试（deny → allow → rules）。

验证要点：
1. deny 优先于 allow（bypass-immune）——allow 无法 override deny
2. allow 命中时 silent allow（不触发 _ask_user）
3. allow 未命中时走原有 rules 路径（向后兼容）
4. wildcard 匹配语义（`*` 任意字符，`?` 单字符，整串匹配）
5. harness.toml 配置解析（[permissions] allow_patterns）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from loom.agent.config import ConfigError, _parse_policy_section
from loom.agent.hooks import Hooks
from loom.agent.permissions import (
    DEFAULT_POLICY,
    PermissionPolicy,
    _wildcard_to_regex,
)


# ---------------------------------------------------------------------------
# MockBlock helper — mirrors the tool_use block shape consumed by Hooks
# ---------------------------------------------------------------------------
class MockBlock:
    def __init__(self, name: str, input_dict: dict):
        self.name = name
        self.input = input_dict


# ---------------------------------------------------------------------------
# 1. _wildcard_to_regex 单元测试
# ---------------------------------------------------------------------------
class TestWildcardToRegex:
    def test_star_matches_any_chars(self):
        """`*` matches any sequence including empty."""
        r = _wildcard_to_regex("python -c *")
        assert r.match("python -c 'assert 1+1==2'")
        assert r.match("python -c ")  # empty arg
        assert r.match("python -c foo bar baz")

    def test_question_matches_one_char(self):
        """`?` matches exactly one character."""
        r = _wildcard_to_regex("pytest -x?")
        assert r.match("pytest -xv")
        assert not r.match("pytest -x")     # missing char
        assert not r.match("pytest -xvv")   # too many chars

    def test_anchored_full_string(self):
        """Pattern is anchored — no partial match in the middle."""
        r = _wildcard_to_regex("python -c *")
        # `echo python -c foo` should NOT match (prefix anchoring)
        assert not r.match("echo python -c foo")

    def test_regex_metacharacters_escaped(self):
        """Regex metacharacters in pattern are escaped, not interpreted."""
        # `.` should match literal dot, not any char
        r = _wildcard_to_regex("pytest test_foo.py")
        assert r.match("pytest test_foo.py")
        assert not r.match("pytest test_fooXpy")  # . should not match X

    def test_no_wildcard_means_exact(self):
        """Pattern without `*` or `?` requires exact match."""
        r = _wildcard_to_regex("git status")
        assert r.match("git status")
        assert not r.match("git status --short")  # extra chars


# ---------------------------------------------------------------------------
# 2. PermissionPolicy.matches_allow 单元测试
# ---------------------------------------------------------------------------
class TestMatchesAllow:
    def test_empty_allow_patterns_returns_none(self):
        """Default policy (no allow_patterns) never matches."""
        p = PermissionPolicy(deny_patterns=(), allow_patterns=())
        assert p.matches_allow("python -c 'foo'") is None

    def test_first_matching_pattern_returned(self):
        """Returns the first matching pattern when multiple match."""
        p = PermissionPolicy(
            deny_patterns=(),
            allow_patterns=("python -c *", "python3 -c *"),
        )
        assert p.matches_allow("python -c 'assert'") == "python -c *"

    def test_no_match_returns_none(self):
        """Returns None when no pattern matches."""
        p = PermissionPolicy(
            deny_patterns=(),
            allow_patterns=("pytest *",),
        )
        assert p.matches_allow("rm -rf /") is None


# ---------------------------------------------------------------------------
# 3. Hooks.check_permission_hook 三态集成测试
# ---------------------------------------------------------------------------
@pytest.fixture
def hooks_with_allow():
    """Hooks instance with allow_patterns for python -c and pytest.

    Removes `python -c ` and `python3 -c ` from deny_patterns so the
    allow_patterns can actually fire (otherwise deny short-circuits).
    Keeps `rm -rf /` and other dangerous denies to test bypass-immunity.
    """
    h = Hooks()
    # Strip the `-c` deny entries — we're explicitly allowing them via
    # allow_patterns in this fixture. Keep all other deny patterns.
    filtered_deny = tuple(
        p for p in DEFAULT_POLICY.deny_patterns
        if p not in ("python -c ", "python3 -c ", "perl -e ", "ruby -e ", "bash -c ")
    )
    h.policy = PermissionPolicy(
        deny_patterns=filtered_deny,
        allow_patterns=("python -c *", "python3 -c *", "pytest *"),
        rules=DEFAULT_POLICY.rules,
    )
    return h


class TestThreeStateBashPermission:
    def test_allow_pattern_silent_allows(self, hooks_with_allow):
        """allow_patterns match → returns None (allow), no _ask_user call."""
        # Spy on _ask_user to ensure it's NOT called
        hooks_with_allow._ask_user = MagicMock(return_value="deny")
        block = MockBlock("bash", {"command": "python -c 'assert 1+1==2'"})
        result = hooks_with_allow.check_permission_hook("PreToolUse", block)
        assert result is None  # allowed
        hooks_with_allow._ask_user.assert_not_called()  # silent

    def test_deny_overrides_allow(self, hooks_with_allow):
        """deny_patterns match → blocked even if allow_patterns also match.

        This is the bypass-immune guarantee: `python -c 'import os; os.system("rm -rf /")'`
        matches `python -c *` (allow) but also contains `rm -rf /` (deny).
        deny MUST win.
        """
        hooks_with_allow._ask_user = MagicMock(return_value="allow")
        # `rm -rf /` is in DEFAULT_POLICY.deny_patterns
        cmd = "python -c 'import os; os.system(\"rm -rf /\")'"
        block = MockBlock("bash", {"command": cmd})
        result = hooks_with_allow.check_permission_hook("PreToolUse", block)
        assert result == "Permission denied."
        hooks_with_allow._ask_user.assert_not_called()  # deny short-circuits

    def test_no_allow_no_deny_falls_through_to_rules(self, hooks_with_allow, monkeypatch):
        """Command matching neither deny nor allow → falls through to rules."""
        # `ls` is safe (no deny, no allow, no rule match) → should be allowed
        block = MockBlock("bash", {"command": "ls -la"})
        result = hooks_with_allow.check_permission_hook("PreToolUse", block)
        assert result is None  # allowed (no rule matched)

    def test_destructive_bash_still_prompts(self, hooks_with_allow, monkeypatch):
        """Destructive bash (rule match) still prompts even with allow_patterns set.

        `rm somefile` matches _destructive_bash rule but not deny_patterns
        (only `rm -rf /` is denied). Should trigger _ask_user.
        """
        monkeypatch.setattr(hooks_with_allow, "_ask_user", lambda *a: "deny")
        block = MockBlock("bash", {"command": "rm somefile"})
        result = hooks_with_allow.check_permission_hook("PreToolUse", block)
        assert result == "Permission denied."  # user denied

    def test_pytest_allow_pattern(self, hooks_with_allow):
        """`pytest *` allows running the test suite silently."""
        hooks_with_allow._ask_user = MagicMock(return_value="deny")
        block = MockBlock("bash", {"command": "pytest tests/test_foo.py -v"})
        result = hooks_with_allow.check_permission_hook("PreToolUse", block)
        assert result is None  # allowed by `pytest *`
        hooks_with_allow._ask_user.assert_not_called()


# ---------------------------------------------------------------------------
# 4. 向后兼容：无 allow_patterns 时行为不变
# ---------------------------------------------------------------------------
class TestBackwardCompat:
    def test_default_policy_has_empty_allow_patterns(self):
        """DEFAULT_POLICY.allow_patterns is empty tuple (no behavior change)."""
        assert DEFAULT_POLICY.allow_patterns == ()

    def test_default_policy_matches_allow_returns_none(self):
        """Default policy never silent-allows anything."""
        assert DEFAULT_POLICY.matches_allow("ls -la") is None
        assert DEFAULT_POLICY.matches_allow("python -c 'foo'") is None

    def test_hooks_without_allow_patterns_works_as_before(self, monkeypatch):
        """Hooks with default policy behaves exactly as before P2-1."""
        h = Hooks()
        # Safe command → allowed
        block = MockBlock("bash", {"command": "ls -la"})
        assert h.check_permission_hook("PreToolUse", block) is None
        # Denied command → blocked
        block = MockBlock("bash", {"command": "rm -rf /"})
        assert h.check_permission_hook("PreToolUse", block) == "Permission denied."


# ---------------------------------------------------------------------------
# 5. config.py: _parse_policy_section 解析 allow_patterns
# ---------------------------------------------------------------------------
class TestParseAllowPatterns:
    def test_allow_patterns_parsed(self):
        """[permissions] allow_patterns = [...] is parsed into policy."""
        section = {"allow_patterns": ["python -c *", "pytest *"]}
        policy = _parse_policy_section(section)
        assert policy.allow_patterns == ("python -c *", "pytest *")

    def test_allow_patterns_default_empty(self):
        """Missing allow_patterns → empty tuple (backward compat)."""
        policy = _parse_policy_section({"deny_patterns": ["rm -rf /"]})
        assert policy.allow_patterns == ()

    def test_allow_patterns_must_be_list_of_strings(self):
        """Non-string entries raise ConfigError."""
        with pytest.raises(ConfigError, match="allow_patterns"):
            _parse_policy_section({"allow_patterns": ["ok", 123]})

    def test_allow_patterns_must_be_list(self):
        """Non-list value raises ConfigError."""
        with pytest.raises(ConfigError, match="allow_patterns"):
            _parse_policy_section({"allow_patterns": "python -c *"})

    def test_allow_and_deny_coexist(self):
        """Both deny_patterns and allow_patterns can be set together."""
        section = {
            "deny_patterns": ["rm -rf /"],
            "allow_patterns": ["python -c *"],
        }
        policy = _parse_policy_section(section)
        assert "rm -rf /" in policy.deny_patterns
        assert policy.allow_patterns == ("python -c *",)

    def test_deny_add_preserves_allow(self):
        """deny_patterns_add doesn't clobber allow_patterns."""
        section = {
            "deny_patterns_add": ["halt"],
            "allow_patterns": ["pytest *"],
        }
        policy = _parse_policy_section(section)
        assert "halt" in policy.deny_patterns
        assert policy.allow_patterns == ("pytest *",)
