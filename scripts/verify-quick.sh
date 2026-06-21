#!/bin/bash
# scripts/verify-quick.sh — fast verification for the dev cycle.
#
# Runs lint + types (always) and a smart subset of tests + eval based on
# `git diff`. Skips visual snapshot tests by default. Use `./init.sh` for
# full verification before declaring a feature done.
#
# Usage:
#   scripts/verify-quick.sh                      # infer scope from git diff
#   scripts/verify-quick.sh tests/test_tools.py  # run a specific test file
#   scripts/verify-quick.sh --no-skip-snapshot   # also run snapshot tests
#   EVAL_FILTER=read-file scripts/verify-quick.sh  # also filter eval cases
#
# Wall-time target: <10s for a non-UI single-file change (was 60-100s).

set -e

cd "$(dirname "$0")/.."

SKIP_SNAPSHOT=1
EXTRA_PYTEST_ARGS=()
EXPLICIT_TESTS=()
EVAL_FILTER_OVERRIDE="${EVAL_FILTER:-}"

for arg in "$@"; do
    case "$arg" in
        --no-skip-snapshot) SKIP_SNAPSHOT=0 ;;
        --help|-h)
            head -12 "$0" | tail -10
            exit 0
            ;;
        -*) EXTRA_PYTEST_ARGS+=("$arg") ;;
        *)  EXPLICIT_TESTS+=("$arg") ;;
    esac
done

echo "=== verify-quick: lint + types (always) ==="
uv run ruff check .
uv run mypy loom/

echo ""
echo "=== verify-quick: pytest (smart subset) ==="

# Snapshot exclusion via -m flag (relies on conftest auto-marking snap_compare tests).
MARKER_ARGS=()
if [ "$SKIP_SNAPSHOT" = "1" ]; then
    MARKER_ARGS=(-m "not snapshot")
fi

if [ ${#EXPLICIT_TESTS[@]} -gt 0 ]; then
    uv run pytest "${EXPLICIT_TESTS[@]}" -q "${MARKER_ARGS[@]}" "${EXTRA_PYTEST_ARGS[@]}"
else
    # Auto-infer: git diff → loom/X/Y.py → tests/test_Y.py
    changed=$(git diff --name-only HEAD 2>/dev/null | grep -E '^loom/.*\.py$' | sort -u || true)
    untracked=$(git ls-files --others --exclude-standard 2>/dev/null | grep -E '^loom/.*\.py$' | sort -u || true)
    all_changed=$(printf "%s\n%s\n" "$changed" "$untracked" | grep -v '^$' | sort -u)

    test_files=()
    eval_case_files=()
    for f in $all_changed; do
        if [[ "$f" == loom/eval/cases/* ]]; then
            eval_case_files+=("$f")
        else
            base=$(basename "$f" .py)
            if [ -f "tests/test_${base}.py" ]; then
                test_files+=("tests/test_${base}.py")
            fi
        fi
    done

    # Dedup
    test_files=($(printf "%s\n" "${test_files[@]}" | sort -u | grep -v '^$' || true))

    if [ ${#test_files[@]} -gt 0 ]; then
        echo "Scope: ${test_files[*]}"
        uv run pytest "${test_files[@]}" -q "${MARKER_ARGS[@]}" "${EXTRA_PYTEST_ARGS[@]}"
    else
        echo "No test files inferred (changed files: ${all_changed:-none}) — running smoke set"
        uv run pytest tests/test_tools.py tests/test_agent_loop.py -q "${MARKER_ARGS[@]}"
    fi
fi

# Eval subset
echo ""
echo "=== verify-quick: eval (filtered) ==="
if [ -n "$EVAL_FILTER_OVERRIDE" ]; then
    uv run python -m loom.cli eval --filter "$EVAL_FILTER_OVERRIDE"
elif [ ${#eval_case_files[@]} -gt 0 ]; then
    names=$(grep -hE '^\s+name\s*=\s*"[^"]+"' "${eval_case_files[@]}" 2>/dev/null \
        | sed -E 's/^[[:space:]]*name[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/' | sort -u)
    if [ -n "$names" ]; then
        echo "Eval cases found in changed files:"
        echo "$names" | sed 's/^/  /'
        for n in $names; do
            uv run python -m loom.cli eval --filter "$n" 2>&1 | tail -2
        done
    else
        echo "(could not infer case names; run \`loom eval\` manually)"
    fi
else
    echo "(no eval cases changed; skipping eval)"
fi

echo ""
echo "=== verify-quick: done. For full verification run ./init.sh ==="
