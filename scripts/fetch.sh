#!/usr/bin/env bash
set -euo pipefail

# Download the sources pinned in sources.yaml and verify their checksums.
#
#   ./scripts/fetch.sh                 # fetch every pinned source
#   ./scripts/fetch.sh linux           # fetch one source
#   ./scripts/fetch.sh --allow-unverified
#
# Downloads land in .cache/sources/, deliberately outside out/ so build
# output stays separable from build inputs. Nothing here is extracted or
# compiled: the builder still consumes prebuilt kernel/BusyBox artifacts.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=lib/tools.sh
source "$ROOT_DIR/scripts/lib/tools.sh"

exec "$(lt_python)" "$ROOT_DIR/scripts/lib/sources.py" fetch "$@"
