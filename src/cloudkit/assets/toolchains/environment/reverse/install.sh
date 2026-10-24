#!/usr/bin/env bash
set -euo pipefail
reverse_sources="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$reverse_sources/install.py" "$@"
