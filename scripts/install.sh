#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if command -v uv >/dev/null 2>&1; then
  export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/bountyos-uv-cache}"
  if [[ ! -x .venv/bin/python ]]; then
    uv venv .venv --python python3
  fi
  uv pip sync --python .venv/bin/python requirements.lock
else
  if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
  fi
  .venv/bin/python -m ensurepip --upgrade
  .venv/bin/python -m pip install -r requirements.lock
fi
