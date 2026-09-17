# shellcheck shell=bash
#
# Host tool prerequisites and guarded cleanup for the Linux-Touch scripts.
# Source this file; it does nothing on its own.

LT_TOOLS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LT_ROOT_DIR="${LT_ROOT_DIR:-$(cd "$LT_TOOLS_LIB_DIR/../.." && pwd -P)}"

# Default distro used when none is selected. The distro profile itself
# lives in distros/; this is only the fallback name.
LT_BUILD_ID="${LT_BUILD_ID:-busybox-minimal}"

# Prebuilt build inputs. These are inputs, not outputs: nothing here
# compiles them. Overridable so stages can be exercised in isolation.
LT_KERNEL_INPUT="${LT_KERNEL_INPUT:-$LT_ROOT_DIR/kernel/linux/arch/arm64/boot/Image}"
LT_BUSYBOX_INPUT="${LT_BUSYBOX_INPUT:-$LT_ROOT_DIR/busybox/busybox}"
LT_ROOTFS_DIR="${LT_ROOTFS_DIR:-$LT_ROOT_DIR/os/rootfs}"
LT_SOURCES_MANIFEST="${LT_SOURCES_MANIFEST:-$LT_ROOT_DIR/sources.yaml}"
LT_SOURCE_CACHE_DIR="${LT_SOURCE_CACHE_DIR:-$LT_ROOT_DIR/.cache/sources}"
export LT_SOURCE_CACHE_DIR

lt_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    echo "error: python3 is required" >&2
    return 1
}

# Reproducible builds need a fixed timestamp. Honour the environment, else
# fall back to the constant pinned in sources.yaml -- never the wall clock.
# Every stage resolves it the same way, so stages agree when run alone.
lt_source_date_epoch() {
    if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
        if [[ ! "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]]; then
            echo "error: SOURCE_DATE_EPOCH must be a non-negative integer" >&2
            return 1
        fi
        printf '%s\n' "$SOURCE_DATE_EPOCH"
        return 0
    fi
    "$(lt_python)" "$LT_TOOLS_LIB_DIR/sources.py" --manifest "$LT_SOURCES_MANIFEST" epoch
}

# The requirement lists live in scripts/lib/requirements.py so that the
# builder and ./scripts/check-env.sh can never disagree about what is
# needed. These helpers only decide when to check.
lt_requirements_tool() {
    "$(lt_python)" "$LT_TOOLS_LIB_DIR/requirements.py" "$@"
}

# Hard requirements for producing the initramfs.
lt_require_build_tools() {
    lt_requirements_tool check --group core --quiet || return 1

    if ! LT_CPIO_OWNER_FLAG="$(lt_requirements_tool cpio-owner-flag)"; then
        return 1
    fi
    export LT_CPIO_OWNER_FLAG
}

# Hard requirements for compiling the pinned kernel and BusyBox. Checked
# only when a source build is actually going to happen.
lt_require_source_build_tools() {
    lt_requirements_tool check --group source-build --quiet
}

# Advisory only when nothing is compiled. These become
# hard requirements when a kernel/BusyBox build stage lands.
lt_report_toolchain() {
    local prefix
    if ! prefix="$(lt_requirements_tool cross-compile 2>/dev/null)"; then
        echo "note: no aarch64 cross compiler detected (not required for this"
        echo "      build, which consumes prebuilt artifacts; run"
        echo "      ./scripts/check-env.sh to see what a source build needs)"
        return 0
    fi
    echo "Toolchain: ${prefix}gcc"
}

# rm -rf, restricted to the directories this project generates.
# Refuses anything outside os/rootfs, os/images and out/.
lt_safe_rm_rf() {
    local target="${1:-}" parent resolved
    if [[ -z "$target" ]]; then
        echo "error: refusing to remove an empty path" >&2
        return 1
    fi

    # Nothing on disk means nothing to remove; no path check needed.
    if [[ ! -e "$target" && ! -L "$target" ]]; then
        return 0
    fi

    if ! parent="$(cd "$(dirname "$target")" 2>/dev/null && pwd -P)"; then
        echo "error: cannot resolve the parent directory of: $target" >&2
        return 1
    fi
    resolved="$parent/$(basename "$target")"

    case "$resolved" in
        "$LT_ROOT_DIR"/os/rootfs | "$LT_ROOT_DIR"/os/rootfs/* | \
        "$LT_ROOT_DIR"/os/images | "$LT_ROOT_DIR"/os/images/* | \
        "$LT_ROOT_DIR"/out | "$LT_ROOT_DIR"/out/*)
            ;;
        *)
            echo "error: refusing to remove a path outside the build output" >&2
            echo "       directories: $resolved" >&2
            return 1
            ;;
    esac

    rm -rf -- "$resolved"
}
