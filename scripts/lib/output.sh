# shellcheck shell=bash
#
# Build output location and verification.
# Source this file after tools.sh; it does nothing on its own.
#
# out/<device>-<distro>/ is the canonical build output. manifest.json in it
# is authoritative: it names the artifacts and their hashes, and consumers
# (run.sh) read it rather than guessing paths.

LT_OUTPUT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LT_ROOT_DIR="${LT_ROOT_DIR:-$(cd "$LT_OUTPUT_LIB_DIR/../.." && pwd -P)}"
LT_BUILDOUT_PY="$LT_OUTPUT_LIB_DIR/buildout.py"
LT_OUT_ROOT="${LT_OUT_ROOT:-$LT_ROOT_DIR/out}"

lt_out_dir() {
    printf '%s/%s-%s\n' "$LT_OUT_ROOT" "$1" "$2"
}

# Verify the build output for <device> <distro> and export LT_OUT_*.
# Returns 0 on success, 3 when nothing has been built yet, and 1 (or any
# other non-zero) when the output is missing artifacts, corrupted, belongs
# to another build, or could not be inspected.
lt_require_build_output() {
    local device="$1" distro="$2" exported status=0 errors
    errors="$(mktemp)"
    exported="$(
        "$(lt_python)" "$LT_BUILDOUT_PY" \
            --root "$LT_ROOT_DIR" \
            --out-dir "$(lt_out_dir "$device" "$distro")" \
            --device "$device" \
            --distro "$distro" \
            env 2>"$errors"
    )" || status=$?
    if (( status != 0 )); then
        # "Nothing built yet" is not an error for the caller to report: it
        # decides what to do about it. Everything else must be seen.
        if (( status != 3 )); then
            cat "$errors" >&2
        fi
        rm -f "$errors"
        return "$status"
    fi
    rm -f "$errors"
    eval "$exported"
}
