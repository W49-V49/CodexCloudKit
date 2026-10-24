#!/usr/bin/env bash
set -euo pipefail
language_source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
language_runtime_root="${CLOUDKIT_LANGUAGE_ROOT:-${CLOUDKIT_RUNTIME_ROOT:?}/languages}"
mkdir -p "$language_runtime_root/node" "$language_runtime_root/bin" "$language_runtime_root/uv-cache"
python3 "$language_source_root/tools/languages/managed.py" --root "$language_runtime_root" --preflight \
  --executable-file "$language_source_root/tools/languages/language_tools.py" "$language_runtime_root/language-tools" \
  --file "$language_source_root/environment/languages/nvim-init.lua" "$language_runtime_root/nvim-init.lua" \
  --file "$language_source_root/environment/languages/package.json" "$language_runtime_root/node/package.json" \
  --file "$language_source_root/environment/languages/package-lock.json" "$language_runtime_root/node/package-lock.json" \
  --file "$language_source_root/environment/languages/requirements.lock" "$language_runtime_root/python/requirements.lock"
python3 "$language_source_root/tools/languages/install.py" --root "$language_runtime_root"
cp "$language_source_root/environment/languages/package.json" "$language_runtime_root/node/package.json"
cp "$language_source_root/environment/languages/package-lock.json" "$language_runtime_root/node/package-lock.json"
language_lock_sha="$(sha256sum "$language_runtime_root/node/package-lock.json" | cut -d ' ' -f 1)"
if [ ! -f "$language_runtime_root/node/installed-lock.sha256" ] ||
   [ "$(cat "$language_runtime_root/node/installed-lock.sha256")" != "$language_lock_sha" ] ||
   [ ! -x "$language_runtime_root/node/node_modules/.bin/tsc" ] ||
   [ ! -x "$language_runtime_root/node/node_modules/.bin/typescript-language-server" ]; then
  npm ci --prefix "$language_runtime_root/node" --ignore-scripts --no-audit --fund=false --cache "$language_runtime_root/npm-cache"
  printf '%s\n' "$language_lock_sha" > "$language_runtime_root/node/installed-lock.sha256"
fi
if ! command -v uv >/dev/null; then
  echo 'The shared installer requires the existing verified uv CLI on PATH.' >&2
  exit 1
fi
if [ ! -x "$language_runtime_root/python/bin/python" ]; then
  uv --cache-dir "$language_runtime_root/uv-cache" venv "$language_runtime_root/python" --python python3
fi
uv pip sync --python "$language_runtime_root/python/bin/python" --require-hashes "$language_source_root/environment/languages/requirements.lock" --cache-dir "$language_runtime_root/uv-cache"
cp "$language_source_root/environment/languages/requirements.lock" "$language_runtime_root/python/requirements.lock"
language_gopls_version="$(python3 - "$language_source_root" <<'PY'
import json,sys
from pathlib import Path
print(json.loads((Path(sys.argv[1])/'environment/languages/toolchain.lock.json').read_text())['go_packages']['gopls']['version'])
PY
)"
if [ ! -x "$language_runtime_root/bin/gopls" ] ||
   ! "$language_runtime_root/bin/go" version -m "$language_runtime_root/bin/gopls" | rg -q "golang.org/x/tools/gopls[[:space:]]+$language_gopls_version"; then
  GOBIN="$language_runtime_root/bin" GOPATH="$language_runtime_root/go-work" GOCACHE="$language_runtime_root/go-cache" \
  GOMODCACHE="$language_runtime_root/go-mod" GOTOOLCHAIN=local GOSUMDB=sum.golang.org GOPROXY=https://proxy.golang.org,direct \
    "$language_runtime_root/bin/go" install "golang.org/x/tools/gopls@$language_gopls_version"
fi
python3 "$language_source_root/tools/languages/managed.py" --root "$language_runtime_root" \
  --executable-file "$language_source_root/tools/languages/language_tools.py" "$language_runtime_root/language-tools" \
  --file "$language_source_root/environment/languages/nvim-init.lua" "$language_runtime_root/nvim-init.lua" \
  --file "$language_source_root/environment/languages/package.json" "$language_runtime_root/node/package.json" \
  --file "$language_source_root/environment/languages/package-lock.json" "$language_runtime_root/node/package-lock.json" \
  --file "$language_source_root/environment/languages/requirements.lock" "$language_runtime_root/python/requirements.lock"
printf '%s\n' 'Portable language toolchain installed; use language-tools run -- <command>. Application dependencies remain in their own project.'
