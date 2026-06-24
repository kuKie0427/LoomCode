"""Unit tests for loom/agent/triangle_protocol.py.

Covers: dataclass construction, serializers, parsers, validators, constants.
"""

from __future__ import annotations

from pathlib import Path

from loom.agent.triangle_protocol import (
    _KNOWN_OLDER_VERSIONS,
    PROTOCOL_VERSION,
    DeltaReport,
    FeatureCard,
    FeedbackDirective,
    FileChange,
    ScopeEnvelope,
    parse_delta_report,
    parse_feedback_directive,
    serialize_delta_report,
    serialize_feature_card,
    serialize_feedback_directive,
    serialize_scope_envelope,
    validate_delta_against_git_diff,
    validate_delta_against_scope,
)

# ── Test data ──────────────────────────────────────────────────────────

COMPLETE_DELTA = """some text before

<delta_report>
status: complete
files_modified:
  - path: loom/tui/app.py
    lines_added: 12
    lines_deleted: 3
    summary: "Added keybinding"
files_created:
  - tests/test_new.py
files_considered_but_not_changed:
  - loom/agent/loop.py  # deny_paths
verification_run: "uv run pytest -v"
verification_result: |
  5 passed in 0.42s
escalations: []
</delta_report>

some text after
"""

PARTIAL_DELTA = """<delta_report>
status: partial
files_modified:
  - path: loom/tui/app.py
    lines_added: 12
    lines_deleted: 3
    summary: "Partial work"
files_created: []
files_considered_but_not_changed: []
verification_run: "skipped"
verification_result: "n/a"
escalations:
  - "[UNCLEAR: ambiguous description]"
</delta_report>"""

BLOCKED_DELTA = """<delta_report>
status: blocked
files_modified: []
files_created: []
files_considered_but_not_changed: []
verification_run: "n/a"
verification_result: "n/a"
escalations:
  - "[UNCLEAR: missing feature_card]"
</delta_report>"""

SINGLE_ACTION_FD = """<feedback_directive>
action: [scope_trim]
target_files:
  - loom/tui/app.py
target_lines:
  - 47-62
retry_review: true
notes: "Roll back lines 47-62"
</feedback_directive>"""

COMPOUND_ACTION_FD = """<feedback_directive>
action: [scope_trim, fix_bug]
target_files:
  - loom/tui/model_picker.py
target_lines:
  - 47-62
retry_review: true
</feedback_directive>"""

NONE_ALONE_FD = """<feedback_directive>
action: [none]
retry_review: false
</feedback_directive>"""

NONE_MIXED_FD = """<feedback_directive>
action: [none, fix_bug]
retry_review: false
</feedback_directive>"""

CLARIFY_MIXED_FD = """<feedback_directive>
action: [clarify_with_user, fix_bug]
retry_review: false
</feedback_directive>"""

EMPTY_ACTION_FD = """<feedback_directive>
action: []
retry_review: false
</feedback_directive>"""

VERSION_REJECT_FD = """<feedback_directive>
_protocol: v99
action: [none]
retry_review: false
</feedback_directive>"""

# §7.6 edge cases (B1 regression tests)
RETRY_CONTRADICTS_NONE_FD = """<feedback_directive>
action: [none]
retry_review: true
</feedback_directive>"""

SCOPE_TRIM_NO_TARGET_FD = """<feedback_directive>
action: [scope_trim]
retry_review: true
</feedback_directive>"""

FIX_BUG_NO_TARGET_FD = """<feedback_directive>
action: [fix_bug]
target_lines: []
retry_review: true
</feedback_directive>"""

IMPROVE_QUALITY_NO_TARGET_FD = """<feedback_directive>
action: [improve_quality]
retry_review: true
</feedback_directive>"""

CONTENT_AFTER_CLOSE_FD = """<feedback_directive>
action: [none]
retry_review: false
</feedback_directive>
trailing prose after closing tag"""


# ── Constants ──────────────────────────────────────────────────────────


class TestConstants:
    def test_protocol_version_constant(self) -> None:
        assert PROTOCOL_VERSION == "v1"

    def test_known_older_versions_empty(self) -> None:
        assert _KNOWN_OLDER_VERSIONS == set()

    def test_protocol_version_constant_exported(self) -> None:
        from loom.agent import triangle_protocol

        assert triangle_protocol.PROTOCOL_VERSION == "v1"


# ── Dataclass construction ─────────────────────────────────────────────


class TestDataclassDefaults:
    def test_feature_card_defaults(self) -> None:
        card = FeatureCard(id="x", name="n", description="d", verification="v")
        assert card.acceptance_criteria == ()

    def test_scope_envelope_defaults(self) -> None:
        scope = ScopeEnvelope(
            allow_paths=("src/**",),
            deny_paths=(),
            allow_actions=("read",),
            deny_actions=(),
        )
        assert scope.max_turns == 30
        assert scope.max_files_touched == 10

    def test_feedback_directive_defaults(self) -> None:
        fd = FeedbackDirective(action=("none",))
        assert fd.target_files == ()
        assert fd.target_lines == ()
        assert fd.retry_review is False
        assert fd.notes == ""

    def test_file_change_creation(self) -> None:
        fc = FileChange(path="f", lines_added=5, lines_deleted=3, summary="test")
        assert fc.path == "f"
        assert fc.lines_added == 5
        assert fc.lines_deleted == 3
        assert fc.summary == "test"

    def test_delta_report_status_literals(self) -> None:
        for status in ("complete", "partial", "blocked"):
            dr = DeltaReport(
                status=status,  # type: ignore[arg-type]
                files_modified=(),
                files_created=(),
                files_considered_but_not_changed=(),
                verification_run="",
                verification_result="",
                escalations=(),
            )
            assert dr.status == status


# ── Serializers ────────────────────────────────────────────────────────


class TestSerializers:
    def test_serialize_feature_card_roundtrip(self) -> None:
        card = FeatureCard(
            id="f-001",
            name="Test feature",
            description="A feature for testing",
            verification="uv run pytest -v",
            acceptance_criteria=("works", "fast"),
        )
        serialized = serialize_feature_card(card)
        assert serialized.startswith("<feature_card>")
        assert serialized.endswith("</feature_card>")
        assert "id: f-001" in serialized
        assert "name: Test feature" in serialized
        assert "acceptance_criteria:" in serialized
        assert "  - works" in serialized

    def test_serialize_scope_envelope_roundtrip(self) -> None:
        scope = ScopeEnvelope(
            allow_paths=("src/**", "tests/**"),
            deny_paths=("**/secrets.py",),
            allow_actions=("read", "write"),
            deny_actions=("delete",),
            max_turns=15,
            max_files_touched=5,
        )
        serialized = serialize_scope_envelope(scope)
        assert serialized.startswith("<scope_envelope>")
        assert serialized.endswith("</scope_envelope>")
        assert "allow_paths:" in serialized
        assert "  - src/**" in serialized
        assert "deny_paths:" in serialized
        assert "  - **/secrets.py" in serialized
        assert "max_turns: 15" in serialized
        assert "max_files_touched: 5" in serialized

    def test_serialize_delta_report_roundtrip(self) -> None:
        delta = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(path="loom/tui/app.py", lines_added=12, lines_deleted=3, summary="Added keybinding"),
            ),
            files_created=("tests/test_new.py",),
            files_considered_but_not_changed=("loom/agent/loop.py",),
            verification_run="uv run pytest -v",
            verification_result="5 passed in 0.42s",
            escalations=(),
        )
        serialized = serialize_delta_report(delta)
        assert serialized.startswith("<delta_report>")
        assert serialized.endswith("</delta_report>")
        assert "status: complete" in serialized
        assert "  - path: loom/tui/app.py" in serialized
        assert "lines_added: 12" in serialized
        assert "  - tests/test_new.py" in serialized
        assert "verification_run: uv run pytest -v" in serialized
        assert "verification_result: |" in serialized
        assert "escalations:" in serialized

    def test_serialize_feedback_directive_roundtrip(self) -> None:
        fd = FeedbackDirective(
            action=("scope_trim", "fix_bug"),
            target_files=("loom/tui/app.py",),
            target_lines=("47-62",),
            retry_review=True,
            notes="Roll back lines 47-62",
        )
        serialized = serialize_feedback_directive(fd)
        assert serialized.startswith("<feedback_directive>")
        assert serialized.endswith("</feedback_directive>")
        assert "action: [scope_trim, fix_bug]" in serialized
        assert "  - loom/tui/app.py" in serialized
        assert "  - 47-62" in serialized
        assert "retry_review: true" in serialized
        assert "notes: Roll back lines 47-62" in serialized

    def test_serialize_delta_report_full_roundtrip(self) -> None:
        """parse(serialize(x)) == x for DeltaReport with all fields populated."""
        original = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(path="loom/tui/app.py", lines_added=12, lines_deleted=3, summary="Added keybinding"),
                FileChange(path="loom/tui/messages.py", lines_added=5, lines_deleted=0, summary="Streaming layout"),
            ),
            files_created=("tests/test_new.py", "docs/spec.md"),
            files_considered_but_not_changed=("loom/agent/loop.py",),
            verification_run="uv run pytest -v",
            verification_result="5 passed in 0.42s\n3 warnings in 0.10s",
            escalations=("Need product sign-off on color scheme",),
        )
        serialized = serialize_delta_report(original)
        parsed = parse_delta_report(serialized)
        assert parsed == original, (
            f"roundtrip mismatch:\n  original={original!r}\n  parsed={parsed!r}"
        )

    def test_serialize_feedback_directive_full_roundtrip(self) -> None:
        """parse(serialize(x)) == x for FeedbackDirective with all fields populated."""
        original = FeedbackDirective(
            action=("scope_trim", "fix_bug"),
            target_files=("loom/tui/app.py", "loom/tui/messages.py"),
            target_lines=("47-62", "100-110"),
            retry_review=True,
            notes="Roll back lines 47-62",
        )
        serialized = serialize_feedback_directive(original)
        parsed = parse_feedback_directive(serialized)
        assert parsed == original, (
            f"roundtrip mismatch:\n  original={original!r}\n  parsed={parsed!r}"
        )


# ── DeltaReport parsing ───────────────────────────────────────────────


class TestParseDeltaReport:
    def test_parse_delta_report_complete(self) -> None:
        delta = parse_delta_report(COMPLETE_DELTA)
        assert delta is not None
        assert delta.status == "complete"
        assert len(delta.files_modified) == 1
        assert delta.files_modified[0].path == "loom/tui/app.py"
        assert delta.files_modified[0].lines_added == 12
        assert delta.files_modified[0].lines_deleted == 3
        assert delta.files_modified[0].summary == '"Added keybinding"'
        assert delta.files_created == ("tests/test_new.py",)
        assert len(delta.files_considered_but_not_changed) == 1
        assert "loom/agent/loop.py" in delta.files_considered_but_not_changed[0]
        assert delta.verification_run == '"uv run pytest -v"'
        assert "5 passed" in delta.verification_result
        assert delta.escalations == ()

    def test_parse_delta_report_partial(self) -> None:
        delta = parse_delta_report(PARTIAL_DELTA)
        assert delta is not None
        assert delta.status == "partial"
        assert len(delta.files_modified) == 1
        assert delta.files_modified[0].path == "loom/tui/app.py"
        assert delta.files_created == ()
        assert len(delta.escalations) == 1
        assert "UNCLEAR" in delta.escalations[0]

    def test_parse_delta_report_blocked(self) -> None:
        delta = parse_delta_report(BLOCKED_DELTA)
        assert delta is not None
        assert delta.status == "blocked"
        assert delta.files_modified == ()
        assert delta.files_created == ()
        assert len(delta.escalations) == 1
        assert "UNCLEAR: missing feature_card" in delta.escalations[0]

    def test_parse_delta_report_missing_block_returns_none(self) -> None:
        result = parse_delta_report("no delta report block here")
        assert result is None

    def test_parse_delta_report_malformed_returns_none(self) -> None:
        text = """<delta_report>
status: invalid_status_xyz
files_modified: []
files_created: []
files_considered_but_not_changed: []
verification_run: "n/a"
verification_result: "n/a"
escalations: []
</delta_report>"""
        result = parse_delta_report(text)
        assert result is None

    def test_parse_delta_report_version_rejection(self) -> None:
        text = """<delta_report>
_protocol: v99
status: complete
files_modified: []
files_created: []
files_considered_but_not_changed: []
verification_run: "n/a"
verification_result: "n/a"
escalations: []
</delta_report>"""
        result = parse_delta_report(text)
        assert result is None

    def test_parse_delta_report_version_absent_defaults(self) -> None:
        text = """<delta_report>
status: complete
files_modified: []
files_created: []
files_considered_but_not_changed: []
verification_run: "n/a"
verification_result: "n/a"
escalations: []
</delta_report>"""
        result = parse_delta_report(text)
        assert result is not None
        assert result.status == "complete"
        assert result.files_modified == ()
        assert result.files_created == ()
        assert result.escalations == ()


# ── FeedbackDirective parsing ─────────────────────────────────────────


class TestParseFeedbackDirective:
    def test_parse_feedback_directive_single_action(self) -> None:
        fd = parse_feedback_directive(SINGLE_ACTION_FD)
        assert fd is not None
        assert fd.action == ("scope_trim",)
        assert fd.target_files == ("loom/tui/app.py",)
        assert fd.target_lines == ("47-62",)
        assert fd.retry_review is True
        assert fd.notes == '"Roll back lines 47-62"'

    def test_parse_feedback_directive_compound_action(self) -> None:
        fd = parse_feedback_directive(COMPOUND_ACTION_FD)
        assert fd is not None
        assert fd.action == ("scope_trim", "fix_bug")
        assert fd.target_files == ("loom/tui/model_picker.py",)
        assert fd.target_lines == ("47-62",)
        assert fd.retry_review is True

    def test_parse_feedback_directive_none_alone(self) -> None:
        fd = parse_feedback_directive(NONE_ALONE_FD)
        assert fd is not None
        assert fd.action == ("none",)
        assert fd.retry_review is False

    def test_parse_feedback_directive_none_mixed_returns_none(self) -> None:
        result = parse_feedback_directive(NONE_MIXED_FD)
        assert result is None

    def test_parse_feedback_directive_clarify_mixed_returns_none(self) -> None:
        result = parse_feedback_directive(CLARIFY_MIXED_FD)
        assert result is None

    def test_parse_feedback_directive_empty_list_returns_none(self) -> None:
        result = parse_feedback_directive(EMPTY_ACTION_FD)
        assert result is None

    def test_parse_feedback_directive_version_rejection(self) -> None:
        result = parse_feedback_directive(VERSION_REJECT_FD)
        assert result is None

    def test_parse_feedback_directive_retry_contradicts_none_returns_none(self) -> None:
        # §7.6 row: retry_review=true + action=[none] is contradictory
        result = parse_feedback_directive(RETRY_CONTRADICTS_NONE_FD)
        assert result is None

    def test_parse_feedback_directive_scope_trim_no_target_returns_none(self) -> None:
        # §7.6 row: scope_trim requires target_files
        result = parse_feedback_directive(SCOPE_TRIM_NO_TARGET_FD)
        assert result is None

    def test_parse_feedback_directive_fix_bug_no_target_returns_none(self) -> None:
        # §7.6 row: fix_bug requires target_files
        result = parse_feedback_directive(FIX_BUG_NO_TARGET_FD)
        assert result is None

    def test_parse_feedback_directive_improve_quality_no_target_returns_none(self) -> None:
        # §7.6 row: improve_quality requires target_files
        result = parse_feedback_directive(IMPROVE_QUALITY_NO_TARGET_FD)
        assert result is None

    def test_parse_feedback_directive_target_files_present_with_scope_trim_passes(self) -> None:
        # Regression guard: target_files present should pass (not falsely reject)
        result = parse_feedback_directive(SINGLE_ACTION_FD)
        assert result is not None

    def test_parse_feedback_directive_content_after_close_tag_warns_only(self) -> None:
        # Oracle #7: I4 soft check — content after </feedback_directive> → log warning, still parse
        result = parse_feedback_directive(CONTENT_AFTER_CLOSE_FD)
        assert result is not None  # soft enforcement: don't reject, just warn


# ── Validators ─────────────────────────────────────────────────────────


class TestValidators:
    def test_validate_delta_against_scope_deny_path_violation(self) -> None:
        scope = ScopeEnvelope(
            allow_paths=("loom/tui/**",),
            deny_paths=("loom/agent/**",),
            allow_actions=("read", "write"),
            deny_actions=(),
        )
        delta = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(path="loom/agent/loop.py", lines_added=1, lines_deleted=0, summary="bad"),
            ),
            files_created=(),
            files_considered_but_not_changed=(),
            verification_run="n/a",
            verification_result="n/a",
            escalations=(),
        )
        violations = validate_delta_against_scope(delta, scope)
        assert any("matches deny_paths" in v for v in violations)

    def test_validate_delta_against_scope_allow_path_ok(self) -> None:
        scope = ScopeEnvelope(
            allow_paths=("loom/tui/**",),
            deny_paths=(),
            allow_actions=("read", "write"),
            deny_actions=(),
        )
        delta = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(path="loom/tui/app.py", lines_added=1, lines_deleted=0, summary="ok"),
            ),
            files_created=(),
            files_considered_but_not_changed=(),
            verification_run="n/a",
            verification_result="n/a",
            escalations=(),
        )
        violations = validate_delta_against_scope(delta, scope)
        assert violations == []

    def test_validate_delta_against_scope_created_path(self) -> None:
        scope = ScopeEnvelope(
            allow_paths=("tests/**",),
            deny_paths=(),
            allow_actions=("read", "write"),
            deny_actions=(),
        )
        delta = DeltaReport(
            status="complete",
            files_modified=(),
            files_created=("tests/test_new.py",),
            files_considered_but_not_changed=(),
            verification_run="n/a",
            verification_result="n/a",
            escalations=(),
        )
        violations = validate_delta_against_scope(delta, scope)
        assert violations == []

    def test_validate_delta_against_scope_spec_5_3_glob_patterns(self) -> None:
        # B2 fix: regression guard for spec §5.3 glob patterns under gitwildmatch mode
        # Each scope pattern from the spec §5.3 examples must correctly match/not-match.
        cases = [
            # (allow, deny, file_path, expected_violation_present)
            (("loom/tui/app.py",), (), "loom/tui/app.py", False),  # exact file
            (("loom/tui/**",), (), "loom/tui/app.py", False),  # dir recursive
            (("loom/tui/**",), (), "loom/tui/sub/app.py", False),  # deep nested
            (("tests/test_*.py",), (), "tests/test_foo.py", False),  # wildcard prefix
            (("tests/test_*.py",), (), "tests/prod.py", True),  # wrong prefix → violation
            (("**/*.py",), (), "loom/agent/x.py", False),  # recursive all .py
            (("docs/**",), (), "docs/sub/page.md", False),  # docs subtree
            (("loom/tui/**",), ("loom/agent/**",), "loom/agent/x.py", True),  # deny hit
            (("loom/tui/**",), ("loom/agent/**",), "loom/tui/app.py", False),  # allow wins
        ]
        for allow, deny, path, expect_violation in cases:
            scope = ScopeEnvelope(
                allow_paths=allow, deny_paths=deny,
                allow_actions=("read",), deny_actions=(),
            )
            delta = DeltaReport(
                status="complete",
                files_modified=(FileChange(path=path, lines_added=1, lines_deleted=0, summary="x"),),
                files_created=(), files_considered_but_not_changed=(),
                verification_run="n/a", verification_result="n/a", escalations=(),
            )
            violations = validate_delta_against_scope(delta, scope)
            has_violation = len(violations) > 0
            assert has_violation == expect_violation, (
                f"path={path!r} allow={allow} deny={deny} "
                f"expected_violation={expect_violation} got={has_violation} violations={violations}"
            )

    def test_validate_delta_against_git_diff_match(self, tmp_path: Path) -> None:
        """If delta declares files that don't match git diff, violations appear."""
        delta = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(path="nonexistent.py", lines_added=1, lines_deleted=0, summary="ghost"),
            ),
            files_created=(),
            files_considered_but_not_changed=(),
            verification_run="n/a",
            verification_result="n/a",
            escalations=(),
        )
        violations = validate_delta_against_git_diff(delta, tmp_path)
        # tmp_path has no git repo, so git diff will fail or return empty
        assert len(violations) > 0

    def _make_git_repo_with_files(
        self, tmp_path: Path, files: dict[str, str], modified: dict[str, str] | None = None
    ) -> None:
        """Helper: init git repo, add files, commit, then optionally modify.

        ``files`` is the initial state (committed).
        ``modified`` is the post-commit modifications (unstaged changes).
        """
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmp_path, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True
        )
        for path, content in files.items():
            full = tmp_path / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True
        )
        if modified:
            for path, content in modified.items():
                full = tmp_path / path
                full.write_text(content)

    def test_validate_delta_against_git_diff_real_match(self, tmp_path: Path) -> None:
        """Declared file with correct line counts → no violations."""
        self._make_git_repo_with_files(
            tmp_path,
            files={"foo.py": "line1\n"},
            modified={"foo.py": "line1\nline2\nline3\n"},  # +2 lines
        )
        delta = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(path="foo.py", lines_added=2, lines_deleted=0, summary="+2"),
            ),
            files_created=(),
            files_considered_but_not_changed=(),
            verification_run="n/a", verification_result="n/a", escalations=(),
        )
        violations = validate_delta_against_git_diff(delta, tmp_path)
        assert violations == []

    def test_validate_delta_against_git_diff_mismatch_lines(self, tmp_path: Path) -> None:
        """Declared +5 but actual +10 (100% off, >10% tolerance) → violation."""
        self._make_git_repo_with_files(
            tmp_path,
            files={"foo.py": "line1\n"},
            modified={"foo.py": "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\nline11\n"},
        )
        delta = DeltaReport(
            status="complete",
            files_modified=(
                # declared +5, actual +10 — exceeds ±10% tolerance (clamped to ≥1)
                FileChange(path="foo.py", lines_added=5, lines_deleted=0, summary="wrong"),
            ),
            files_created=(),
            files_considered_but_not_changed=(),
            verification_run="n/a", verification_result="n/a", escalations=(),
        )
        violations = validate_delta_against_git_diff(delta, tmp_path)
        assert any("foo.py" in v for v in violations)
        assert any("declared +5" in v for v in violations)

    def test_validate_delta_against_git_diff_missing_file(self, tmp_path: Path) -> None:
        """File in delta but not in git diff → 'in delta_report but not in git diff'."""
        self._make_git_repo_with_files(
            tmp_path,
            files={"real.py": "x\n"},
            # No modification — delta claims phantom.py was changed
        )
        delta = DeltaReport(
            status="complete",
            files_modified=(
                FileChange(path="phantom.py", lines_added=1, lines_deleted=0, summary="ghost"),
            ),
            files_created=(),
            files_considered_but_not_changed=(),
            verification_run="n/a", verification_result="n/a", escalations=(),
        )
        violations = validate_delta_against_git_diff(delta, tmp_path)
        assert any("phantom.py" in v for v in violations)
        assert any("in delta_report but not in git diff" in v for v in violations)
