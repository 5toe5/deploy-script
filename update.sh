#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v git >/dev/null 2>&1; then
	echo "[update] ERROR: 'git' is not installed" >&2
	exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
	echo "[update] ERROR: 'uv' is not installed" >&2
	exit 1
fi

if git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	echo "[update] Pulling latest deploy-script..." >&2
	git -C "$SCRIPT_DIR" pull --ff-only
fi

exec uv run "$SCRIPT_DIR/update-robot-env.py" "$@"
