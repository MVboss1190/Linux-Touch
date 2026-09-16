#!/usr/bin/env bash
set -euo pipefail

# Linux-Touch builder: orchestrates the build stages.
#
#   ./scripts/build.sh [device] [--distro <distro>] [--force] [--no-legacy-copy]
#
# The work itself lives in builder/stage-*.sh, one stage per step, each
# runnable on its own. This script only decides the order and reports.
#
#   validated device profile + validated distro profile
#       -> inputs    (kernel, BusyBox and host tools)
#       -> kernel    (copy the prebuilt Image into the output)
#       -> rootfs    (stage the userspace from the distro profile)
#       -> initramfs (pack it reproducibly)
#       -> manifest  (describe what was produced)
#       -> out/<device>-<distro>/

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=lib/profile.sh
source "$ROOT_DIR/scripts/lib/profile.sh"
# shellcheck source=lib/tools.sh
source "$ROOT_DIR/scripts/lib/tools.sh"
# shellcheck source=lib/output.sh
source "$ROOT_DIR/scripts/lib/output.sh"

# [device] [--distro <distro>]; defaults to virtual-phone + busybox-minimal.
lt_parse_arguments "$@"
TARGET="$LT_TARGET_DEVICE"

# Options this script understands beyond the shared ones.
LT_FORCE=0
LEGACY_COPY=1
REMAINING=()
for option in ${LT_EXTRA_ARGS+"${LT_EXTRA_ARGS[@]}"}; do
    case "$option" in
        --force) LT_FORCE=1 ;;
        --no-legacy-copy) LEGACY_COPY=0 ;;
        *) REMAINING+=("$option") ;;
    esac
done
LT_EXTRA_ARGS=(${REMAINING+"${REMAINING[@]}"})
lt_reject_unknown_options "[--force] [--no-legacy-copy]"
export LT_FORCE

# A build is one device profile plus one distro profile.
#   device -> architecture, kernel, boot, runner, device configuration
#   distro -> userspace: init system, root filesystem, BusyBox, overlay
# Each is discovered from disk and validated against its own schema. The
# device is resolved first so the distro can be checked against its
# architecture. Exits 2 on an unknown name, 1 on an invalid profile.
lt_require_device "$TARGET"
lt_require_distro "$LT_TARGET_DISTRO" "$LT_DEVICE_ARCH"

OUT_DIR="$(lt_out_dir "$LT_DEVICE_ID" "$LT_DISTRO_ID")"
SOURCE_DATE_EPOCH="$(lt_source_date_epoch)"
export SOURCE_DATE_EPOCH

STAGE_ARGS=("$LT_DEVICE_ID" "$LT_DISTRO_ID")
if [[ "$LT_FORCE" == "1" ]]; then
    STAGE_ARGS+=("--force")
fi

echo "================================"
echo "       Linux-Touch Builder"
echo "================================"
echo
echo "Target: $TARGET"
echo "Device: $LT_DEVICE_NAME"
echo "Profile: $LT_DEVICE_PROFILE"
echo "Distro: $LT_DISTRO_ID"
echo "Distro profile: $LT_DISTRO_PROFILE"
echo "Output: $OUT_DIR"
echo "SOURCE_DATE_EPOCH: $SOURCE_DATE_EPOCH"
echo

"$ROOT_DIR/builder/stage-inputs.sh" "$LT_DEVICE_ID" "$LT_DISTRO_ID"
"$ROOT_DIR/builder/stage-kernel.sh" "${STAGE_ARGS[@]}"
"$ROOT_DIR/builder/stage-rootfs.sh" "${STAGE_ARGS[@]}"
"$ROOT_DIR/builder/stage-initramfs.sh" "${STAGE_ARGS[@]}"
"$ROOT_DIR/builder/stage-manifest.sh" "$LT_DEVICE_ID" "$LT_DISTRO_ID"

# ------------------------------------------------------------
# Legacy compatibility copy
# ------------------------------------------------------------
#
# os/images/ is no longer the canonical output; out/<device>-<distro>/ is.
# The copy stays only so an older workflow that points at the historical
# path keeps working, and can be switched off with --no-legacy-copy.

IMAGE_DIR="$ROOT_DIR/os/images"
LEGACY_INITRAMFS="$IMAGE_DIR/initramfs.cpio.gz"

if [[ "$LEGACY_COPY" == "1" ]]; then
    mkdir -p "$IMAGE_DIR"
    cp "$OUT_DIR/initramfs.cpio.gz" "$LEGACY_INITRAMFS"
    touch -d "@$SOURCE_DATE_EPOCH" "$LEGACY_INITRAMFS"
fi

echo
echo "================================"
echo "       Build complete"
echo "================================"
echo
echo "Output: $OUT_DIR"
echo "  Image"
echo "  initramfs.cpio.gz"
echo "  manifest.json"
if [[ "$LEGACY_COPY" == "1" ]]; then
    echo
    echo "Legacy copy (not canonical):"
    echo "  $LEGACY_INITRAMFS"
fi
