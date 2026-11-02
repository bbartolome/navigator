#!/usr/bin/env bash
set -eu
D="${CLAUDE_PLUGIN_DATA:?}"; R="${CLAUDE_PLUGIN_ROOT:?}"
V="$D/venv"; STAMP="$D/pyproject.stamp"
log(){ echo "[navigator] $*" >&2; }
if [ ! -x "$V/bin/navigator-mcp" ] || ! cmp -s "$R/server/pyproject.toml" "$STAMP"; then
  UV="$(command -v uv || true)"
  if [ -z "$UV" ] && [ ! -x "$D/uv-bin/uv" ]; then
    curl -LsSf "https://astral.sh/uv/${NAVIGATOR_UV_VERSION:?pin required}/install.sh" \
      | UV_UNMANAGED_INSTALL="$D/uv-bin" sh >&2 || log "uv install failed; falling back to venv"
  fi
  [ -z "$UV" ] && [ -x "$D/uv-bin/uv" ] && UV="$D/uv-bin/uv"
  rm -rf "$V"
  if [ -n "$UV" ]; then
    "$UV" venv "$V" >&2 && "$UV" pip install --python "$V/bin/python" "$R/server" >&2
  else
    python3 -c 'import sys;sys.exit(sys.version_info<(3,10))' || { log "need Python >= 3.10"; exit 1; }
    python3 -m venv "$V" >&2 2>&1 || { rm -rf "$V"; python3 -m venv --without-pip "$V" >&2
      curl -sSf https://bootstrap.pypa.io/get-pip.py | "$V/bin/python" >&2; }
    "$V/bin/python" -m pip install "$R/server" >&2
  fi
  cp "$R/server/pyproject.toml" "$STAMP"
fi
exec "$V/bin/navigator-mcp" "$@"
