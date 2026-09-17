# shellcheck shell=bash
#
# Cross toolchain resolution and source-build bookkeeping.
# Sourced by the build helpers in this directory.

# Resolve the aarch64 cross compiler prefix. Honours CROSS_COMPILE, else
# probes the usual triplets. Fails loudly: a source build must never
# silently fall back to the host compiler.
lt_resolve_cross_compile() {
    local architecture="$1"

    if [[ "$architecture" != "aarch64" ]]; then
        echo "error: no cross toolchain policy for architecture '$architecture'" >&2
        return 1
    fi

    # The prefixes it tries live in scripts/lib/requirements.py, the same
    # list ./scripts/check-env.sh reports on.
    "$(lt_python)" "$LT_ROOT_DIR/scripts/lib/requirements.py" cross-compile
}

lt_compiler_version() {
    "${1}gcc" --version 2>/dev/null | head -1
}

# Verify a produced binary really is 64-bit ARM. A build that silently
# targeted the host would produce an image QEMU cannot run.
lt_require_aarch64_binary() {
    local path="$1" description
    if ! command -v file >/dev/null 2>&1; then
        return 0
    fi
    description="$(file -b "$path")"
    case "$description" in
        *aarch64* | *"ARM aarch64"* | *"ARM64"*)
            return 0
            ;;
        *)
            echo "error: $path is not an aarch64 binary:" >&2
            echo "       $description" >&2
            return 1
            ;;
    esac
}

# Record what produced a component, for the manifest stage to merge.
# Every value is passed explicitly, and a failure here aborts the stage
# rather than leaving a record that misstates an artifact's origin.
#
#   lt_write_component_record <out-dir> <name> [writer arguments...]
lt_write_component_record() {
    local out_dir="$1" name="$2"
    shift 2
    "$(lt_python)" "$LT_BUILDER_DIR/lib/write-component.py" \
        --output "$out_dir/.build/$name.json" \
        --source-date-epoch "$LT_STAGE_EPOCH" \
        --architecture "$LT_DEVICE_ARCH" \
        "$@"
}
