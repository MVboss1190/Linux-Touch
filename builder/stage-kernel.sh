#!/usr/bin/env bash
set -euo pipefail

# Stage 2: produce the kernel image for the build output.
#
#   stage-kernel.sh <device> <distro> [--force]
#
# Inputs:  the device profile's kernel block and the pinned source, or
#          LT_KERNEL_INPUT when falling back to a prebuilt image
# Outputs: out/<device>-<distro>/Image
#
# Either the pinned kernel is compiled from source, or a prebuilt image is
# copied. Which one happened is announced and recorded in the manifest, so
# a copied image is never mistaken for one this project built.

# shellcheck source=stage-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stage-common.sh"

lt_stage_setup "$@"

IMAGE="$LT_OUT_DIR_PATH/Image"

MODE="$(lt_resolve_build_mode kernel "$LT_DEVICE_KERNEL_SOURCE" "$LT_KERNEL_INPUT")"

if [[ "$MODE" == "source" ]]; then
    KEY="$(lt_stage_key \
        "kernel-source-v1" \
        "$LT_DEVICE_KERNEL_SOURCE" \
        "$(lt_file_fingerprint "$LT_DEVICE_PROFILE")" \
        "$(lt_file_fingerprint "$LT_ROOT_DIR/$LT_DEVICE_KERNEL_FRAGMENTS")" \
        "$(lt_resolve_cross_compile "$LT_DEVICE_ARCH")" \
        "$LT_STAGE_EPOCH")"
else
    KEY="$(lt_stage_key \
        "kernel-prebuilt-v1" \
        "$(lt_file_fingerprint "$LT_KERNEL_INPUT")" \
        "$LT_STAGE_EPOCH")"
fi

if lt_stage_is_current "$LT_OUT_DIR_PATH" kernel "$KEY" "$IMAGE" &&
    lt_is_json_file "$LT_OUT_DIR_PATH/.build/kernel.json"; then
    lt_stage_announce kernel "Image: cached ($MODE)"
    exit 0
fi

mkdir -p "$LT_OUT_DIR_PATH"

if [[ "$MODE" == "source" ]]; then
    lt_stage_announce kernel "building Linux from the pinned source"
    lt_build_kernel "$LT_OUT_DIR_PATH"
    SOURCE_IMAGE="$LT_BUILT_KERNEL_IMAGE"
else
    SOURCE_IMAGE="$LT_KERNEL_INPUT"
    lt_write_component_record "$LT_OUT_DIR_PATH" kernel \
        --provenance prebuilt-unverified \
        --binary "$SOURCE_IMAGE" \
        --prebuilt-path "$(realpath --relative-to="$LT_ROOT_DIR" "$SOURCE_IMAGE" 2>/dev/null || echo "$SOURCE_IMAGE")"
fi

cp "$SOURCE_IMAGE" "$IMAGE.tmp"
mv -f "$IMAGE.tmp" "$IMAGE"
touch -d "@$LT_STAGE_EPOCH" "$IMAGE"

lt_stage_record "$LT_OUT_DIR_PATH" kernel "$KEY"
lt_stage_announce kernel "Image: $([[ "$MODE" == source ]] && echo built || echo staged) ($MODE)"
