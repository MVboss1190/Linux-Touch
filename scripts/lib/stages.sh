# shellcheck shell=bash
#
# Stage bookkeeping for the builder.
# Source this file after tools.sh; it does nothing on its own.
#
# Each stage records a stamp under out/<device>-<distro>/.stamps/<stage>
# holding a hash of everything it depends on. If the stamp matches and the
# stage's outputs are present, the stage is skipped. Set LT_FORCE=1 (or pass
# --force to build.sh) to ignore stamps. That is the whole cache: no
# dependency graph, no cache directory, no eviction.

LT_STAGES_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

lt_stage_announce() {
    printf '[%s] %s\n' "$1" "$2"
}

# A stage key is the hash of its declared inputs. Include a version tag so
# changing a stage's behaviour invalidates its stamps.
lt_stage_key() {
    printf '%s\0' "$@" | sha256sum | cut -d' ' -f1
}

lt_file_fingerprint() {
    if [[ ! -f "$1" ]]; then
        printf 'absent\n'
        return 0
    fi
    sha256sum "$1" | cut -d' ' -f1
}

# Content plus structure: names, types and modes, then file contents.
lt_tree_fingerprint() {
    local directory="$1"
    if [[ ! -d "$directory" ]]; then
        printf 'absent\n'
        return 0
    fi
    (
        cd "$directory"
        find . -printf '%P %y %m\n' | LC_ALL=C sort
        find . -type l -printf '%P -> %l\n' | LC_ALL=C sort
        find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
    ) | sha256sum | cut -d' ' -f1
}

# A component record is only usable if it actually parses. A stamp alone
# cannot vouch for that.
lt_is_json_file() {
    [[ -f "$1" ]] || return 1
    "$(lt_python)" -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" 2>/dev/null
}

# Provenance recorded by the stage that produced a component, or "none".
lt_component_provenance() {
    local record="$1"
    if [[ ! -f "$record" ]]; then
        printf 'none\n'
        return 0
    fi
    "$(lt_python)" -c \
        'import json,sys; print(json.load(open(sys.argv[1])).get("provenance","none"))' \
        "$record" 2>/dev/null || printf 'none\n'
}

lt_stage_stamp_path() {
    printf '%s/.stamps/%s\n' "$1" "$2"
}

# lt_stage_is_current <out-dir> <stage> <key> [outputs...]
lt_stage_is_current() {
    local out_dir="$1" stage="$2" key="$3" stamp output
    shift 3

    [[ "${LT_FORCE:-0}" == "1" ]] && return 1

    stamp="$(lt_stage_stamp_path "$out_dir" "$stage")"
    [[ -f "$stamp" ]] || return 1
    [[ "$(cat "$stamp")" == "$key" ]] || return 1

    # An empty output is a truncated or half-written one: treat it as a
    # cache miss rather than trusting the stamp.
    for output in "$@"; do
        if [[ -d "$output" ]]; then
            continue
        fi
        [[ -s "$output" ]] || return 1
    done
    return 0
}

lt_stage_record() {
    local out_dir="$1" stage="$2" key="$3" stamp
    stamp="$(lt_stage_stamp_path "$out_dir" "$stage")"
    mkdir -p "$(dirname "$stamp")"
    printf '%s\n' "$key" > "$stamp"
}

# Shared entry code for the stage scripts: <device> <distro> [--force].
lt_stage_parse_arguments() {
    local usage="usage: $(basename "${0}") <device> <distro> [--force]"
    LT_STAGE_DEVICE=""
    LT_STAGE_DISTRO=""

    while (( $# > 0 )); do
        case "$1" in
            --force)
                LT_FORCE=1
                shift
                ;;
            -h | --help)
                echo "$usage"
                exit 0
                ;;
            -*)
                echo "error: unknown option: $1" >&2
                echo "$usage" >&2
                exit 2
                ;;
            *)
                if [[ -z "$LT_STAGE_DEVICE" ]]; then
                    LT_STAGE_DEVICE="$1"
                elif [[ -z "$LT_STAGE_DISTRO" ]]; then
                    LT_STAGE_DISTRO="$1"
                else
                    echo "error: unexpected argument: $1" >&2
                    echo "$usage" >&2
                    exit 2
                fi
                shift
                ;;
        esac
    done

    if [[ -z "$LT_STAGE_DEVICE" || -z "$LT_STAGE_DISTRO" ]]; then
        echo "$usage" >&2
        exit 2
    fi
    export LT_FORCE="${LT_FORCE:-0}"
}
