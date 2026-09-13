# shellcheck shell=bash
#
# Host tool prerequisites and guarded cleanup for the Linux-Touch scripts.
# Source this file; it does nothing on its own.

LT_TOOLS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LT_ROOT_DIR="${LT_ROOT_DIR:-$(cd "$LT_TOOLS_LIB_DIR/../.." && pwd -P)}"

# Identity of the implicit prototype userspace. This is a label, not a
# distro profile: distro profiles are a later step.
LT_BUILD_ID="${LT_BUILD_ID:-busybox-minimal}"

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

# Hard requirements for producing the initramfs.
lt_require_build_tools() {
    local tool missing=()
    for tool in find sort stat touch chmod cpio gzip; do
        command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    lt_python >/dev/null || missing+=("python3")

    if (( ${#missing[@]} > 0 )); then
        echo "error: missing required host tools: ${missing[*]}" >&2
        echo "Install them with your distribution's package manager." >&2
        echo "GNU coreutils, GNU findutils, GNU cpio and gzip are expected." >&2
        return 1
    fi

    if ! LT_CPIO_OWNER_FLAG="$(lt_detect_cpio_owner_flag)"; then
        echo "error: cpio does not support reproducible archives" >&2
        echo "GNU cpio 2.12 or newer is required (--reproducible and --owner)." >&2
        return 1
    fi
    export LT_CPIO_OWNER_FLAG
}

# GNU cpio spells forced-numeric ownership '+0:+0'; older builds accept
# only '0:0'. Probe once instead of guessing.
lt_detect_cpio_owner_flag() {
    local probe_dir candidate status
    probe_dir="$(mktemp -d)" || return 1
    status=1
    for candidate in "--owner=+0:+0" "--owner=0:0"; do
        if (
            cd "$probe_dir" &&
            printf '.\0' |
                cpio --null --create --quiet --format=newc --reproducible \
                    "$candidate" >/dev/null 2>&1
        ); then
            printf '%s\n' "$candidate"
            status=0
            break
        fi
    done
    [[ -n "$probe_dir" && -d "$probe_dir" ]] && rm -rf -- "$probe_dir"
    return "$status"
}

# Advisory only: nothing in the current pipeline compiles. These become
# hard requirements when a kernel/BusyBox build stage lands.
lt_report_toolchain() {
    local prefix candidate
    prefix="${CROSS_COMPILE:-}"
    if [[ -z "$prefix" ]]; then
        for candidate in aarch64-linux-gnu- aarch64-unknown-linux-gnu- aarch64-none-linux-gnu-; do
            if command -v "${candidate}gcc" >/dev/null 2>&1; then
                prefix="$candidate"
                break
            fi
        done
    fi

    if [[ -z "$prefix" ]] || ! command -v "${prefix}gcc" >/dev/null 2>&1; then
        echo "note: no aarch64 cross compiler detected (not required yet:"
        echo "      this build consumes prebuilt kernel/BusyBox artifacts)"
        return 0
    fi
    command -v make >/dev/null 2>&1 ||
        echo "note: make not found (not required yet)"
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
