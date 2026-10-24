#!/usr/bin/env bash
set -euo pipefail
design_sources="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "$design_sources/install.py"
