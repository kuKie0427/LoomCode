import html
import http.client
import re
import shutil
import subprocess
import time
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

import httpx
from anthropic.types import MessageParam, ToolParam
from loguru import logger

import loom.agent.trace as trace_mod
from loom.agent.config import LLM_CONFIG
from loom.agent.permissions import DEFAULT_POLICY
from loom.agent.tool_registry import Tool, ToolRegistry
from loom.memory import MemoryStore
from loom.skills import build_skill_index

WORKDIR = Path.cwd()

CURRENT_TODOS: list = []

VERIFY_TIMEOUT_SECONDS = 600
VERIFY_TAIL_LINES = 30


def run_verify(target: str = ".") -> str:
    """Run the project's init.sh verification pipeline. Returns structured pass/fail + tail.

    Fail-closed: any exception is caught, recorded to trace as verify_end with
    passed=False + error=str(exc), and a structured error string is returned.
    Never swallows exceptions silently.
    """
    tr = trace_mod.current()
    if tr is not None:
        tr.record("verify_start", target=target)

    try:
        # safe_path() validates target is inside WORKDIR (raise ValueError if not)
        target_path = safe_path(target)
        init_sh = target_path / "init.sh"
        if not init_sh.is_file():
            elapsed_ms = 0
            result = f"No init.sh found at {target}"
            if tr is not None:
                tr.record("verify_end", target=target, exit_code=-1,
                          duration_ms=elapsed_ms, passed=False, error="missing_init_sh")
            return result

        t0 = time.monotonic()
        proc = subprocess.run(
            [str(init_sh)], cwd=str(target_path),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        passed = proc.returncode == 0

        # Build tail (last N lines of combined stdout+stderr)
        combined = (proc.stdout or "") + (proc.stderr or "")
        tail_lines = combined.splitlines()[-VERIFY_TAIL_LINES:]
        tail = "\n".join(tail_lines)

        if tr is not None:
            tr.record("verify_end", target=target, exit_code=proc.returncode,
                      duration_ms=elapsed_ms, passed=passed)

        status = "pass" if passed else "fail"
        return (
            f"[verify: {status} exit={proc.returncode} duration={elapsed_ms}ms]\n"
            f"--- last {len(tail_lines)} lines of stdout ---\n{tail}"
        )
    except subprocess.TimeoutExpired:
        if tr is not None:
            tr.record("verify_end", target=target, exit_code=-1,
                      duration_ms=VERIFY_TIMEOUT_SECONDS * 1000, passed=False,
                      error="timeout")
        return f"[verify: fail timeout={VERIFY_TIMEOUT_SECONDS}s]\ninit.sh did not complete within {VERIFY_TIMEOUT_SECONDS}s"
    except Exception as exc:
        # fail-closed: don't swallow. Return structured error string.
        if tr is not None:
            tr.record("verify_end", target=target, exit_code=-1,
                      duration_ms=0, passed=False, error=str(exc))
        return f"[verify: fail error={type(exc).__name__}]\n{exc}"


def run_bash(command: str) -> str:
    matched = DEFAULT_POLICY.matches_deny(command)
    if matched is not None:
        return f"Error: Dangerous command blocked (matched: {matched})"
    try:
        r = subprocess.run(
            command, shell=True, cwd=WORKDIR,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR.resolve()):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, limit: int | None = None, offset: int = 0) -> str:
    try:
        if offset < 0:
            return f"Error: offset must be >= 0, got {offset}"
        all_lines = safe_path(path).read_text().splitlines()
        total = len(all_lines)
        if offset == 0:
            start_idx = 0
        elif offset > total:
            return f"(file has {total} lines, offset {offset} is past end)"
        else:
            start_idx = offset - 1
        end_idx = start_idx + limit if limit is not None else total
        window = all_lines[start_idx:end_idx]
        if not window:
            return "(no content in window)"
        width = len(str(total))
        numbered = [f"{start_idx + 1 + i:>{width}}: {line}" for i, line in enumerate(window)]
        remaining_after = total - (start_idx + len(window))
        if remaining_after > 0:
            numbered.append(f"... ({remaining_after} more lines)")
        return "\n".join(numbered)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def _format_unified_diff(path: str, original: str, updated: str) -> str:
    import difflib
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=2,
    )
    return "".join(diff).rstrip("\n")


def _fuzzy_match_single(text: str, snippet: str, min_ratio: float = 0.85) -> int | None:
    """Return the byte offset of the best single fuzzy match of `snippet` in `text`,
    or None if no match meets `min_ratio` AND the snippet is unique enough.

    Uses difflib.SequenceMatcher ratio at every possible start position.
    """
    import difflib
    if not snippet:
        return None
    snippet_lines = snippet.splitlines(keepends=True)
    snippet_len = sum(len(s) for s in snippet_lines)
    if snippet_len == 0 or snippet_len > len(text):
        return None
    n_lines = len(snippet_lines)
    text_lines = text.splitlines(keepends=True)
    if n_lines > len(text_lines):
        return None
    best_ratio = 0.0
    best_offset = -1
    for i in range(len(text_lines) - n_lines + 1):
        window = "".join(text_lines[i : i + n_lines])
        ratio = difflib.SequenceMatcher(None, snippet, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_offset = sum(len(l) for l in text_lines[:i])
            if best_ratio >= 0.999:
                break
    if best_ratio >= min_ratio:
        return best_offset
    return None


def _edit_apply(text: str, old_text: str, new_text: str, fuzzy_min_ratio: float = 0.85) -> tuple[str, str]:
    """Apply a single old->new edit to `text`.

    Returns (updated_text, mode) where mode is 'exact', 'fuzzy', or 'error'.
    """
    if not old_text:
        return text, "error:empty_old_text"
    count = text.count(old_text)
    if count == 1:
        return text.replace(old_text, new_text, 1), "exact"
    if count > 1:
        return text, f"error:multiple_matches({count})"
    if len(old_text) >= 40:
        offset = _fuzzy_match_single(text, old_text, fuzzy_min_ratio)
        if offset is not None:
            return text[:offset] + new_text + text[offset + len(old_text):], "fuzzy"
    return text, "error:not_found"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    """Replace a single block of text in a file.

    Match order: (1) exact unique match -> apply, (2) multiple exact matches
    -> return error, (3) zero exact matches AND old_text is >=40 chars ->
    difflib fuzzy fallback at ratio>=0.85 -> apply once, (4) otherwise
    return error. Returns a structured message with a unified diff on success.
    """
    try:
        file_path = safe_path(path)
        text = file_path.read_text()
    except Exception as exc:
        return f"Error: {exc}"
    updated, mode = _edit_apply(text, old_text, new_text)
    if mode.startswith("error:"):
        return f"Error: {mode.split(':', 1)[1]} (path={path})"
    if mode == "fuzzy":
        note = f" (fuzzy match, len(old_text)={len(old_text)})"
    else:
        note = ""
    diff = _format_unified_diff(path, text, updated)
    try:
        file_path.write_text(updated)
    except Exception as exc:
        return f"Error: {exc}"
    return f"Edited {path}{note}\n--- diff ---\n{diff}"


def run_multi_edit(path: str, edits: list[dict]) -> str:
    """Apply multiple edits to one file atomically.

    Each edit is {old_text, new_text}. All edits must apply (with the same
    exact/fuzzy rules as run_edit) or the file is left unchanged and an
    error is returned. Returns a structured message with the diff.
    """
    try:
        file_path = safe_path(path)
        original = file_path.read_text()
    except Exception as exc:
        return f"Error: {exc}"
    if not isinstance(edits, list) or not edits:
        return f"Error: edits must be a non-empty list"
    for i, e in enumerate(edits):
        if not isinstance(e, dict) or "old_text" not in e or "new_text" not in e:
            return f"Error: edits[{i}] missing old_text or new_text"
    text = original
    for i, e in enumerate(edits):
        updated, mode = _edit_apply(text, e["old_text"], e["new_text"])
        if mode.startswith("error:"):
            return f"Error: edits[{i}] {mode.split(':', 1)[1]} (path={path}, file unchanged)"
        text = updated
    try:
        file_path.write_text(text)
    except Exception as exc:
        return f"Error: {exc}"
    diff = _format_unified_diff(path, original, text)
    return f"Multi-edited {path} ({len(edits)} edits applied)\n--- diff ---\n{diff}"


def run_edit_lines(path: str, start_line: int, end_line: int, new_content: str) -> str:
    """Replace lines [start_line, end_line] inclusive (1-indexed) with new_content.

    Useful for structured line-range edits when the agent can identify lines
    from `read_file` output but reproducing exact whitespace is risky.
    """
    try:
        file_path = safe_path(path)
        text = file_path.read_text()
    except Exception as exc:
        return f"Error: {exc}"
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if start_line < 1 or end_line < start_line:
        return f"Error: invalid line range ({start_line}..{end_line})"
    if start_line > total:
        return f"Error: start_line {start_line} > total {total}"
    s = start_line - 1
    e = min(end_line, total)
    before = "".join(lines[:s])
    after = "".join(lines[e:])
    if new_content and not new_content.endswith("\n"):
        new_content_eol = new_content + "\n"
    else:
        new_content_eol = new_content
    updated = before + new_content_eol + after
    try:
        file_path.write_text(updated)
    except Exception as exc:
        return f"Error: {exc}"
    diff = _format_unified_diff(path, text, updated)
    return f"Replaced lines {start_line}..{end_line} in {path} ({total} total)\n--- diff ---\n{diff}"


def run_glob(pattern: str) -> str:
    import glob as g
    try:
        results = []
        for match in g.glob(pattern, root_dir=WORKDIR):
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


GREP_MAX_MATCHES = 200
GREP_CONTENT_MAX_CHARS = 200
GREP_LARGE_FILE_BYTES = 1_000_000


def _format_grep_hit(rel_path: str, line_no: int, content: str) -> str:
    snippet = content[:GREP_CONTENT_MAX_CHARS]
    if len(content) > GREP_CONTENT_MAX_CHARS:
        snippet += "..."
    return f"{rel_path}:{line_no}:{snippet}"


def _iter_files(root: Path, glob_pat: str | None) -> list[Path]:
    if glob_pat is not None:
        candidates = list(root.rglob(glob_pat))
    else:
        candidates = [p for p in root.rglob("*") if p.is_file()]
    return [p for p in candidates if p.is_file() and not any(part.startswith(".") for part in p.parts)]


def _grep_python(pattern: str, root: Path, glob_pat: str | None,
                 case_insensitive: bool) -> tuple[list[tuple[Path, int, str]], int]:
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        return [], 0
    hits: list[tuple[Path, int, str]] = []
    truncated = 0
    for fpath in _iter_files(root, glob_pat):
        try:
            if fpath.stat().st_size > GREP_LARGE_FILE_BYTES:
                continue
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                if len(hits) < GREP_MAX_MATCHES:
                    hits.append((fpath, line_no, line))
                else:
                    truncated += 1
    return hits, truncated


def _grep_ripgrep(pattern: str, root: Path, glob_pat: str | None,
                  case_insensitive: bool) -> tuple[list[tuple[Path, int, str]], int] | None:
    if not shutil.which("rg"):
        return None
    cmd = ["rg", "--no-heading", "--line-number", "--no-config"]
    if case_insensitive:
        cmd.append("-i")
    if glob_pat is not None:
        cmd.extend(["--glob", glob_pat])
    cmd.extend(["--max-count", "10000", pattern, str(root)])
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=30, check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode not in (0, 1):
        return None
    hits: list[tuple[Path, int, str]] = []
    truncated = 0
    for raw in proc.stdout.splitlines():
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        try:
            line_no = int(parts[1])
        except ValueError:
            continue
        abs_path = Path(parts[0])
        if not abs_path.is_absolute():
            abs_path = (root / parts[0]).resolve()
        if len(hits) < GREP_MAX_MATCHES:
            hits.append((abs_path, line_no, parts[2]))
        else:
            truncated += 1
    return hits, truncated


def run_grep(pattern: str, path: str = ".", glob: str | None = None,
             case_insensitive: bool = False) -> str:
    """Search files under WORKDIR for `pattern` and return structured matches.

    Output is `path:line:content` per match, capped at GREP_MAX_MATCHES hits.
    `glob` filters the file set (e.g. "*.py"). `case_insensitive` is a bool.
    """
    if not pattern:
        return "Error: pattern is required"
    try:
        root = safe_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if not root.exists():
        return f"Error: path does not exist: {path}"

    result = _grep_ripgrep(pattern, root, glob, case_insensitive)
    if result is None:
        hits, truncated = _grep_python(pattern, root, glob, case_insensitive)
    else:
        hits, truncated = result

    if not hits:
        return "(no matches)"
    workdir_resolved = WORKDIR.resolve()
    rendered = [
        _format_grep_hit(str(p.relative_to(workdir_resolved)), n, c)
        for p, n, c in hits
    ]
    if truncated:
        rendered.append(f"[...{truncated} more matches truncated at limit {GREP_MAX_MATCHES}]")
    return "\n".join(rendered)


WEB_FETCH_TIMEOUT_S = 30
WEB_FETCH_MAX_REDIRECTS = 5
WEB_FETCH_MAX_CHARS = 50_000


class _TextExtractor(HTMLParser):
    """Convert HTML to readable plain text. Drops scripts/styles, inserts
    blank lines around block-level elements, decodes entities."""

    BLOCK_TAGS = {
        "p", "div", "section", "article", "header", "footer", "main",
        "nav", "aside", "br", "hr", "li", "ul", "ol",
        "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
    }
    SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.parts.append(data)

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def run_web_fetch(url: str, max_chars: int = WEB_FETCH_MAX_CHARS) -> str:
    """Fetch a URL and return its readable text content.

    Implementation: httpx with redirect follow (max 5), 30s timeout, content-type
    sniff (text/html -> HTMLParser-based extraction; text/plain -> returned as-is;
    other -> error). Result is truncated to max_chars. is_read_only=True.
    """
    if not url:
        return "Error: url is required"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Error: only http/https URLs supported, got scheme={parsed.scheme!r}"
    try:
        with httpx.Client(
            timeout=WEB_FETCH_TIMEOUT_S,
            follow_redirects=True,
            max_redirects=WEB_FETCH_MAX_REDIRECTS,
        ) as client:
            r = client.get(url, headers={"User-Agent": "loom-web-fetch/1.0"})
    except httpx.HTTPError as exc:
        return f"Error: HTTP request failed: {type(exc).__name__}: {exc}"
    except Exception as exc:
        return f"Error: fetch failed: {type(exc).__name__}: {exc}"
    if r.status_code >= 400:
        return f"Error: HTTP {r.status_code} for {url}"
    content_type = r.headers.get("content-type", "").lower()
    body = r.text
    if "html" in content_type or body.lstrip().lower().startswith("<!doctype") or body.lstrip().lower().startswith("<html"):
        extractor = _TextExtractor()
        try:
            extractor.feed(body)
            text = extractor.get_text()
        except Exception as exc:
            return f"Error: HTML parse failed: {type(exc).__name__}: {exc}"
    elif "plain" in content_type or "markdown" in content_type or "json" in content_type:
        text = body
    else:
        return f"Error: unsupported content-type {content_type!r} for {url}"
    if not text.strip():
        return f"(empty content from {url})"
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    header = f"[fetch: {url} status={r.status_code} bytes_returned={len(body)}]"
    if truncated:
        header += f" (truncated at {max_chars} chars)"
    return f"{header}\n{text}"


def run_todo_write(todos: list) -> str:
    global CURRENT_TODOS
    for i, t in enumerate(todos):
        if "content" not in t or "status" not in t:
            return f"Error: todos[{i}] missing 'content' or 'status'"
        if t["status"] not in ("pending", "in_progress", "completed"):
            return f"Error: todos[{i}] has invalid status '{t['status']}'"
    CURRENT_TODOS = todos
    lines = ["\n\033[33m## Current Tasks\033[0m"]
    for t in CURRENT_TODOS:
        icon = {"pending": " ", "in_progress": "\033[36m▸\033[0m", "completed": "\033[32m✓\033[0m"}[t["status"]]
        lines.append(f"  [{icon}] {t['content']}")
    logger.info("\n".join(lines))
    # Fire backend callback for f-tui-header-backend-wiring. Deferred
    # import to avoid circular dependency (loop.py imports tools.py).
    from loom.agent.loop import fire_callback
    fire_callback("on_todo_update", list(CURRENT_TODOS))
    return f"Updated {len(CURRENT_TODOS)} tasks"


def run_memory_read() -> str:
    return MemoryStore(WORKDIR).read()

def run_memory_search(query: str) -> str:
    matches = MemoryStore(WORKDIR).search(query)
    if not matches:
        return f"(no matches for {query!r})"
    return "\n".join(matches)

def run_memory_write(entry: str, heading: str | None = None) -> str:
    try:
        MemoryStore(WORKDIR).append(entry, heading=heading)
    except ValueError as e:
        return f"Memory cap exceeded: {e}"
    try:
        from loom.agent.system_prompt import invalidate_system_prompt
        invalidate_system_prompt(reason="memory_write appended to MEMORY.md")
    except Exception:
        pass
    return f"Appended {len(entry)} chars to MEMORY.md"


def run_cold_archive(turns_json: str, dest: str | None = None, chunk_size: int = 50) -> str:
    """Archive a JSON-serialized list of turns to cold storage.

    `turns_json` must be a JSON array string (use bash + jq or similar to
    serialize prior to calling). Writes manifest + gzipped JSONL chunks
    to .minicode/cold-storage/ (or `dest` if provided). Returns a summary
    line with total turns + chunk count.
    """
    import json as _json
    from pathlib import Path as _P
    from loom.agent.cold_archive import archive as _archive
    try:
        turns = _json.loads(turns_json)
        if not isinstance(turns, list):
            return f"Error: cold_archive expected JSON array, got {type(turns).__name__}"
    except _json.JSONDecodeError as exc:
        return f"Error: cold_archive invalid JSON: {exc}"
    target = _P(dest) if dest else (WORKDIR / ".minicode" / "cold-storage")
    manifest = _archive(turns, target, chunk_size=chunk_size)
    return (
        f"archived {manifest.total_turns} turns ({manifest.total_tokens} est tokens) "
        f"into {len(manifest.chunks)} chunk(s) at {target}"
    )


def run_cold_load(start_turn: int, end_turn: int, dest: str | None = None) -> str:
    """Rehydrate turns [start_turn, end_turn) from cold storage.

    Returns the turns as a JSON array string so the model can parse them.
    Returns an error string on bad range / missing manifest.
    """
    from pathlib import Path as _P
    from loom.agent.cold_archive import rehydrate as _rehydrate
    target = _P(dest) if dest else (WORKDIR / ".minicode" / "cold-storage")
    try:
        turns = _rehydrate(target, start_turn, end_turn)
    except FileNotFoundError as exc:
        return f"Error: cold_load {exc}"
    except ValueError as exc:
        return f"Error: cold_load {exc}"
    import json as _json
    return _json.dumps(turns, ensure_ascii=False)


def run_load_skill(name: str) -> str:
    skill = build_skill_index(WORKDIR).get(name)
    if skill is None:
        return f"Error: skill {name!r} not found in {WORKDIR / '.minicode/skills'}"
    if not skill.has_body:
        return f"Error: skill {name!r} has no body (SKILL.md is empty after the metadata section)"
    try:
        from loom.agent.system_prompt import invalidate_system_prompt
        invalidate_system_prompt(reason=f"load_skill {name!r}")
    except Exception:
        pass
    return skill.body


def _coerce_lsp_line(target_path: Path, line: int) -> int:
    """R6 mitigation: auto-correct probable 1-indexed line numbers.

    LLMs frequently confuse grep 1-indexed line output with the LSP spec's
    0-indexed line numbers. If the supplied `line` is exactly the file's
    line count, the agent almost certainly passed the 1-indexed `cat -n`
    output it saw in `read_file`. We decrement and log a warning so the
    audit trail records the auto-correction.

    OSError reading the file → return the original line (let the LSP
    server's own out-of-range error be the source of truth).
    """
    try:
        text = target_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return line
    file_lines = text.splitlines()
    if (
        line > 0
        and line >= len(file_lines)
        and (line - 1) < len(file_lines)
    ):
        logger.warning(
            "LSP line {} out of range for {} ({} lines); "
            "auto-correcting to {} (1-indexed → 0-indexed)",
            line, target_path, len(file_lines), line - 1,
        )
        return line - 1
    return line


def _format_locations(locs: list[dict]) -> str:
    """Render LSP Location dicts as `path:line:col` lines (1-indexed for display)."""
    out: list[str] = []
    for loc in locs:
        uri = loc.get("uri", "")
        path = uri[len("file://"):] if uri.startswith("file://") else uri
        rng = loc.get("range", {}).get("start", {})
        out.append(f"{path}:{int(rng.get('line', 0)) + 1}:{int(rng.get('character', 0)) + 1}")
    return "\n".join(out)


def run_lsp_goto_definition(path: str, line: int, character: int) -> str:
    """Find the definition of the symbol at file:line:character.

    line/character are 0-indexed per LSP spec — use AFTER grep to find the
    candidate position and subtract 1. Fail-closed: missing config →
    "No LSP server configured"; manager not implemented (PL-2 stub) →
    "LSP unavailable"; LSPError → "LSP error: ..."; safe_path violation
    → "Error: ...".
    """
    from loom.agent import lsp_client
    from loom.agent.lsp_manager import (
        _ACTIVE_SERVERS,
        _LOCK as _LSP_LOCK,
        _PER_SERVER_LOCKS,
        get_or_start,
        get_server_lock,
    )
    from loom.agent.loop import _active_config

    try:
        target_path = safe_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    line = _coerce_lsp_line(target_path, line)
    try:
        server = get_or_start(target_path, _active_config)
    except (FileNotFoundError, lsp_client.LSPError) as exc:
        return f"LSP unavailable: {exc}"
    if server is None:
        return f"No LSP server configured for {Path(path).suffix} files"

    tr = trace_mod.current()
    if tr is not None:
        tr.record("lsp_request", server=server.name, method="textDocument/definition")

    with get_server_lock(server.name):
        try:
            locs = lsp_client.goto_definition(server, str(target_path), line, character)
        except (lsp_client.LSPError, EOFError) as exc:
            with _LSP_LOCK:
                _ACTIVE_SERVERS.pop(server.name, None)
                _PER_SERVER_LOCKS.pop(server.name, None)
            return f"LSP error: {exc} (server evicted; next call will restart it)"
    if not locs:
        return "no definition found"
    return _format_locations(locs)


def run_lsp_find_references(
    path: str,
    line: int,
    character: int,
    include_declaration: bool = True,
) -> str:
    """Find all references to the symbol at file:line:character.

    line/character are 0-indexed per LSP spec — use AFTER grep to find the
    candidate position and subtract 1. Returns `path:line:col` lines,
    "no references found", or a fail-closed error string. Read-only.
    """
    from loom.agent import lsp_client
    from loom.agent.lsp_manager import (
        _ACTIVE_SERVERS,
        _LOCK as _LSP_LOCK,
        _PER_SERVER_LOCKS,
        get_or_start,
        get_server_lock,
    )
    from loom.agent.loop import _active_config

    try:
        target_path = safe_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    line = _coerce_lsp_line(target_path, line)
    try:
        server = get_or_start(target_path, _active_config)
    except (FileNotFoundError, lsp_client.LSPError) as exc:
        return f"LSP unavailable: {exc}"
    if server is None:
        return f"No LSP server configured for {Path(path).suffix} files"

    tr = trace_mod.current()
    if tr is not None:
        tr.record("lsp_request", server=server.name, method="textDocument/references")

    with get_server_lock(server.name):
        try:
            locs = lsp_client.find_references(
                server, str(target_path), line, character,
                include_declaration=include_declaration,
            )
        except (lsp_client.LSPError, EOFError) as exc:
            with _LSP_LOCK:
                _ACTIVE_SERVERS.pop(server.name, None)
                _PER_SERVER_LOCKS.pop(server.name, None)
            return f"LSP error: {exc} (server evicted; next call will restart it)"
    if not locs:
        return "no references found"
    return _format_locations(locs)


def run_lsp_rename_symbol(path: str, line: int, character: int, new_name: str) -> str:
    """Rename the symbol at file:line:character via LSP and apply to disk.

    Two-pass permission flow (R3 fix — direct rule check, no second hook trigger):

    1. PreToolUse (automatic via ``_run_tool_block`` →
       ``Hooks.check_permission_hook`` → ``DEFAULT_POLICY.find_rule``):
       checks the entry ``path`` is inside the workspace.
    2. Post-LSP (manual, here in the handler): we now have the full
       ``WorkspaceEdit`` from the LSP server and can check EVERY file the
       rename will touch. The handler invokes ``DEFAULT_POLICY.find_rule``
       again with ``_resolved_files`` injected so the same rule — no
       duplication — validates the expanded file set.

    line/character are 0-indexed per LSP spec — use AFTER grep to find the
    candidate position and subtract 1. Fail-closed the same way as the
    other two LSP tools.
    """
    from loom.agent import lsp_client
    from loom.agent.lsp_apply import apply_workspace_edit, parse_workspace_edit
    from loom.agent.lsp_manager import (
        _ACTIVE_SERVERS,
        _LOCK as _LSP_LOCK,
        _PER_SERVER_LOCKS,
        get_or_start,
        get_server_lock,
    )
    from loom.agent.loop import _active_config
    from loom.agent.permissions import DEFAULT_POLICY

    try:
        target_path = safe_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    line = _coerce_lsp_line(target_path, line)
    try:
        server = get_or_start(target_path, _active_config)
    except (FileNotFoundError, lsp_client.LSPError) as exc:
        return f"LSP unavailable: {exc}"
    if server is None:
        return f"No LSP server configured for {Path(path).suffix} files"

    tr = trace_mod.current()
    if tr is not None:
        tr.record("lsp_request", server=server.name, method="textDocument/rename")

    with get_server_lock(server.name):
        try:
            edit = lsp_client.rename_symbol(server, str(target_path), line, character, new_name)
        except (lsp_client.LSPError, EOFError) as exc:
            with _LSP_LOCK:
                _ACTIVE_SERVERS.pop(server.name, None)
                _PER_SERVER_LOCKS.pop(server.name, None)
            return f"LSP error: {exc} (server evicted; next call will restart it)"
        except ValueError as exc:
            return f"Error: {exc}"
    if edit is None:
        return "rename cancelled (server returned null)"

    try:
        plan = parse_workspace_edit(edit)
    except (ValueError, NotImplementedError) as exc:
        return f"Cannot apply WorkspaceEdit: {exc}"

    # SECOND PASS: now we know every file the rename will touch.
    # We use DEFAULT_POLICY (not the Hooks instance's potentially-overridden
    # policy) intentionally — this is non-overridable defense-in-depth
    # against LSP server bugs/malice, not user preference.
    # If harness.toml policy override is ever implemented, this second pass
    # must stay hardcoded to DEFAULT_POLICY or call _lsp_rename_outside_workspace
    # directly (bypassing find_rule) to remain non-overridable.
    augmented_args = {
        "path": path, "line": line, "character": character, "new_name": new_name,
        "_resolved_files": [str(p) for p in plan.keys()],
    }
    blocking_rule = DEFAULT_POLICY.find_rule("lsp_rename_symbol", augmented_args)
    if blocking_rule is not None:
        return f"Rename blocked by permission policy: {blocking_rule.message}"

    try:
        results = apply_workspace_edit(edit)
    except Exception as exc:
        return f"Apply failed (in-process rollback attempted): {exc}"
    file_list = "\n  ".join(str(p) for p in results.keys())
    return f"Renamed to '{new_name}' in {len(results)} files:\n  {file_list}"


TOOL_REGISTRY = ToolRegistry()
TOOL_REGISTRY.register(Tool(
    name="bash",
    description="Run a shell command.",
    input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    handler=run_bash,
))
TOOL_REGISTRY.register(Tool(
    name="read_file",
    description=(
        "Read file contents with optional 1-indexed pagination. Output lines are numbered "
        "(cat -n style, right-aligned to total line count) so any line number you see can "
        "be passed straight back as `offset` for the next read. A trailing '... (N more "
        "lines)' hint indicates further content past the window."
    ),
    input_schema={"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}, "offset": {"type": "integer", "minimum": 1, "description": "1-indexed start line; omit or 0 to read from the beginning"}}, "required": ["path"]},
    handler=run_read,
    is_read_only=True,
    is_concurrent_safe=True,
))
TOOL_REGISTRY.register(Tool(
    name="write_file",
    description="Write content to a file.",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    handler=run_write,
))
TOOL_REGISTRY.register(Tool(
    name="edit_file",
    description=(
        "Replace a single block of text in a file. Match order: (1) exact unique "
        "match -> apply; (2) multiple exact matches -> error (add surrounding context "
        "to disambiguate); (3) zero matches AND old_text is >=40 chars -> difflib "
        "fuzzy fallback at ratio>=0.85 -> apply once; (4) otherwise error. Returns "
        "a unified diff on success. For multiple edits use `multi_edit`; for known "
        "line numbers use `edit_lines`."
    ),
    input_schema={"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
    handler=run_edit,
))
TOOL_REGISTRY.register(Tool(
    name="multi_edit",
    description=(
        "Apply multiple edits to a single file atomically. Each edit is a dict "
        "{old_text, new_text}. All edits use the same match rules as `edit_file` "
        "(exact, then fuzzy if old_text>=40 chars). If ANY edit fails, the file is "
        "left UNCHANGED and an error is returned. Returns a unified diff on success. "
        "Use this when you have several non-overlapping edits to one file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["old_text", "new_text"],
                },
            },
        },
        "required": ["path", "edits"],
    },
    handler=run_multi_edit,
))
TOOL_REGISTRY.register(Tool(
    name="edit_lines",
    description=(
        "Replace lines [start_line, end_line] inclusive (1-indexed) with new_content. "
        "Use this when you have read a file with `read_file` and know the line numbers "
        "to replace, rather than reproducing the exact text. Trailing newline is added "
        "automatically if new_content doesn't end in one. Past-EOF start_line returns "
        "an error."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
            "new_content": {"type": "string"},
        },
        "required": ["path", "start_line", "end_line", "new_content"],
    },
    handler=run_edit_lines,
))
TOOL_REGISTRY.register(Tool(
    name="glob",
    description="Find files matching a glob pattern.",
    input_schema={"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
    handler=run_glob,
    is_read_only=True,
    is_concurrent_safe=True,
))
TOOL_REGISTRY.register(Tool(
    name="grep",
    description=(
        "Search files under the workspace for a regex or literal pattern. "
        "Output is STRUCTURED: each match on its own line as `path:line:content` "
        "with content truncated to 200 chars, capped at 200 matches. Use grep "
        "BEFORE reading multiple files when looking for symbols, imports, or "
        "string usages — far cheaper than opening files one by one. `glob` "
        "filter narrows the file set (e.g. '*.py'). `case_insensitive=true` for "
        "case-insensitive search. Returns '(no matches)' when nothing found."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex or literal pattern to search for."},
            "path": {"type": "string", "description": "Workspace-relative root to search (default: '.')."},
            "glob": {"type": "string", "description": "File glob filter, e.g. '*.py'."},
            "case_insensitive": {"type": "boolean", "description": "Case-insensitive search (default: false)."},
        },
        "required": ["pattern"],
    },
    handler=run_grep,
    is_read_only=True,
    is_concurrent_safe=True,
))
TOOL_REGISTRY.register(Tool(
    name="web_fetch",
    description=(
        "Fetch a URL and return its readable text content. Use this to read "
        "external API docs, package READMEs, or reference material. Supports "
        "http and https only. Follows redirects (max 5) with a 30s timeout. "
        "Content-type routing: text/html -> extracted plain text (scripts/styles "
        "stripped, block elements separated by blank lines); text/plain / "
        "application/json / markdown -> returned as-is; other types -> error. "
        "Result is truncated to max_chars (default 50000). Returns a structured "
        "header with the URL, status code, and bytes returned so you can verify "
        "the fetch succeeded."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http or https URL to fetch."},
            "max_chars": {"type": "integer", "description": "Max content length to return (default 50000)."},
        },
        "required": ["url"],
    },
    handler=run_web_fetch,
    is_read_only=True,
    is_concurrent_safe=True,
))
TODO_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                },
                "required": ["content", "status"],
            },
        },
    },
    "required": ["todos"],
}
TOOL_REGISTRY.register(Tool(
    name="todo_write",
    description="Create and manage a task list for your current coding session.",
    input_schema=TODO_WRITE_SCHEMA,
    handler=run_todo_write,
))
TOOL_REGISTRY.register(Tool(
    name="memory_read",
    description="Read the project's MEMORY.md (long-term cross-session memory).",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=run_memory_read,
    is_read_only=True,
))
TOOL_REGISTRY.register(Tool(
    name="memory_search",
    description="Search MEMORY.md for lines containing the query (case-insensitive).",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    handler=run_memory_search,
    is_read_only=True,
))
TOOL_REGISTRY.register(Tool(
    name="memory_write",
    description="Append a dated entry to MEMORY.md.",
    input_schema={"type": "object", "properties": {"entry": {"type": "string"}, "heading": {"type": "string"}}, "required": ["entry"]},
    handler=run_memory_write,
))
TOOL_REGISTRY.register(Tool(
    name="load_skill",
    description="Load a skill's body into context by name. Call after seeing the skill index in your system prompt.",
    input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    handler=run_load_skill,
    is_read_only=True,
))
TOOL_REGISTRY.register(Tool(
    name="verify",
    description=(
        "Run the project's init.sh verification pipeline (lint + types + tests). "
        "Use this BEFORE declaring a feature done. Returns pass/fail + tail of output."
    ),
    input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": []},
    handler=run_verify,
    is_read_only=False,
))
TOOL_REGISTRY.register(Tool(
    name="cold_archive",
    description=(
        "Archive a JSON array of turns to gzipped cold storage at "
        ".minicode/cold-storage/ (or `dest` if provided). Use this when a "
        "session grows past the active context budget — older turns can be "
        "moved to disk and rehydrated later via `cold_load`."
    ),
    input_schema={"type": "object", "properties": {"turns_json": {"type": "string"}, "dest": {"type": "string"}, "chunk_size": {"type": "integer"}}, "required": ["turns_json"]},
    handler=run_cold_archive,
    is_read_only=False,
))
TOOL_REGISTRY.register(Tool(
    name="cold_load",
    description=(
        "Rehydrate turns [start_turn, end_turn) from cold storage as a JSON "
        "array string. Use this when the agent needs to reference content "
        "from a prior session or turn range that was archived via cold_archive."
    ),
    input_schema={"type": "object", "properties": {"start_turn": {"type": "integer"}, "end_turn": {"type": "integer"}, "dest": {"type": "string"}}, "required": ["start_turn", "end_turn"]},
    handler=run_cold_load,
    is_read_only=True,
))
TOOL_REGISTRY.register(Tool(
    name="lsp_goto_definition",
    description=(
        "Jump to the definition of the symbol at file:line:character via LSP. "
        "line/character are 0-indexed (LSP spec). Use AFTER grep to find the "
        "candidate position and subtract 1 to convert from grep's 1-indexed "
        "output. Returns `path:line:col` lines (1-indexed for display), "
        "'no definition found', or a fail-closed error string when no LSP "
        "server is configured for the file's extension."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path."},
            "line": {"type": "integer", "description": "0-indexed line (LSP spec)."},
            "character": {"type": "integer", "description": "0-indexed character offset (LSP spec)."},
        },
        "required": ["path", "line", "character"],
    },
    handler=run_lsp_goto_definition,
    is_read_only=True,
))
TOOL_REGISTRY.register(Tool(
    name="lsp_find_references",
    description=(
        "Find every reference to the symbol at file:line:character via LSP. "
        "line/character are 0-indexed (LSP spec). Use AFTER grep to find the "
        "candidate position and subtract 1 to convert from grep's 1-indexed "
        "output. `include_declaration` defaults to true. Returns "
        "`path:line:col` lines, 'no references found', or a fail-closed "
        "error string when no LSP server is configured for the file's "
        "extension."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path."},
            "line": {"type": "integer", "description": "0-indexed line (LSP spec)."},
            "character": {"type": "integer", "description": "0-indexed character offset (LSP spec)."},
            "include_declaration": {"type": "boolean", "description": "Include the declaration site itself (default true)."},
        },
        "required": ["path", "line", "character"],
    },
    handler=run_lsp_find_references,
    is_read_only=True,
))
TOOL_REGISTRY.register(Tool(
    name="lsp_rename_symbol",
    description=(
        "Compute the LSP WorkspaceEdit for renaming the symbol at "
        "file:line:character. line/character are 0-indexed (LSP spec). Use "
        "AFTER grep to find the candidate position and subtract 1 to convert "
        "from grep's 1-indexed output. PL-1 returns the WorkspaceEdit as a "
        "JSON string only (apply is wired up in PL-3). Returns a fail-closed "
        "error string when no LSP server is configured for the file's "
        "extension."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path."},
            "line": {"type": "integer", "description": "0-indexed line (LSP spec)."},
            "character": {"type": "integer", "description": "0-indexed character offset (LSP spec)."},
            "new_name": {"type": "string", "description": "Replacement identifier (non-empty, no whitespace)."},
        },
        "required": ["path", "line", "character", "new_name"],
    },
    handler=run_lsp_rename_symbol,
    is_read_only=False,
))

TOOLS = TOOL_REGISTRY.to_anthropic_schema()
TOOL_HANDLERS = {name: TOOL_REGISTRY.handler_for(name) for name in TOOL_REGISTRY.names()}

SUB_TOOLS: list[ToolParam] = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents with 1-indexed pagination. Output lines are numbered (cat -n style) so any line number can be passed back as `offset` for the next read. A trailing '... (N more lines)' hint indicates further content past the window.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}, "offset": {"type": "integer", "minimum": 1, "description": "1-indexed start line; omit or 0 to read from the beginning"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once. Falls back to difflib fuzzy match (ratio>=0.85) if old_text>=40 chars and not found exactly. Returns error on multiple exact matches (add context).",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "multi_edit", "description": "Apply multiple edits to one file atomically. Each edit is {old_text, new_text}. All-or-nothing: if any edit fails the file is unchanged.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["old_text", "new_text"]}}}, "required": ["path", "edits"]}},
    {"name": "edit_lines", "description": "Replace lines [start_line, end_line] inclusive (1-indexed) with new_content.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}, "new_content": {"type": "string"}}, "required": ["path", "start_line", "end_line", "new_content"]}},
    {"name": "glob", "description": "Find files matching a glob pattern.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "grep", "description": "Search files for a regex or literal pattern. Output is `path:line:content` per match, capped at 200 hits. Use grep BEFORE reading multiple files when looking for symbols or string usages — much cheaper than opening files one by one.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}, "case_insensitive": {"type": "boolean"}}, "required": ["pattern"]}},
    {"name": "web_fetch", "description": "Fetch a URL and return readable text content. Supports http/https. Follows redirects (max 5). HTML is extracted; text/plain and json returned as-is. Default max 50000 chars.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer"}}, "required": ["url"]}},
    {"name": "lsp_goto_definition", "description": "Find the definition of the symbol at file:line:character (0-indexed). Use AFTER grep to find the candidate position. Returns 'path:line:col' lines or 'no definition found'.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "line": {"type": "integer", "minimum": 0}, "character": {"type": "integer", "minimum": 0}}, "required": ["path", "line", "character"]}},
    {"name": "lsp_find_references", "description": "Find all references to the symbol at file:line:character (0-indexed). Use AFTER grep to find the candidate position. Returns 'path:line:col' lines.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "line": {"type": "integer", "minimum": 0}, "character": {"type": "integer", "minimum": 0}, "include_declaration": {"type": "boolean"}}, "required": ["path", "line", "character"]}},
    {"name": "lsp_rename_symbol", "description": "Rename the symbol at file:line:character (0-indexed) to new_name across all files in the workspace. Applies LSP WorkspaceEdit atomically with PreToolUse permission gate. Returns the list of changed files.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "line": {"type": "integer", "minimum": 0}, "character": {"type": "integer", "minimum": 0}, "new_name": {"type": "string"}}, "required": ["path", "line", "character", "new_name"]}},
]

SUB_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "multi_edit": run_multi_edit, "edit_lines": run_edit_lines,
    "glob": run_glob, "grep": run_grep, "web_fetch": run_web_fetch,
    "lsp_goto_definition": run_lsp_goto_definition,
    "lsp_find_references": run_lsp_find_references,
    "lsp_rename_symbol": run_lsp_rename_symbol,
}

SUB_SYSTEM = (
    "你是一个编程子智能体（subagent），由主 agent 通过 `task` 工具委派任务。\n"
    "你的工作目录和主 agent 相同，但上下文独立（不继承主 agent 的对话历史）。\n"
    "规则：\n"
    "- 专注完成委派给你的任务，不要做超出范围的工作\n"
    "- 不要进一步委派（不要调用 task 工具）\n"
    "- 完成后用简洁的摘要返回结果\n"
    "- 如果任务需要多步，列出你做了什么、找到/改了什么\n"
    "- 遇到错误就直接报告，不要重试同一操作超过一次"
)


def extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content)
    return "\n".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text")


def spawn_subagent(description: str, llm_client=None, hooks=None) -> str:
    if llm_client is None or hooks is None:
        from loom.agent.loop import hooks as _hooks
        from loom.agent.loop import llm_client as _llm_client
        llm_client = llm_client or _llm_client
        hooks = hooks or _hooks
    tr = trace_mod.current()
    if tr is not None:
        tr.record("subagent_start", description_len=len(description))
    t0 = time.monotonic()
    hooks.trigger_hooks("AgentStart")
    messages: list[MessageParam] = [{"role": "user", "content": description}]

    turn_count = 0
    tool_call_count = 0
    for _ in range(30):
        turn_count += 1
        response = llm_client.client.messages.create(
            model=llm_client.model, system=SUB_SYSTEM,
            messages=messages, tools=SUB_TOOLS, max_tokens=LLM_CONFIG.max_output_tokens,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_call_count += 1
                blocked = hooks.trigger_hooks("PreToolUse", block)
                if blocked:
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": str(blocked), "is_error": True})
                    continue
                handler = SUB_HANDLERS.get(block.name)
                output = handler(**block.input) if handler else f"Unknown: {block.name}"
                hooks.trigger_hooks("PostToolUse", block, output)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output, "is_error": False})
        messages.append({"role": "user", "content": results})

    result = extract_text(messages[-1]["content"])
    if not result:
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                result = extract_text(msg["content"])
                if result:
                    break
        if not result:
            result = "Subagent stopped after 30 turns without final answer."
    hooks.trigger_hooks("AgentStop", messages)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if tr is not None:
        tr.record("subagent_end", turns=turn_count, tool_calls=tool_call_count,
                  duration_ms=elapsed_ms)
    return f"[done: {turn_count} turns, {tool_call_count} tool calls]\n{result}"


TOOLS.append({
    "name": "task",
    "description": "Launch a subagent to handle a complex subtask. Returns only the final conclusion.",
    "input_schema": {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]},
})


def run_task(description: str) -> str:
    return spawn_subagent(description)


TOOL_HANDLERS["task"] = run_task


def _run_template_subagent(template_name: str, args: dict) -> str:
    """Run a subagent with a specialized template prompt."""
    from loom.agent.subagent_templates import format_subagent_prompt
    try:
        description = format_subagent_prompt(template_name, **args)
    except TypeError as exc:
        return f"Error: {exc}"
    except ValueError as exc:
        return f"Error: {exc}"
    return spawn_subagent(description)


def _make_template_tool(template_name: str):
    from loom.agent.subagent_templates import get_template
    tpl = get_template(template_name)
    if tpl is None:
        return None
    def _handler(**kwargs):
        return _run_template_subagent(template_name, kwargs)
    return Tool(
        name=f"task_{template_name}",
        description=(
            f"Launch a subagent specialized for: {tpl['description']} "
            f"Accepts args: {', '.join(tpl['args_schema'])}."
        ),
        input_schema={
            "type": "object",
            "properties": {a: {"type": "string"} for a in tpl["args_schema"]},
            "required": list(tpl["args_schema"]),
        },
        handler=_handler,
    )


for _tpl_name in ("investigate_code", "refactor_across_files", "fix_failing_test"):
    _tool = _make_template_tool(_tpl_name)
    if _tool is not None:
        TOOL_REGISTRY.register(_tool)
