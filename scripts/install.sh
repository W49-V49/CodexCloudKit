#!/usr/bin/env bash
set -euo pipefail
toolkit_source=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
toolkit_runtime=${CLOUDKIT_RUNTIME_ROOT:-/workspace/external-tools/codexcloudkit}
python3 -m venv "$toolkit_runtime/venv"
"$toolkit_runtime/venv/bin/python" -m pip install "$toolkit_source"
"$toolkit_runtime/venv/bin/cloudkit" setup --runtime-root "$toolkit_runtime"
