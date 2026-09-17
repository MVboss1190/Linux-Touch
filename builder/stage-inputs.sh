#!/usr/bin/env bash
set -euo pipefail

# Stage 1: validate build inputs and host tools.
#
#   stage-inputs.sh <device> <distro>
#
# Inputs:  both profiles, LT_KERNEL_INPUT, LT_BUSYBOX_INPUT
# Outputs: none -- this stage only checks, so it never caches and is always
#          safe to rerun. It runs before anything destructive.

# shellcheck source=stage-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stage-common.sh"

lt_stage_setup "$@"

# How each component will be produced. This fails early and loudly rather
# than discovering halfway through that nothing can be built.
KERNEL_MODE="$(lt_resolve_build_mode kernel "$LT_DEVICE_KERNEL_SOURCE" "$LT_KERNEL_INPUT")"
BUSYBOX_MODE="none"
if [[ "$LT_DISTRO_BOOTSTRAP_METHOD" == "busybox" ]]; then
    BUSYBOX_MODE="$(lt_resolve_build_mode busybox "$LT_DISTRO_BUILD_SOURCE" "$LT_BUSYBOX_INPUT")"
fi

lt_require_build_tools

if [[ "$KERNEL_MODE" == "source" || "$BUSYBOX_MODE" == "source" ]]; then
    lt_require_source_build_tools || exit 1
    lt_stage_announce inputs "cross toolchain: $(lt_resolve_cross_compile "$LT_DEVICE_ARCH")gcc"
else
    lt_report_toolchain
fi

lt_stage_announce inputs "kernel: $KERNEL_MODE, busybox: $BUSYBOX_MODE, host tools: OK"
