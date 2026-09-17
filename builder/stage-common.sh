# shellcheck shell=bash
#
# Boilerplate shared by the stage scripts in this directory: locate the
# repository, load the libraries, parse <device> <distro> [--force], resolve
# and validate both profiles, and derive the output directory.
#
# After sourcing, a stage has: LT_DEVICE_*, LT_DISTRO_*, LT_OUT_DIR_PATH,
# LT_STAGE_EPOCH, and the LT_*_INPUT paths.

LT_BUILDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$LT_BUILDER_DIR/.." && pwd)"

# shellcheck source=../scripts/lib/profile.sh
source "$ROOT_DIR/scripts/lib/profile.sh"
# shellcheck source=../scripts/lib/tools.sh
source "$ROOT_DIR/scripts/lib/tools.sh"
# shellcheck source=../scripts/lib/output.sh
source "$ROOT_DIR/scripts/lib/output.sh"
# shellcheck source=../scripts/lib/stages.sh
source "$ROOT_DIR/scripts/lib/stages.sh"
# shellcheck source=lib/toolchain.sh
source "$LT_BUILDER_DIR/lib/toolchain.sh"
# shellcheck source=lib/build-kernel.sh
source "$LT_BUILDER_DIR/lib/build-kernel.sh"
# shellcheck source=lib/build-busybox.sh
source "$LT_BUILDER_DIR/lib/build-busybox.sh"

lt_stage_setup() {
    lt_stage_parse_arguments "$@"

    lt_require_device "$LT_STAGE_DEVICE"
    lt_require_distro "$LT_STAGE_DISTRO" "$LT_DEVICE_ARCH"

    LT_OUT_DIR_PATH="$(lt_out_dir "$LT_DEVICE_ID" "$LT_DISTRO_ID")"
    LT_STAGE_EPOCH="$(lt_source_date_epoch)"
    export SOURCE_DATE_EPOCH="$LT_STAGE_EPOCH"
    LT_BUILD_DIR="${LT_BUILD_DIR:-$ROOT_DIR/build}"
    LT_BUILD_MODE="${LT_BUILD_MODE:-auto}"
    export LT_BUILD_DIR LT_BUILD_MODE LT_STAGE_EPOCH
}

# Decide how one component is produced, and say so out loud. Never falls
# back to a prebuilt artifact without reporting it.
#
#   lt_resolve_build_mode <component> <source-name> <prebuilt-path>
# Prints "source" or "prebuilt"; fails when the requested mode is impossible.
lt_resolve_build_mode() {
    local component="$1" source_name="$2" prebuilt="$3"
    local have_source=0 have_prebuilt=0 have_toolchain=0 reason=""

    # Deliberately not piped into grep: `grep -q` exits on its first match,
    # which can hand the writer a SIGPIPE, and under `pipefail` a successful
    # match would then read as a failure. Capture the output, then test it.
    local description=""
    if [[ -n "$source_name" ]]; then
        description="$("$(lt_python)" "$ROOT_DIR/scripts/lib/sources.py" --manifest "$LT_SOURCES_MANIFEST" describe "$source_name" 2>/dev/null)" || description=""
    fi

    if [[ "$description" == *'"cached": true'* ]]; then
        have_source=1
    else
        reason="the pinned source is not fetched (run ./scripts/fetch.sh $source_name)"
    fi

    if [[ -z "$source_name" ]]; then
        reason="no source build is configured for $component"
    fi

    if lt_resolve_cross_compile "$LT_DEVICE_ARCH" >/dev/null 2>&1; then
        have_toolchain=1
    elif (( have_source == 1 )); then
        reason="no aarch64 cross compiler was found"
    fi

    [[ -e "$prebuilt" ]] && have_prebuilt=1

    case "$LT_BUILD_MODE" in
        source)
            if (( have_source == 1 && have_toolchain == 1 )); then
                printf 'source\n'
                return 0
            fi
            echo "error: --from-source was requested but $component cannot be" >&2
            echo "       built: $reason" >&2
            return 1
            ;;
        prebuilt)
            if (( have_prebuilt == 1 )); then
                printf 'prebuilt\n'
                return 0
            fi
            echo "error: --prebuilt was requested but no prebuilt $component" >&2
            echo "       exists at: $prebuilt" >&2
            return 1
            ;;
        auto)
            if (( have_source == 1 && have_toolchain == 1 )); then
                printf 'source\n'
                return 0
            fi
            if (( have_prebuilt == 1 )); then
                echo "warning: building $component from source is not possible:" >&2
                echo "         $reason" >&2
                echo "warning: falling back to the prebuilt $component at" >&2
                echo "         $prebuilt (recorded as prebuilt-unverified)." >&2
                printf 'prebuilt\n'
                return 0
            fi
            echo "error: cannot produce $component." >&2
            echo "       No source build: $reason" >&2
            echo "       No prebuilt artifact at: $prebuilt" >&2
            return 1
            ;;
        *)
            echo "error: unknown build mode: $LT_BUILD_MODE" >&2
            return 1
            ;;
    esac
}
