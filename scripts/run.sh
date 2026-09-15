#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=lib/profile.sh
source "$ROOT_DIR/scripts/lib/profile.sh"
# shellcheck source=lib/tools.sh
source "$ROOT_DIR/scripts/lib/tools.sh"

# [device] [--distro <distro>]; defaults to virtual-phone + busybox-minimal.
lt_parse_arguments "$@"
TARGET="$LT_TARGET_DEVICE"

# The device supplies the runner configuration; the distro only selects
# which build output to boot. Both are validated, so a typo in either is
# caught before QEMU starts. Exits 2 on an unknown name, 1 on an invalid
# profile.
lt_require_device "$TARGET"
lt_require_distro "$LT_TARGET_DISTRO" "$LT_DEVICE_ARCH"

DEVICE_PROFILE="$LT_DEVICE_PROFILE"
LT_BUILD_ID="$LT_DISTRO_ID"

if ! command -v qemu-system-aarch64 >/dev/null 2>&1; then
    echo "error: qemu-system-aarch64 not found" >&2
    exit 1
fi

KERNEL="$ROOT_DIR/kernel/linux/arch/arm64/boot/Image"

# Prefer the build output directory, fall back to the historical location
# so an initramfs built before this change still boots.
INITRAMFS="$ROOT_DIR/out/$LT_DEVICE_ID-$LT_BUILD_ID/initramfs.cpio.gz"
if [[ ! -f "$INITRAMFS" ]]; then
    INITRAMFS="$ROOT_DIR/os/images/initramfs.cpio.gz"
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
echo "Distro:   $LT_DISTRO_ID"
echo "Machine:  $MACHINE"
echo "CPU:      $CPU"
echo "Memory:   ${MEMORY}M"
echo "Console:  $CONSOLE"
echo "Initramfs: $INITRAMFS"
echo

exec qemu-system-aarch64 \
    -machine "$MACHINE" \
    -cpu "$CPU" \
    -m "$MEMORY" \
    -nographic \
    -kernel "$KERNEL" \
    -initrd "$INITRAMFS" \
    -append "console=$CONSOLE"
