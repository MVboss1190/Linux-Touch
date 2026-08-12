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

if ! command -v qemu-system-aarch64 >/dev/null 2>&1; then
    echo "error: qemu-system-aarch64 not found" >&2
    exit 1
fi

if ! command -v yq >/dev/null 2>&1; then
    echo "error: yq not found" >&2
    echo "Install it with: sudo pacman -S yq" >&2
    exit 1
fi

KERNEL="$ROOT_DIR/kernel/linux/arch/arm64/boot/Image"
INITRAMFS="$ROOT_DIR/os/images/initramfs.cpio.gz"

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

# ------------------------------------------------------------
# Read QEMU configuration from device profile
# ------------------------------------------------------------

ARCH="$(yq -r '.architecture' "$DEVICE_PROFILE")"
MACHINE="$(yq -r '.platform.qemu.machine' "$DEVICE_PROFILE")"
CPU="$(yq -r '.platform.qemu.cpu' "$DEVICE_PROFILE")"
MEMORY="$(yq -r '.platform.qemu.memory' "$DEVICE_PROFILE")"
CONSOLE="$(yq -r '.platform.qemu.console' "$DEVICE_PROFILE")"

if [[ "$ARCH" != "aarch64" ]]; then
    echo "error: unsupported architecture for this runner: $ARCH" >&2
    exit 1
fi

echo "================================"
echo "       Linux-Touch Runner"
echo "================================"
echo
echo "Target:   $TARGET"
echo "Profile:  $DEVICE_PROFILE"
echo "Machine:  $MACHINE"
echo "CPU:      $CPU"
echo "Memory:   ${MEMORY}M"
echo "Console:  $CONSOLE"
echo

exec qemu-system-aarch64 \
    -machine "$MACHINE" \
    -cpu "$CPU" \
    -m "$MEMORY" \
    -nographic \
    -kernel "$KERNEL" \
    -initrd "$INITRAMFS" \
    -append "console=$CONSOLE"
