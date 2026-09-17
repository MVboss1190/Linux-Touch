#!/usr/bin/env bash
set -euo pipefail

# Report whether this machine can build and boot Linux-Touch.
#
#   ./scripts/check-env.sh                    # everything
#   ./scripts/check-env.sh --group core       # just one group
#   ./scripts/check-env.sh --strict           # also fail on recommended items
#   ./scripts/check-env.sh --json             # machine readable
#
# Exits non-zero when something required is missing, naming the package to
# install. The builder checks against the same list in
# scripts/lib/requirements.py, so this cannot disagree with a real build.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=lib/tools.sh
source "$ROOT_DIR/scripts/lib/tools.sh"

exec "$(lt_python)" "$ROOT_DIR/scripts/lib/requirements.py" check "$@"
