#!/usr/bin/env bash
set -euo pipefail

# Stage 4: pack the staged root filesystem into a reproducible initramfs.
#
#   stage-initramfs.sh <device> <distro> [--force]
#
# Inputs:  os/rootfs/, SOURCE_DATE_EPOCH
# Outputs: out/<device>-<distro>/initramfs.cpio.gz
#
# The archiving rules live in scripts/lib/mkinitramfs.sh; this stage only
# decides when to run them.

# shellcheck source=stage-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stage-common.sh"

lt_stage_setup "$@"

ROOTFS="$LT_ROOTFS_DIR"
INITRAMFS="$LT_OUT_DIR_PATH/initramfs.cpio.gz"

if [[ ! -d "$ROOTFS" ]]; then
    echo "error: no staged root filesystem at:" >&2
    echo "       $ROOTFS" >&2
    echo "Run builder/stage-rootfs.sh $LT_DEVICE_ID $LT_DISTRO_ID first." >&2
    exit 1
fi

KEY="$(lt_stage_key "initramfs-v1" "$(lt_tree_fingerprint "$ROOTFS")" "$LT_STAGE_EPOCH")"

if lt_stage_is_current "$LT_OUT_DIR_PATH" initramfs "$KEY" "$INITRAMFS"; then
    lt_stage_announce initramfs "initramfs: cached"
    exit 0
fi

mkdir -p "$LT_OUT_DIR_PATH"
"$ROOT_DIR/scripts/lib/mkinitramfs.sh" "$ROOTFS" "$INITRAMFS" "$LT_STAGE_EPOCH"

lt_stage_record "$LT_OUT_DIR_PATH" initramfs "$KEY"
lt_stage_announce initramfs "initramfs: packed"
