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

# A build is one device profile plus one distro profile.
#   device -> architecture, kernel, boot, runner, device configuration
#   distro -> userspace: init system, root filesystem, BusyBox, overlay
# Each is discovered from disk and validated against its own schema. The
# device is resolved first so the distro can be checked against its
# architecture. Exits 2 on an unknown name, 1 on an invalid profile.
lt_require_device "$TARGET"
lt_require_distro "$LT_TARGET_DISTRO" "$LT_DEVICE_ARCH"

DEVICE_PROFILE="$LT_DEVICE_PROFILE"
DISTRO_PROFILE="$LT_DISTRO_PROFILE"

# The output directory is keyed by both axes.
LT_BUILD_ID="$LT_DISTRO_ID"

KERNEL="$ROOT_DIR/kernel/linux/arch/arm64/boot/Image"
BUSYBOX="$ROOT_DIR/busybox/busybox"
ROOTFS="$ROOT_DIR/os/rootfs"
SOURCES="$ROOT_DIR/sources.yaml"

# Generated artifacts live in out/<device>-<build>/. os/images/ is kept as a
# compatibility copy so existing habits and run.sh keep working.
OUT_DIR="$ROOT_DIR/out/$LT_DEVICE_ID-$LT_BUILD_ID"
INITRAMFS="$OUT_DIR/initramfs.cpio.gz"
MANIFEST="$OUT_DIR/manifest.json"
IMAGE_DIR="$ROOT_DIR/os/images"
COMPAT_INITRAMFS="$IMAGE_DIR/initramfs.cpio.gz"

# Reproducible builds need a fixed timestamp. Honour the environment, else
# fall back to the constant pinned in sources.yaml -- never the wall clock.
if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
    if [[ ! "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]]; then
        echo "error: SOURCE_DATE_EPOCH must be a non-negative integer" >&2
        exit 1
    fi
else
    SOURCE_DATE_EPOCH="$("$(lt_python)" "$ROOT_DIR/scripts/lib/sources.py" epoch)"
fi
export SOURCE_DATE_EPOCH

echo "================================"
echo "       Linux-Touch Builder"
echo "================================"
echo
echo "Target: $TARGET"
echo "Device: $LT_DEVICE_NAME"
echo "Profile: $DEVICE_PROFILE"
echo "Distro: $LT_DISTRO_ID"
echo "Distro profile: $DISTRO_PROFILE"
echo "SOURCE_DATE_EPOCH: $SOURCE_DATE_EPOCH"
echo

# ------------------------------------------------------------
# Check build inputs (non-destructive)
# ------------------------------------------------------------

if [[ ! -f "$KERNEL" ]]; then
    echo "error: kernel not found:" >&2
    echo "       $KERNEL" >&2
    echo >&2
    echo "Build the ARM64 kernel first." >&2
    exit 1
fi

echo "[1/5] Kernel: OK"

if [[ ! -x "$BUSYBOX" ]]; then
    echo "error: BusyBox not found or not executable:" >&2
    echo "       $BUSYBOX" >&2
    echo >&2
    echo "Build the ARM64 BusyBox first." >&2
    exit 1
fi

echo "[2/5] BusyBox: OK"

# ------------------------------------------------------------
# Check host tools before any destructive work
# ------------------------------------------------------------

lt_require_build_tools
lt_report_toolchain

echo "[3/5] Host tools: OK"

# ------------------------------------------------------------
# Recreate rootfs
# ------------------------------------------------------------

echo "[4/5] Creating root filesystem..."

# Guarded: refuses any path outside os/rootfs, os/images and out/.
lt_safe_rm_rf "$ROOTFS"

# Directory skeleton, from the distro profile.
for directory in $LT_DISTRO_DIRECTORIES; do
    mkdir -p "$ROOTFS/$directory"
done

# Bootstrap, from the distro profile. 'busybox' installs the prebuilt
# binary and links its applets beside it; 'none' stages nothing.
if [[ "$LT_DISTRO_BOOTSTRAP_METHOD" == "busybox" ]]; then
    cp "$BUSYBOX" "$ROOTFS/$LT_DISTRO_BUSYBOX_PATH"
    chmod +x "$ROOTFS/$LT_DISTRO_BUSYBOX_PATH"

    busybox_name="$(basename "$LT_DISTRO_BUSYBOX_PATH")"
    (
        cd "$ROOTFS/$(dirname "$LT_DISTRO_BUSYBOX_PATH")"
        for applet in $LT_DISTRO_APPLETS; do
            ln -sf "$busybox_name" "$applet"
        done
    )
fi

# Overlay, from the distro profile. Copied last so it wins, and with modes
# preserved so /init keeps its executable bit. This is where /init lives.
if [[ -n "$LT_DISTRO_OVERLAY" ]]; then
    if [[ ! -d "$LT_DISTRO_OVERLAY" ]]; then
        echo "error: distro overlay directory not found:" >&2
        echo "       $LT_DISTRO_OVERLAY" >&2
        exit 1
    fi
    cp -a "$LT_DISTRO_OVERLAY/." "$ROOTFS/"
fi

if [[ ! -x "$ROOTFS/init" ]]; then
    echo "error: $LT_DISTRO_ID produced no executable /init" >&2
    echo "       check distros/$LT_DISTRO_ID/overlay/init and its mode" >&2
    exit 1
fi

# ------------------------------------------------------------
# Build a reproducible initramfs
# ------------------------------------------------------------

lt_safe_rm_rf "$OUT_DIR"
mkdir -p "$OUT_DIR" "$IMAGE_DIR"

"$ROOT_DIR/scripts/lib/mkinitramfs.sh" "$ROOTFS" "$INITRAMFS" "$SOURCE_DATE_EPOCH"

# Compatibility copy for the historical location.
cp "$INITRAMFS" "$COMPAT_INITRAMFS"
touch -d "@$SOURCE_DATE_EPOCH" "$COMPAT_INITRAMFS"

echo "[5/5] Initramfs: OK"

# ------------------------------------------------------------
# Build manifest
# ------------------------------------------------------------

"$(lt_python)" "$ROOT_DIR/scripts/lib/manifest.py" \
    --root "$ROOT_DIR" \
    --device-profile "$DEVICE_PROFILE" \
    --distro-profile "$DISTRO_PROFILE" \
    --build-id "$LT_BUILD_ID" \
    --source-date-epoch "$SOURCE_DATE_EPOCH" \
    --sources "$SOURCES" \
    --input "kernel_image=$KERNEL" \
    --input "busybox_binary=$BUSYBOX" \
    --artifact "initramfs=$INITRAMFS" \
    --output "$MANIFEST" >/dev/null

echo
echo "================================"
echo "       Build complete"
echo "================================"
echo
echo "Kernel:"
echo "  $KERNEL"
echo
echo "Initramfs:"
echo "  $INITRAMFS"
echo "  $COMPAT_INITRAMFS (compatibility copy)"
echo
echo "Manifest:"
echo "  $MANIFEST"
