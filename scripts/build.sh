#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-virtual-phone}"

case "$TARGET" in
  virtual-phone)
    DEVICE_PROFILE="$ROOT_DIR/devices/virtual-phone/device.yaml"
    ;;
  *)
    echo "error: unknown target: $TARGET" >&2
    echo "available targets: virtual-phone" >&2
    exit 2
    ;;
esac

if [[ ! -f "$DEVICE_PROFILE" ]]; then
  echo "error: missing device profile: $DEVICE_PROFILE" >&2
  exit 1
fi

echo "Linux-Touch build entry point"
echo "target: $TARGET"
echo "profile: $DEVICE_PROFILE"
echo
echo "v0.1 build pipeline is not implemented yet."
echo "Next step: add the AArch64 kernel + rootfs build pipeline."
