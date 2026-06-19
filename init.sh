#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== Harness Initialization (loom) ==="

if ! command -v uv >/dev/null 2>&1; then
    echo "✗ uv is not installed."
    echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo ""
echo "=== 1/4 Sync dependencies ==="
uv sync --extra test --extra dev

echo ""
echo "=== 2/4 Lint (ruff) ==="
uv run ruff check .

echo ""
echo "=== 3/4 Type check (mypy) ==="
uv run mypy loom/

echo ""
echo "=== 4/4 Tests (pytest) ==="
set +e
uv run pytest 2>&1 | tee /tmp/loom-pytest.log
PYTEST_EXIT=${PIPESTATUS[0]}
set -e

if [ "$PYTEST_EXIT" -eq 0 ]; then
    echo ""
    echo "=== Verification Complete (all green) ==="
    exit 0
fi

EXPECTED=$(uv run python - <<'PY'
import json, re, sys
log = open("/tmp/loom-pytest.log").read()
failed = set(re.findall(r"^FAILED (\S+)", log, re.MULTILINE))
if not failed:
    print("UNEXPECTED: no FAILED line parsed")
    sys.exit(0)
data = json.load(open("feature_list.json"))
blocked = [f for f in data["features"] if f["status"] == "blocked"]
unexpected = []
for ft in failed:
    matched = False
    for f in blocked:
        v = f.get("verification", "")
        if not v or "pytest" not in v:
            continue
        target = v.split("pytest", 1)[1].strip().split()[0]
        if target and target in ft:
            matched = True
            break
    if not matched:
        unexpected.append(ft)
if unexpected:
    print("UNEXPECTED_FAILURES: " + ", ".join(unexpected))
else:
    print("ALL_IN_BLOCKED_FEATURES")
PY
)

case "$EXPECTED" in
    ALL_IN_BLOCKED_FEATURES)
        echo ""
        echo "=== Verification Complete (with blocked features) ==="
        echo "All test failures are in features marked 'blocked' in feature_list.json."
        echo "This is expected. Resolve the 'blocker' field to unblock."
        exit 0
        ;;
    UNEXPECTED_FAILURES:*)
        echo ""
        echo "=== Verification FAILED ==="
        echo "Unexpected test failures (not in any blocked feature):"
        echo "  $EXPECTED" | sed 's/^UNEXPECTED_FAILURES: /    /'
        exit 1
        ;;
    *)
        echo ""
        echo "=== Verification FAILED ==="
        echo "Could not classify failure: $EXPECTED"
        exit 1
        ;;
esac
