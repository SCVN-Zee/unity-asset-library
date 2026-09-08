#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -s)/$(uname -m)" != Darwin/arm64 ]]; then
  echo "Packaging currently requires macOS arm64." >&2
  exit 1
fi
command -v uv >/dev/null || { echo "Install uv: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }
# Keep this pin in sync with package.json's extraResources source.
uv python install 3.12.11 --install-dir .build/python --no-bin
# This is our private staging runtime, never the developer's system Python.
uv pip sync requirements.txt --require-hashes --only-binary :all: --break-system-packages \
  --python .build/python/cpython-3.12.11-macos-aarch64-none/bin/python3
