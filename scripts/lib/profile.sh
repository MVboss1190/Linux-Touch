# shellcheck shell=bash
#
# Shared profile resolution for the Linux-Touch scripts.
# Source this file; it does nothing on its own.
#
# Two axes, deliberately separate:
#   device profile (devices/*/device.yaml)  -- where it runs: architecture,
#                                              kernel, boot, runner
#   distro profile (distros/*/distro.yaml)  -- what runs: init system,
#                                              root filesystem, overlay
#
# All profile knowledge lives in scripts/lib/profile.py and distro.py. Both
# axes are discovered from disk, so adding either never requires editing a
# script.

LT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LT_ROOT_DIR="$(cd "$LT_LIB_DIR/../.." && pwd)"
LT_PROFILE_PY="$LT_LIB_DIR/profile.py"
LT_DISTRO_PY="$LT_LIB_DIR/distro.py"
LT_DEVICES_DIR="${LT_DEVICES_DIR:-$LT_ROOT_DIR/devices}"
LT_DISTROS_DIR="${LT_DISTROS_DIR:-$LT_ROOT_DIR/distros}"
LT_DEFAULT_DEVICE="${LT_DEFAULT_DEVICE:-virtual-phone}"
LT_DEFAULT_DISTRO="${LT_DEFAULT_DISTRO:-busybox-minimal}"

lt_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    echo "error: python3 is required to read device profiles" >&2
    return 1
}

lt_profile_tool() {
    local python
    python="$(lt_python)" || return 1
    "$python" "$LT_PROFILE_PY" --devices-dir "$LT_DEVICES_DIR" "$@"
}

lt_distro_tool() {
    local python
    python="$(lt_python)" || return 1
    "$python" "$LT_DISTRO_PY" --distros-dir "$LT_DISTROS_DIR" "$@"
}

# Shared argument parsing so build.sh and run.sh cannot drift:
#
#   <script> [device] [--distro <id>]
#
# Sets LT_TARGET_DEVICE and LT_TARGET_DISTRO. Exits 2 on a usage error.
lt_parse_arguments() {
    local usage="usage: $(basename "${0}") [device] [--distro <distro>]"
    LT_TARGET_DEVICE=""
    LT_TARGET_DISTRO=""
    LT_EXTRA_ARGS=()

    while (( $# > 0 )); do
        case "$1" in
            --distro)
                if [[ -z "${2:-}" ]]; then
                    echo "error: --distro requires a value" >&2
                    echo "$usage" >&2
                    exit 2
                fi
                LT_TARGET_DISTRO="$2"
                shift 2
                ;;
            --distro=*)
                LT_TARGET_DISTRO="${1#*=}"
                shift
                ;;
            -h | --help)
                echo "$usage"
                exit 0
                ;;
            -*)
                # Kept for the calling script to interpret. Anything it does
                # not recognise is rejected there, so an unknown option is
                # still an error.
                LT_EXTRA_ARGS+=("$1")
                shift
                ;;
            *)
                if [[ -n "$LT_TARGET_DEVICE" ]]; then
                    echo "error: unexpected argument: $1" >&2
                    echo "$usage" >&2
                    exit 2
                fi
                LT_TARGET_DEVICE="$1"
                shift
                ;;
        esac
    done

    LT_TARGET_DEVICE="${LT_TARGET_DEVICE:-$LT_DEFAULT_DEVICE}"
    LT_TARGET_DISTRO="${LT_TARGET_DISTRO:-$LT_DEFAULT_DISTRO}"
}

# Reject any option the calling script did not consume from LT_EXTRA_ARGS.
lt_reject_unknown_options() {
    local option
    for option in ${LT_EXTRA_ARGS+"${LT_EXTRA_ARGS[@]}"}; do
        echo "error: unknown option: $option" >&2
        echo "usage: $(basename "${0}") [device] [--distro <distro>]${1:+ $1}" >&2
        exit 2
    done
}

# Validate a device profile and export its values into the calling shell.
# Exits the script with the tool's own exit code on failure
# (1 = invalid profile, 2 = unknown device).
lt_require_device() {
    local name="$1" environment status=0
    environment="$(lt_profile_tool env "$name")" || status=$?
    if [[ $status -ne 0 ]]; then
        exit "$status"
    fi
    eval "$environment"
}

# Validate a distro profile, check it supports the device architecture, and
# export its values into the calling shell. Exits with the tool's own exit
# code on failure (1 = invalid or incompatible, 2 = unknown distro).
lt_require_distro() {
    local name="$1" architecture="${2:-}" environment status=0
    environment="$(lt_distro_tool env "$name" --arch "$architecture")" || status=$?
    if [[ $status -ne 0 ]]; then
        exit "$status"
    fi
    eval "$environment"
}
