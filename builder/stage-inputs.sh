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

if [[ ! -f "$LT_KERNEL_INPUT" ]]; then
    echo "error: kernel not found:" >&2
    echo "       $LT_KERNEL_INPUT" >&2
    echo >&2
    echo "Build the ARM64 kernel first." >&2
    exit 1
fi

if [[ ! -x "$LT_BUSYBOX_INPUT" ]]; then
    echo "error: BusyBox not found or not executable:" >&2
    echo "       $LT_BUSYBOX_INPUT" >&2
    echo >&2
    echo "Build the ARM64 BusyBox first." >&2
    exit 1
fi

lt_require_build_tools
lt_report_toolchain

lt_stage_announce inputs "kernel, BusyBox and host tools: OK"
