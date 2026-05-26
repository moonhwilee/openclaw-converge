#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${OPENCLAW_CONVERGE_INSTALL_ROOT:-"$HOME/.openclaw/converge"}"
BIN_DIR="${OPENCLAW_CONVERGE_BIN_DIR:-"$HOME/.openclaw/bin"}"
PLUGIN_DIR="${OPENCLAW_CONVERGE_PLUGIN_DIR:-"$HOME/.openclaw/plugin-sources/openclaw-converge"}"

assert_managed_root() {
  local path="$1"
  local expected_basename="$2"
  local label="$3"
  if [[ "$(basename "$path")" != "$expected_basename" ]]; then
    echo "Refusing to clean $label that is not a managed $expected_basename directory: $path" >&2
    exit 2
  fi
}

assert_managed_root "$INSTALL_ROOT" "converge" "install root"
assert_managed_root "$PLUGIN_DIR" "openclaw-converge" "plugin source root"

mkdir -p "$INSTALL_ROOT" "$BIN_DIR" "$PLUGIN_DIR"
SOURCE_REAL="$(cd "$SOURCE_ROOT" && pwd -P)"
INSTALL_REAL="$(cd "$INSTALL_ROOT" && pwd -P)"
PLUGIN_REAL="$(cd "$PLUGIN_DIR" && pwd -P)"
if [[ "$SOURCE_REAL" == "$INSTALL_REAL" || "$SOURCE_REAL" == "$PLUGIN_REAL" ]]; then
  echo "Refusing to run install-local from a managed install target: $SOURCE_REAL" >&2
  exit 2
fi

copy_runtime_tree() {
  local target_root="$1"
  rm -rf "$target_root/converge" "$target_root/bin" "$target_root/scripts" "$target_root/prompts" "$target_root/tests"
  mkdir -p "$target_root/bin" "$target_root/scripts" "$target_root/prompts"

  cp "$SOURCE_ROOT/README.md" "$target_root/README.md"
  cp -R "$SOURCE_ROOT/converge" "$target_root/"
  cp "$SOURCE_ROOT/bin/converge" "$target_root/bin/converge"
  cp "$SOURCE_ROOT/scripts/converge_watchdog_runner.py" "$target_root/scripts/converge_watchdog_runner.py"
  cp "$SOURCE_ROOT/scripts/install-local.sh" "$target_root/scripts/install-local.sh"
  cp "$SOURCE_ROOT/scripts/deploy-local.sh" "$target_root/scripts/deploy-local.sh"
  cp "$SOURCE_ROOT/prompts/converge-watchdog.md" "$target_root/prompts/converge-watchdog.md"
  cp "$SOURCE_ROOT/package.json" "$target_root/package.json"
  cp "$SOURCE_ROOT/openclaw.plugin.json" "$target_root/openclaw.plugin.json"

  find "$target_root" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
  find "$target_root" -type f -name '*.pyc' -delete
  chmod 0755 "$target_root/bin/converge" "$target_root/scripts/converge_watchdog_runner.py" "$target_root/scripts/install-local.sh" "$target_root/scripts/deploy-local.sh"
}

copy_runtime_tree "$INSTALL_ROOT"
copy_runtime_tree "$PLUGIN_DIR"

cat > "$BIN_DIR/converge" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$INSTALL_ROOT:\${PYTHONPATH:-}"
export OPENCLAW_CONVERGE_SOURCE_ROOT="$INSTALL_ROOT"
exec python3 -m converge.cli "\$@"
EOF
chmod 0755 "$BIN_DIR/converge"

cat <<EOF
Converge installed locally.

Installed CLI:
  $BIN_DIR/converge

Installed package:
  $INSTALL_ROOT

Installed plugin source:
  $PLUGIN_DIR

No Gateway restart was performed.
No slash routing was changed.

Post-install verification:
  $BIN_DIR/converge validate --sample-docs
  $BIN_DIR/converge scan --json
  OPENCLAW_CONVERGE_BIN="$BIN_DIR/converge" "$INSTALL_ROOT/scripts/converge_watchdog_runner.py" --json
EOF
