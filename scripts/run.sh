#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-virtual-phone}"

case "$TARGET" in
  virtual-phone)
    ;;
  *)
    echo "error: unknown target: $TARGET" >&2
    echo "available targets: virtual-phone" >&2
    exit 2
    ;;
esac

echo "Linux-Touch run entry point"
echo "target: $TARGET"
echo
echo "v0.1 QEMU runner is not implemented yet."
echo "Next step: add the generated kernel/rootfs paths and QEMU command line."
