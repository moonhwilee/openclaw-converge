#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/install-local.sh"

cat <<'EOF'

Converge deploy-local currently performs local install wiring only.
It does not restart Gateway, change slash routing, push, open PRs, or release.
EOF
