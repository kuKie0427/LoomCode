#!/usr/bin/env bash
# scripts/build-tui.sh — build the Go Bubble Tea TUI binary and place it at
# bin/loom-tui so `loom run` (no --plain) will pick it up via the PATH/exists
# check in loom/cli.py.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/tui-go"

mkdir -p "$ROOT/bin"
go build -o "$ROOT/bin/loom-tui" .

echo "Built $ROOT/bin/loom-tui"
