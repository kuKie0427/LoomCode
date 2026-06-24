"""Triangle Protocol v1 — data contracts + parsers + validators.

See docs/triangle-protocol.md for the specification and
docs/triangle-protocol-examples.md for end-to-end examples.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loguru import logger

PROTOCOL_VERSION = "v1"


@dataclass(frozen=True)
class FeatureCard:
    id: str
    name: str
    description: str
    verification: str
    acceptance_criteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeEnvelope:
    allow_paths: tuple[str, ...]
    deny_paths: tuple[str, ...]
    allow_actions: tuple[str, ...]
    deny_actions: tuple[str, ...]
    max_turns: int = 30
    max_files_touched: int = 10


@dataclass(frozen=True)
class FileChange:
    path: str
    lines_added: int
    lines_deleted: int
    summary: str


@dataclass(frozen=True)
class DeltaReport:
    status: Literal["complete", "partial", "blocked"]
    files_modified: tuple[FileChange, ...]
    files_created: tuple[str, ...]
    # Advisory field — Reviewer MAY read as hint but MUST NOT fail if entries
    # are missing/incomplete. See docs/triangle-protocol.md §6.2.
    files_considered_but_not_changed: tuple[str, ...]
    verification_run: str
    verification_result: str
    escalations: tuple[str, ...]


Action = Literal["none", "scope_trim", "fix_bug",
                 "improve_quality", "clarify_with_user", "escalate"]


@dataclass(frozen=True)
class FeedbackDirective:
    # action is a LIST (tuple) — allows compound verdicts like [scope_trim, fix_bug].
    # MUST be non-empty. See docs/triangle-protocol.md §7.2-7.3 for combination rules.
    action: tuple[Action, ...]
    target_files: tuple[str, ...] = ()
    target_lines: tuple[str, ...] = ()
    retry_review: bool = False
    notes: str = ""


_KNOWN_OLDER_VERSIONS: set[str] = set()

# ── YAML-ish body parser ──────────────────────────────────────────────


def _parse_yaml_ish(body: str) -> dict[str, str | list[str]]:
    """Parse a YAML-ish block body into a dict of field name → value or list.

    Supports:
      - ``key: value`` (scalar, stripped)
      - ``key: |`` (multi-line block, subsequent indented lines)
      - ``key:`` followed by ``  - item`` lines (list)
      - ``_protocol: v1`` (internal meta field, passed through)

    Does NOT support nested structures, quoted strings.
    Returns a plain dict — callers cast to the desired types.
    """
    result: dict[str, str | list[str]] = {}
    current_list_key: str | None = None
    current_block_key: str | None = None
    block_lines: list[str] = []

    for line in body.splitlines():
        # Multi-line block continuation. Strip the 2-space literal-block indent
        # so the parsed value matches the original (YAML semantics: `key: |`
        # content is the unindented text). Blank lines are preserved as empty
        # strings to keep multi-line roundtrip stable.
        if current_block_key and (line.startswith("  ") or line == ""):
            block_lines.append(line[2:] if line.startswith("  ") else "")
            continue
        if current_block_key is not None:
            result[current_block_key] = "\n".join(block_lines)
            current_block_key = None
            block_lines = []

        # List items
        if line.startswith("  - ") and current_list_key:
            result.setdefault(current_list_key, [])
            assert isinstance(result[current_list_key], list)
            result[current_list_key].append(line[4:])  # type: ignore[union-attr]
            continue
        current_list_key = None

        # Key: value or key: |
        m = re.match(r"^(\w[\w_]*):\s*(.*)", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val == "|":
                current_block_key = key
                block_lines = []
                current_list_key = None
            elif val == "":
                # Could be start of a list — next line will tell
                current_list_key = key
                result[key] = []  # will be replaced or remain empty
            else:
                result[key] = val

    # Flush trailing block
    if current_block_key is not None and block_lines:
        result[current_block_key] = "\n".join(block_lines)

    return result


def _parse_action_list(body: str) -> list[str]:
    """Extract ``action: [item1, item2, ...]`` from a parsed body.

    Returns empty list if no action field found or it's not a bracketed list.
    """
    raw = _parse_yaml_ish(body)
    action_val = raw.get("action")
    if action_val is None:
        return []
    if isinstance(action_val, list):
        return [str(a).strip() for a in action_val]
    # Try to parse "[a, b, c]" format
    if isinstance(action_val, str) and action_val.startswith("[") and action_val.endswith("]"):
        inner = action_val[1:-1]
        return [a.strip().strip('"').strip("'") for a in inner.split(",") if a.strip()]
    return []


# ── Serializers ───────────────────────────────────────────────────────


def serialize_feature_card(card: FeatureCard) -> str:
    """Serialize FeatureCard to ``<feature_card>...</feature_card>`` block."""
    lines = ["<feature_card>"]
    lines.append(f"id: {card.id}")
    lines.append(f"name: {card.name}")
    lines.append("description: |")
    for dl in card.description.splitlines():
        lines.append(f"  {dl}")
    lines.append(f"verification: {card.verification}")
    if card.acceptance_criteria:
        lines.append("acceptance_criteria:")
        for ac in card.acceptance_criteria:
            lines.append(f"  - {ac}")
    lines.append("</feature_card>")
    return "\n".join(lines)


def serialize_scope_envelope(scope: ScopeEnvelope) -> str:
    """Serialize ScopeEnvelope to ``<scope_envelope>...</scope_envelope>`` block."""
    lines = ["<scope_envelope>"]
    lines.append("allow_paths:")
    for p in scope.allow_paths:
        lines.append(f"  - {p}")
    lines.append("deny_paths:")
    for p in scope.deny_paths:
        lines.append(f"  - {p}")
    actions_str = ", ".join(scope.allow_actions)
    lines.append(f"allow_actions: [{actions_str}]")
    deny_str = ", ".join(scope.deny_actions)
    lines.append(f"deny_actions: [{deny_str}]")
    lines.append(f"max_turns: {scope.max_turns}")
    lines.append(f"max_files_touched: {scope.max_files_touched}")
    lines.append("</scope_envelope>")
    return "\n".join(lines)


def serialize_delta_report(delta: DeltaReport) -> str:
    """Serialize DeltaReport to ``<delta_report>...</delta_report>`` block.

    Used by ``run_review()`` to inject Generator's delta into Reviewer prompt (TP-2),
    and by PreCompact hook to persist delta across autocompact (TP-4).
    """
    lines = ["<delta_report>"]
    lines.append(f"status: {delta.status}")
    lines.append("files_modified:")
    for fc in delta.files_modified:
        lines.append(f"  - path: {fc.path}")
        lines.append(f"    lines_added: {fc.lines_added}")
        lines.append(f"    lines_deleted: {fc.lines_deleted}")
        lines.append(f"    summary: {fc.summary}")
    lines.append("files_created:")
    for path in delta.files_created:
        lines.append(f"  - {path}")
    lines.append("files_considered_but_not_changed:")
    for path in delta.files_considered_but_not_changed:
        lines.append(f"  - {path}")
    lines.append(f"verification_run: {delta.verification_run}")
    lines.append("verification_result: |")
    for vl in delta.verification_result.splitlines():
        lines.append(f"  {vl}")
    lines.append("escalations:")
    for esc in delta.escalations:
        lines.append(f"  - {esc}")
    lines.append("</delta_report>")
    return "\n".join(lines)


def serialize_feedback_directive(fd: FeedbackDirective) -> str:
    """Serialize FeedbackDirective to ``<feedback_directive>...</feedback_directive>`` block.

    Used by PreCompact hook to inject structured directive into system-reminder (TP-4).
    """
    lines = ["<feedback_directive>"]
    action_str = ", ".join(fd.action)
    lines.append(f"action: [{action_str}]")
    if fd.target_files:
        lines.append("target_files:")
        for tf in fd.target_files:
            lines.append(f"  - {tf}")
    if fd.target_lines:
        lines.append("target_lines:")
        for tl in fd.target_lines:
            lines.append(f"  - {tl}")
    lines.append(f"retry_review: {str(fd.retry_review).lower()}")
    if fd.notes:
        lines.append(f"notes: {fd.notes}")
    lines.append("</feedback_directive>")
    return "\n".join(lines)


# ── Parsers ───────────────────────────────────────────────────────────


def parse_delta_report(
    text: str, *, max_version: str = PROTOCOL_VERSION
) -> DeltaReport | None:
    """Extract and parse ``<delta_report>...</delta_report>`` from Generator's final message.

    Returns ``None`` if no block found, parse error, or incompatible ``_protocol`` version.
    Tolerant: missing optional fields use defaults; missing required fields → ``None``.

    Version handling per docs §9.3: absent ``_protocol`` → accept as ``max_version``;
    present and unknown newer → reject (return ``None`` + log error).
    """
    m = re.search(r"<delta_report>(.*?)</delta_report>", text, re.DOTALL)
    if not m:
        return None
    body = m.group(1)

    # Check _protocol version if present
    ver_match = re.search(r"_protocol:\s*(\S+)", body)
    if ver_match:
        ver = ver_match.group(1)
        if ver != max_version and ver not in _KNOWN_OLDER_VERSIONS:
            logger.error("parse_delta_report: unknown protocol version %s", ver)
            return None

    # Verify this is the LAST block: closing tag followed only by whitespace or EOF
    tail = text[m.end() :]
    if tail.strip():
        logger.warning(
            "parse_delta_report: content after </delta_report> (I4 violation)"
        )

    # Parse YAML-ish body
    parsed = _parse_yaml_ish(body)

    # Extract fields with tolerance
    try:
        status = parsed.get("status")
        if status not in ("complete", "partial", "blocked"):
            logger.warning("parse_delta_report: invalid status %r", status)
            return None

        # files_modified: parse list of dicts
        files_modified_raw = _parse_list_of_dicts(body, "files_modified")
        files_modified: list[FileChange] = []
        for item in files_modified_raw:
            path = item.get("path", "")
            if not path:
                return None
            try:
                added = int(item.get("lines_added", 0))
                deleted = int(item.get("lines_deleted", 0))
            except (ValueError, TypeError):
                added, deleted = 0, 0
            files_modified.append(
                FileChange(path=path, lines_added=added, lines_deleted=deleted, summary=item.get("summary", ""))
            )

        # files_created: simple list
        files_created = _parse_simple_list(body, "files_created")

        # files_considered_but_not_changed: advisory
        files_considered = _parse_simple_list(body, "files_considered_but_not_changed")

        # verification fields
        verification_run = str(parsed.get("verification_run", ""))
        verification_result = str(parsed.get("verification_result", ""))

        # Escalations
        escalations = tuple(_parse_simple_list(body, "escalations"))

        return DeltaReport(
            status=status,  # type: ignore[arg-type]
            files_modified=tuple(files_modified),
            files_created=tuple(files_created),
            files_considered_but_not_changed=tuple(files_considered),
            verification_run=verification_run,
            verification_result=verification_result,
            escalations=escalations,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("parse_delta_report: parse error %s", exc)
        return None


def parse_feedback_directive(
    text: str, *, max_version: str = PROTOCOL_VERSION
) -> FeedbackDirective | None:
    """Extract and parse ``<feedback_directive>...</feedback_directive>`` from Reviewer's final message.

    Returns ``None`` if no block found, parse error, incompatible version, or
    action list violates combination rules (§7.3 / §7.6):
    - list MUST be non-empty
    - ``none`` MAY only appear alone
    - ``clarify_with_user`` MAY only appear alone
    - ``retry_review=true`` + ``action=[none]`` is contradictory
    - ``target_files`` required when action contains ``scope_trim``/``fix_bug``/``improve_quality``
    """
    m = re.search(
        r"<feedback_directive>(.*?)</feedback_directive>", text, re.DOTALL
    )
    if not m:
        return None
    body = m.group(1)

    # I4 last-block check: closing tag should be followed only by whitespace/EOF
    tail = text[m.end():]
    if tail.strip():
        logger.warning(
            "parse_feedback_directive: content after </feedback_directive> (I4 soft violation)"
        )

    # Check _protocol version
    ver_match = re.search(r"_protocol:\s*(\S+)", body)
    if ver_match:
        ver = ver_match.group(1)
        if ver != max_version and ver not in _KNOWN_OLDER_VERSIONS:
            logger.error(
                "parse_feedback_directive: unknown protocol version %s", ver
            )
            return None
        if ver != max_version and ver in _KNOWN_OLDER_VERSIONS:
            logger.warning(
                "parse_feedback_directive: deprecated protocol version %s (current %s)",
                ver, max_version,
            )

    # Parse action list
    actions = _parse_action_list(body)
    if not actions:
        return None

    # Parse remaining fields (needed for combination-rule validation below)
    parsed = _parse_yaml_ish(body)
    target_files_raw = _parse_simple_list(body, "target_files")
    target_lines_raw = _parse_simple_list(body, "target_lines")
    retry_raw = str(parsed.get("retry_review", "false")).strip().lower()
    retry_review = retry_raw == "true"
    notes = str(parsed.get("notes", ""))

    # Validate combination rules (§7.3 / §7.6) — ALL 6 cases
    if "none" in actions and len(actions) > 1:
        logger.warning(
            "parse_feedback_directive: 'none' mixed with other actions %r",
            actions,
        )
        return None
    if "clarify_with_user" in actions and len(actions) > 1:
        logger.warning(
            "parse_feedback_directive: 'clarify_with_user' mixed with other actions %r",
            actions,
        )
        return None
    # §7.6: retry_review=true + action=[none] is contradictory
    if "none" in actions and retry_review:
        logger.warning(
            "parse_feedback_directive: 'none' with retry_review=true is contradictory"
        )
        return None
    # §7.6: target_files required when action contains scope_trim/fix_bug/improve_quality
    requires_target = {"scope_trim", "fix_bug", "improve_quality"}
    if any(a in requires_target for a in actions) and not target_files_raw:
        logger.warning(
            "parse_feedback_directive: target_files missing for action %r",
            actions,
        )
        return None

    return FeedbackDirective(
        action=tuple(actions),  # type: ignore[arg-type]
        target_files=tuple(target_files_raw),
        target_lines=tuple(target_lines_raw),
        retry_review=retry_review,
        notes=notes,
    )


# ── Internal helpers ──────────────────────────────────────────────────


def _parse_list_of_dicts(body: str, section_name: str) -> list[dict[str, str]]:
    """Parse a section like ``files_modified:`` with ``- key: value`` sub-items.

    Returns list of dicts, one per ``- `` item.
    Empty list on missing section.
    """
    result: list[dict[str, str]] = []
    pattern = re.compile(
        rf"^{re.escape(section_name)}:\s*$", re.MULTILINE
    )
    m = pattern.search(body)
    if not m:
        return result
    # Find the first "- " after the section header
    section_start = m.end()
    # Snip body from section_start onwards
    rest = body[section_start:]
    current: dict[str, str] = {}
    for line in rest.splitlines():
        # Check if we hit the next top-level key (no leading space)
        if line and not line.startswith(" ") and ":" in line and not line.startswith("-"):
            break
        item_m = re.match(r"^  - \s*path:\s*(.*)", line)
        if item_m:
            if current:
                result.append(current)
            current = {"path": item_m.group(1).strip()}
            continue
        kv = re.match(r"^\s+(\w[\w_]*):\s*(.*)", line)
        if kv and current:
            current[kv.group(1)] = kv.group(2).strip()
    if current:
        result.append(current)
    return result


def _parse_simple_list(body: str, section_name: str) -> list[str]:
    """Parse a section containing simple ``  - item`` list items.

    Returns list of item strings. Empty list on missing section.
    Works for files_created, escalations, target_files, etc.
    """
    result: list[str] = []
    pattern = re.compile(
        rf"^{re.escape(section_name)}:\s*$", re.MULTILINE
    )
    m = pattern.search(body)
    if not m:
        return result
    rest = body[m.end() :]
    for line in rest.splitlines():
        if line and not line.startswith(" ") and ":" in line:
            break
        item_m = re.match(r"^\s*- \s*(.*)", line)
        if item_m:
            result.append(item_m.group(1).strip())
    return result


# ── Validators ────────────────────────────────────────────────────────


def validate_delta_against_scope(
    delta: DeltaReport, scope: ScopeEnvelope
) -> list[str]:
    """Return list of scope violations. Empty list = in scope.

    Checks all paths in ``delta.files_modified`` and ``delta.files_created``
    against ``scope.allow_paths`` and ``scope.deny_paths`` using
    ``pathspec.PathSpec.from_lines("gitignore", ...)``.
    (Note: ``pathspec`` deprecated ``gitwildmatch`` in favor of ``gitignore``
    which is the non-deprecated GitIgnoreBasicPattern API.)
    """
    import pathspec

    violations: list[str] = []
    allow_spec = pathspec.PathSpec.from_lines("gitignore", scope.allow_paths)
    deny_spec = pathspec.PathSpec.from_lines("gitignore", scope.deny_paths)

    for fc in delta.files_modified:
        if deny_spec.match_file(fc.path):
            violations.append(f"{fc.path} matches deny_paths")
        elif not allow_spec.match_file(fc.path):
            violations.append(f"{fc.path} not in allow_paths")

    for path in delta.files_created:
        if deny_spec.match_file(path):
            violations.append(f"created {path} matches deny_paths")
        elif not allow_spec.match_file(path):
            violations.append(f"created {path} not in allow_paths")

    return violations


def validate_delta_against_git_diff(
    delta: DeltaReport, workdir: Path
) -> list[str]:
    """Return list of mismatches between ``delta.files_modified`` and
    ``git diff --numstat``.

    Uses ``--numstat`` (not ``--stat``) to get exact add/delete counts (R6).
    Tolerant: ±10% per file for whitespace, clamped to at least 1 line.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return [f"git diff failed: {exc}"]

    actual: dict[str, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added_str, deleted_str, path = parts
        try:
            actual[path] = (int(added_str), int(deleted_str))
        except (ValueError, TypeError):
            continue

    mismatches: list[str] = []
    declared = {
        fc.path: (fc.lines_added, fc.lines_deleted) for fc in delta.files_modified
    }

    for path, _ in actual.items():
        if path not in declared:
            mismatches.append(f"{path} in git diff but not in delta_report")

    for path, (a, d) in declared.items():
        if path not in actual:
            mismatches.append(f"{path} in delta_report but not in git diff")
            continue
        actual_a, actual_d = actual[path]
        if abs(actual_a - a) > max(1, actual_a * 0.1):
            mismatches.append(
                f"{path}: declared +{a} actual +{actual_a} (>{10}% diff)"
            )
        if abs(actual_d - d) > max(1, actual_d * 0.1):
            mismatches.append(
                f"{path}: declared -{d} actual -{actual_d} (>{10}% diff)"
            )

    return mismatches
