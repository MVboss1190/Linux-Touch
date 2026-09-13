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

KERNEL="$ROOT_DIR/kernel/linux/arch/arm64/boot/Image"
BUSYBOX="$ROOT_DIR/busybox/busybox"
ROOTFS="$ROOT_DIR/os/rootfs"
IMAGE_DIR="$ROOT_DIR/os/images"
INITRAMFS="$IMAGE_DIR/initramfs.cpio.gz"

echo "================================"
echo "       Linux-Touch Builder"
echo "================================"
echo
echo "Target: $TARGET"
echo "Device: $LT_DEVICE_NAME"
echo "Profile: $DEVICE_PROFILE"
echo

# ------------------------------------------------------------
# Check prerequisites
# ------------------------------------------------------------

command -v cpio >/dev/null 2>&1 || {
    echo "error: cpio is required" >&2
    exit 1
}

command -v gzip >/dev/null 2>&1 || {
    echo "error: gzip is required" >&2
    exit 1
}

# ------------------------------------------------------------
# Check prebuilt kernel
# ------------------------------------------------------------

if [[ ! -f "$KERNEL" ]]; then
    echo "error: kernel not found:"
    echo "       $KERNEL"
    echo
    echo "Build the ARM64 kernel first."
    exit 1
fi

echo "[1/4] Kernel: OK"

# ------------------------------------------------------------
# Check prebuilt BusyBox
# ------------------------------------------------------------

if [[ ! -x "$BUSYBOX" ]]; then
    echo "error: BusyBox not found or not executable:"
    echo "       $BUSYBOX"
    echo
    echo "Build the ARM64 BusyBox first."
    exit 1
fi

echo "[2/4] BusyBox: OK"

# ------------------------------------------------------------
# Recreate rootfs
# ------------------------------------------------------------

echo "[3/4] Creating root filesystem..."

rm -rf "$ROOTFS"

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
# Build initramfs
# ------------------------------------------------------------

mkdir -p "$IMAGE_DIR"

rm -f "$INITRAMFS"

(
    cd "$ROOTFS"
    find . -print0 |
        cpio --null -ov --format=newc |
        gzip -9 > "$INITRAMFS"
)

echo
echo "[4/4] Initramfs: OK"
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
