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

# Component records written by the kernel and rootfs stages: how each was
# produced, from which source, with which configuration and toolchain.
COMPONENT_ARGS=()
for component in kernel busybox; do
    record="$LT_OUT_DIR_PATH/.build/$component.json"
    if [[ -f "$record" ]]; then
        COMPONENT_ARGS+=(--component "$component=$record")
    fi
done

# Record the file each component was actually taken from: the compiled
# output when it was built here, the prebuilt artifact when it was not.
INPUT_ARGS=()

if [[ "$(lt_component_provenance "$LT_OUT_DIR_PATH/.build/kernel.json")" == "built-from-source" ]]; then
    INPUT_ARGS+=(--input "kernel_image=$LT_BUILD_DIR/kernel/$LT_DEVICE_ID/$LT_DEVICE_KERNEL_IMAGE")
else
    INPUT_ARGS+=(--input "kernel_image=$LT_KERNEL_INPUT")
fi

if [[ "$LT_DISTRO_BOOTSTRAP_METHOD" == "busybox" ]]; then
    if [[ "$(lt_component_provenance "$LT_OUT_DIR_PATH/.build/busybox.json")" == "built-from-source" ]]; then
        INPUT_ARGS+=(--input "busybox_binary=$LT_BUILD_DIR/busybox/$LT_DISTRO_ID/busybox")
    else
        INPUT_ARGS+=(--input "busybox_binary=$LT_BUSYBOX_INPUT")
    fi
fi

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
    ${INPUT_ARGS[@]+"${INPUT_ARGS[@]}"} \
    --artifact "kernel_image=$IMAGE" \
    --artifact "initramfs=$INITRAMFS" \
    --copied-from "kernel_image=kernel_image" \
    ${COMPONENT_ARGS[@]+"${COMPONENT_ARGS[@]}"} \
    --output "$MANIFEST" >/dev/null

lt_stage_announce manifest "manifest: written"
