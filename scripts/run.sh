#!/usr/bin/env bash
set -euo pipefail

# Boot a build output in QEMU.
#
#   ./scripts/run.sh [device] [--distro <distro>]
#
# The device profile supplies the runner configuration; the distro selects
# which build output to boot. Artifacts come from that output's
# manifest.json, which is verified before QEMU starts -- no artifact path is
# hardcoded here.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=lib/profile.sh
source "$ROOT_DIR/scripts/lib/profile.sh"
# shellcheck source=lib/tools.sh
source "$ROOT_DIR/scripts/lib/tools.sh"
# shellcheck source=lib/output.sh
source "$ROOT_DIR/scripts/lib/output.sh"

# [device] [--distro <distro>]; defaults to virtual-phone + busybox-minimal.
lt_parse_arguments "$@"
lt_reject_unknown_options
TARGET="$LT_TARGET_DEVICE"

# Both profiles are validated, so a typo in either is caught before QEMU
# starts. Exits 2 on an unknown name, 1 on an invalid profile.
lt_require_device "$TARGET"
lt_require_distro "$LT_TARGET_DISTRO" "$LT_DEVICE_ARCH"

# Checked literally, right next to the exec below that uses it.
if ! command -v qemu-system-aarch64 >/dev/null 2>&1; then
    echo "error: qemu-system-aarch64 not found" >&2
    echo "Install it (pacman -S qemu-system-aarch64, apt install" >&2
    echo "qemu-system-arm), or run ./scripts/check-env.sh for the full list." >&2
    exit 1
fi

# ------------------------------------------------------------
# Locate and verify the build output
# ------------------------------------------------------------
#
# Canonical: out/<device>-<distro>/, described by manifest.json. The
# manifest is checked for this device and distro, every artifact it records
# must exist, and every hash must match before anything boots.

OUT_DIR="$(lt_out_dir "$LT_DEVICE_ID" "$LT_DISTRO_ID")"
BUILD_SOURCE="$OUT_DIR"

status=0
lt_require_build_output "$LT_DEVICE_ID" "$LT_DISTRO_ID" || status=$?

case "$status" in
    0)
        KERNEL="$LT_OUT_KERNEL"
        INITRAMFS="$LT_OUT_INITRAMFS"
        ;;
    3)
        # Legacy fallback: an initramfs produced before out/ became the
        # canonical output. Reached only when no manifest exists at all --
        # a corrupted or foreign build output is an error, never a reason
        # to boot something unverified instead.
        LEGACY_KERNEL="$ROOT_DIR/kernel/linux/arch/arm64/boot/Image"
        LEGACY_INITRAMFS="$ROOT_DIR/os/images/initramfs.cpio.gz"

        if [[ ! -f "$LEGACY_KERNEL" || ! -f "$LEGACY_INITRAMFS" ]]; then
            echo "error: no build output for $LT_DEVICE_ID / $LT_DISTRO_ID" >&2
            echo "       expected $OUT_DIR/manifest.json" >&2
            echo "Run ./scripts/build.sh $TARGET --distro $LT_DISTRO_ID first." >&2
            exit 1
        fi

        echo "warning: no manifest in $OUT_DIR" >&2
        echo "warning: falling back to the legacy os/images layout; these" >&2
        echo "         artifacts are unverified. Rebuild to use out/." >&2
        KERNEL="$LEGACY_KERNEL"
        INITRAMFS="$LEGACY_INITRAMFS"
        BUILD_SOURCE="legacy os/images (unverified)"
        ;;
    *)
        exit "$status"
        ;;
esac

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
echo "Profile:  $LT_DEVICE_PROFILE"
echo "Distro:   $LT_DISTRO_ID"
echo "Machine:  $MACHINE"
echo "CPU:      $CPU"
echo "Memory:   ${MEMORY}M"
echo "Console:  $CONSOLE"
echo "Build:    $BUILD_SOURCE"
echo "Kernel:    $KERNEL"
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
