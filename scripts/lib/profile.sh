# shellcheck shell=bash
#
# Shared device profile resolution for the Linux-Touch scripts.
# Source this file; it does nothing on its own.
#
# All profile knowledge lives in scripts/lib/profile.py. Devices are
# discovered from devices/*/device.yaml, so adding a device never requires
# editing a script.

LT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LT_ROOT_DIR="$(cd "$LT_LIB_DIR/../.." && pwd)"
LT_PROFILE_PY="$LT_LIB_DIR/profile.py"
LT_DEVICES_DIR="${LT_DEVICES_DIR:-$LT_ROOT_DIR/devices}"

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
