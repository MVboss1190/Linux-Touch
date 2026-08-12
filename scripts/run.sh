#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-virtual-phone}"

case "$TARGET" in
  virtual-phone)
    MACHINE="virt"
    CPU="cortex-a72"
    MEMORY="1024"
    CONSOLE="ttyAMA0"
    ;;
  *)
    echo "error: unknown target: $TARGET" >&2
    echo "available targets: virtual-phone" >&2
    exit 2
    ;;
esac

KERNEL="$ROOT_DIR/kernel/linux/arch/arm64/boot/Image"
INITRAMFS="$ROOT_DIR/os/images/initramfs.cpio.gz"

if ! command -v qemu-system-aarch64 >/dev/null 2>&1; then
    echo "error: qemu-system-aarch64 not found" >&2
    exit 1
fi

if [[ ! -f "$KERNEL" ]]; then
    echo "error: kernel not found:" >&2
    echo "       $KERNEL" >&2
    echo "Run ./scripts/build.sh $TARGET first." >&2
    exit 1
fi

if [[ ! -f "$INITRAMFS" ]]; then
    echo "error: initramfs not found:" >&2
    echo "       $INITRAMFS" >&2
    echo "Run ./scripts/build.sh $TARGET first." >&2
    exit 1
fi

echo "================================"
echo "       Linux-Touch Runner"
echo "================================"
echo
echo "Target: $TARGET"
echo "Machine: $MACHINE"
echo "CPU: $CPU"
echo "Memory: ${MEMORY}M"
echo

exec qemu-system-aarch64 \
    -machine "$MACHINE" \
    -cpu "$CPU" \
    -m "$MEMORY" \
    -nographic \
    -kernel "$KERNEL" \
    -initrd "$INITRAMFS" \
    -append "console=$CONSOLE"
