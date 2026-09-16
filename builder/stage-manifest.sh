#!/usr/bin/env bash
set -euo pipefail

# Stage 5: describe the build output.
#
#   stage-manifest.sh <device> <distro>
#
# Inputs:  both profiles, sources.yaml, the staged artifacts
# Outputs: out/<device>-<distro>/manifest.json
#
# Never cached: it is cheap, it is the summary of every other stage, and it
# is deterministic, so regenerating it cannot change the output.

# shellcheck source=stage-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stage-common.sh"

lt_stage_setup "$@"

IMAGE="$LT_OUT_DIR_PATH/Image"
INITRAMFS="$LT_OUT_DIR_PATH/initramfs.cpio.gz"
MANIFEST="$LT_OUT_DIR_PATH/manifest.json"

for artifact in "$IMAGE" "$INITRAMFS"; do
    if [[ ! -f "$artifact" ]]; then
        echo "error: missing artifact for the manifest:" >&2
        echo "       $artifact" >&2
        echo "Run the kernel and initramfs stages first." >&2
        exit 1
    fi
done

"$(lt_python)" "$ROOT_DIR/scripts/lib/manifest.py" \
    --root "$ROOT_DIR" \
    --device-profile "$LT_DEVICE_PROFILE" \
    --distro-profile "$LT_DISTRO_PROFILE" \
    --build-id "$LT_DISTRO_ID" \
    --source-date-epoch "$LT_STAGE_EPOCH" \
    --sources "$LT_SOURCES_MANIFEST" \
    --input "kernel_image=$LT_KERNEL_INPUT" \
    --input "busybox_binary=$LT_BUSYBOX_INPUT" \
    --artifact "kernel_image=$IMAGE" \
    --artifact "initramfs=$INITRAMFS" \
    --copied-from "kernel_image=kernel_image" \
    --output "$MANIFEST" >/dev/null

lt_stage_announce manifest "manifest: written"
