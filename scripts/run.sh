#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-virtual-phone}"

# shellcheck source=lib/profile.sh
source "$ROOT_DIR/scripts/lib/profile.sh"

# Discovers the device under devices/, validates it against the profile
# schema and exports LT_DEVICE_* / LT_QEMU_*. Exits 2 on an unknown device,
# 1 on an invalid profile.
lt_require_device "$TARGET"

DEVICE_PROFILE="$LT_DEVICE_PROFILE"

if ! command -v qemu-system-aarch64 >/dev/null 2>&1; then
    echo "error: qemu-system-aarch64 not found" >&2
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
# QEMU configuration from the validated device profile
# ------------------------------------------------------------

ARCH="$LT_DEVICE_ARCH"
MACHINE="$LT_QEMU_MACHINE"
CPU="$LT_QEMU_CPU"
MEMORY="$LT_QEMU_MEMORY"
CONSOLE="$LT_QEMU_CONSOLE"

if [[ "$LT_RUNNER_TYPE" != "qemu" ]]; then
    echo "error: device $TARGET uses runner '$LT_RUNNER_TYPE', not qemu" >&2
    exit 1
fi

if [[ "$ARCH" != "aarch64" ]]; then
    echo "error: unsupported architecture for this runner: $ARCH" >&2
    exit 1
fi

echo "================================"
echo "       Linux-Touch Runner"
echo "================================"
echo
echo "Target:   $TARGET"
echo "Device:   $LT_DEVICE_NAME"
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
