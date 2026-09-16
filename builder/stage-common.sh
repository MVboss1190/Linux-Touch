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

lt_stage_setup() {
    lt_stage_parse_arguments "$@"

    lt_require_device "$LT_STAGE_DEVICE"
    lt_require_distro "$LT_STAGE_DISTRO" "$LT_DEVICE_ARCH"

    LT_OUT_DIR_PATH="$(lt_out_dir "$LT_DEVICE_ID" "$LT_DISTRO_ID")"
    LT_STAGE_EPOCH="$(lt_source_date_epoch)"
    export SOURCE_DATE_EPOCH="$LT_STAGE_EPOCH"
}
