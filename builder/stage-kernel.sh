#!/usr/bin/env bash
set -euo pipefail

# Stage 2: stage the kernel image into the build output.
#
#   stage-kernel.sh <device> <distro> [--force]
#
# Inputs:  LT_KERNEL_INPUT (a prebuilt artifact; nothing here compiles it)
# Outputs: out/<device>-<distro>/Image
#
# The image is copied, not built. Its hash is recorded by the manifest stage
# so the output directory is self-contained and says where the bytes came
# from instead of implying this project produced them.

# shellcheck source=stage-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stage-common.sh"

lt_stage_setup "$@"

IMAGE="$LT_OUT_DIR_PATH/Image"

if [[ ! -f "$LT_KERNEL_INPUT" ]]; then
    echo "error: kernel not found:" >&2
    echo "       $LT_KERNEL_INPUT" >&2
    echo "Run builder/stage-inputs.sh $LT_DEVICE_ID $LT_DISTRO_ID for details." >&2
    exit 1
fi

KEY="$(lt_stage_key "kernel-v1" "$(lt_file_fingerprint "$LT_KERNEL_INPUT")" "$LT_STAGE_EPOCH")"

if lt_stage_is_current "$LT_OUT_DIR_PATH" kernel "$KEY" "$IMAGE"; then
    lt_stage_announce kernel "Image: cached"
    exit 0
fi

mkdir -p "$LT_OUT_DIR_PATH"
cp "$LT_KERNEL_INPUT" "$IMAGE.tmp"
mv -f "$IMAGE.tmp" "$IMAGE"
touch -d "@$LT_STAGE_EPOCH" "$IMAGE"

lt_stage_record "$LT_OUT_DIR_PATH" kernel "$KEY"
lt_stage_announce kernel "Image: staged"
