#!/usr/bin/env bash
set -euo pipefail

# Stage 3: stage the root filesystem from the distro profile.
#
#   stage-rootfs.sh <device> <distro> [--force]
#
# Inputs:  distro profile (directories, bootstrap, applets, overlay), and
#          either the pinned BusyBox source or LT_BUSYBOX_INPUT
# Outputs: os/rootfs/ (a staging tree, not a published artifact)
#
# BusyBox is userspace, so building it belongs to this stage. Either the
# pinned source is compiled statically for aarch64, or a prebuilt binary is
# used; which one happened is announced and recorded.
#
# The tree is rebuilt from scratch every time it is not cached, so a rerun
# can never inherit a file from a previous distro. Removal is guarded.

# shellcheck source=stage-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stage-common.sh"

lt_stage_setup "$@"

ROOTFS="$LT_ROOTFS_DIR"

BUSYBOX_MODE="none"
if [[ "$LT_DISTRO_BOOTSTRAP_METHOD" == "busybox" ]]; then
    BUSYBOX_MODE="$(lt_resolve_build_mode busybox "$LT_DISTRO_BUILD_SOURCE" "$LT_BUSYBOX_INPUT")"
fi

if [[ "$BUSYBOX_MODE" == "source" ]]; then
    BUSYBOX_KEY="$(lt_stage_key \
        "busybox-source-v1" \
        "$LT_DISTRO_BUILD_SOURCE" \
        "$LT_DISTRO_BUILD_DEFCONFIG" \
        "$LT_DISTRO_BUILD_STATIC" \
        "$(lt_file_fingerprint "$LT_DISTRO_DIR/$LT_DISTRO_BUILD_FRAGMENT")" \
        "$(lt_resolve_cross_compile "$LT_DEVICE_ARCH")")"
else
    BUSYBOX_KEY="$(lt_stage_key "busybox-prebuilt-v1" "$(lt_file_fingerprint "$LT_BUSYBOX_INPUT")")"
fi

KEY="$(lt_stage_key \
    "rootfs-v2" \
    "$(lt_file_fingerprint "$LT_DISTRO_PROFILE")" \
    "$(lt_tree_fingerprint "$LT_DISTRO_OVERLAY")" \
    "$BUSYBOX_MODE" \
    "$BUSYBOX_KEY" \
    "$LT_DISTRO_BOOTSTRAP_METHOD" \
    "$LT_DISTRO_BUSYBOX_PATH" \
    "$LT_DISTRO_DIRECTORIES" \
    "$LT_DISTRO_APPLETS" \
    "$LT_STAGE_EPOCH")"

if lt_stage_is_current "$LT_OUT_DIR_PATH" rootfs "$KEY" "$ROOTFS" "$ROOTFS/init" &&
    lt_is_json_file "$LT_OUT_DIR_PATH/.build/busybox.json"; then
    lt_stage_announce rootfs "root filesystem: cached ($BUSYBOX_MODE busybox)"
    exit 0
fi

# Obtain the BusyBox binary before touching the staging tree.
BUSYBOX_BINARY=""
if [[ "$BUSYBOX_MODE" == "source" ]]; then
    lt_stage_announce rootfs "building BusyBox from the pinned source"
    lt_build_busybox "$LT_OUT_DIR_PATH"
    BUSYBOX_BINARY="$LT_BUILT_BUSYBOX_BINARY"
elif [[ "$BUSYBOX_MODE" == "prebuilt" ]]; then
    if [[ ! -x "$LT_BUSYBOX_INPUT" ]]; then
        echo "error: BusyBox not found or not executable:" >&2
        echo "       $LT_BUSYBOX_INPUT" >&2
        exit 1
    fi
    BUSYBOX_BINARY="$LT_BUSYBOX_INPUT"
    mkdir -p "$LT_OUT_DIR_PATH"
    lt_write_component_record "$LT_OUT_DIR_PATH" busybox \
        --provenance prebuilt-unverified \
        --binary "$BUSYBOX_BINARY" \
        --prebuilt-path "$(realpath --relative-to="$LT_ROOT_DIR" "$BUSYBOX_BINARY" 2>/dev/null || echo "$BUSYBOX_BINARY")"
fi

# Guarded: refuses any path outside os/rootfs, os/images and out/.
lt_safe_rm_rf "$ROOTFS"

# Directory skeleton, from the distro profile.
for directory in $LT_DISTRO_DIRECTORIES; do
    mkdir -p "$ROOTFS/$directory"
done

# Bootstrap, from the distro profile. 'busybox' installs the prebuilt
# binary and links its applets beside it; 'none' stages nothing.
if [[ "$LT_DISTRO_BOOTSTRAP_METHOD" == "busybox" ]]; then
    cp "$BUSYBOX_BINARY" "$ROOTFS/$LT_DISTRO_BUSYBOX_PATH"
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

mkdir -p "$LT_OUT_DIR_PATH"
lt_stage_record "$LT_OUT_DIR_PATH" rootfs "$KEY"
lt_stage_announce rootfs "root filesystem: staged ($BUSYBOX_MODE busybox)"
