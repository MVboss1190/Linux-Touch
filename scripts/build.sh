#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-virtual-phone}"

# shellcheck source=lib/profile.sh
source "$ROOT_DIR/scripts/lib/profile.sh"
# shellcheck source=lib/tools.sh
source "$ROOT_DIR/scripts/lib/tools.sh"

# Discovers the device under devices/, validates it against the profile
# schema and exports LT_DEVICE_* / LT_QEMU_*. Exits 2 on an unknown device,
# 1 on an invalid profile.
lt_require_device "$TARGET"

DEVICE_PROFILE="$LT_DEVICE_PROFILE"

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
echo "Build: $LT_BUILD_ID"
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

mkdir -p \
    "$ROOTFS/bin" \
    "$ROOTFS/sbin" \
    "$ROOTFS/etc" \
    "$ROOTFS/proc" \
    "$ROOTFS/sys" \
    "$ROOTFS/dev" \
    "$ROOTFS/tmp"

cp "$BUSYBOX" "$ROOTFS/bin/busybox"
chmod +x "$ROOTFS/bin/busybox"

# BusyBox applets required by our minimal system.
(
    cd "$ROOTFS/bin"

    for applet in \
        sh \
        mount \
        echo \
        uname \
        cat \
        ls \
        mkdir \
        sleep
    do
        ln -sf busybox "$applet"
    done
)

# ------------------------------------------------------------
# Init
# ------------------------------------------------------------

cat > "$ROOTFS/init" <<'EOF'
#!/bin/sh

mount -t devtmpfs dev /dev
mount -t proc proc /proc
mount -t sysfs sysfs /sys

echo
echo "================================"
echo "       Welcome to Linux-Touch"
echo "================================"
echo
echo "Architecture: $(uname -m)"
echo "Kernel:       $(uname -r)"
echo

exec /bin/sh
EOF

chmod +x "$ROOTFS/init"

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
