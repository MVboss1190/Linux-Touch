#!/usr/bin/env bash
set -euo pipefail

# Create a reproducible initramfs from a staged root filesystem.
#
#   mkinitramfs.sh <rootfs-dir> <output-file> <source-date-epoch>
#   mkinitramfs.sh --list <rootfs-dir> <source-date-epoch>
#
# Determinism comes from four things:
#   * member order is LC_ALL=C sorted, not filesystem order
#   * mtimes are forced to SOURCE_DATE_EPOCH
#   * modes are normalized, so the builder's umask cannot leak in
#   * uid/gid are forced to 0:0 by cpio, and gzip -n omits name and time
#
# --list prints the normalized member list instead of writing an archive.
# It needs no cpio, which makes the normalization testable on its own.

LT_MKINITRAMFS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=tools.sh
source "$LT_MKINITRAMFS_DIR/tools.sh"

lt_normalize_tree() {
    local rootfs="$1" epoch="$2"

    # Directories and executables 0755, everything else 0644. Symlink modes
    # are not stored by newc in a way that varies, so they are left alone.
    find "$rootfs" -type d -exec chmod 0755 {} +
    find "$rootfs" -type f -perm -u+x -exec chmod 0755 {} +
    find "$rootfs" -type f ! -perm -u+x -exec chmod 0644 {} +

    # -h so symlink timestamps are set rather than their targets'.
    find "$rootfs" -exec touch -h -d "@$epoch" {} +
}

lt_initramfs_members() {
    local rootfs="$1"
    (
        cd "$rootfs"
        find . -print0 | LC_ALL=C sort -z
    )
}

lt_list_members() {
    local rootfs="$1" epoch="$2" member
    lt_normalize_tree "$rootfs" "$epoch"
    while IFS= read -r -d '' member; do
        # uid/gid are deliberately absent: cpio forces them to 0:0 at
        # archive time, so the staged tree's ownership is not meaningful.
        printf '%s %s %s\n' \
            "$(stat -c '%a' "$rootfs/$member")" \
            "$(stat -c '%Y' "$rootfs/$member")" \
            "$member"
    done < <(lt_initramfs_members "$rootfs")
}

lt_make_initramfs() {
    local rootfs="$1" output="$2" epoch="$3"

    lt_require_build_tools

    lt_normalize_tree "$rootfs" "$epoch"

    mkdir -p "$(dirname "$output")"

    # Written to a temporary file first so a failure never leaves a
    # half-written archive that later stages would hash.
    (
        cd "$rootfs"
        find . -print0 | LC_ALL=C sort -z |
            cpio --null --create --quiet --format=newc --reproducible \
                "$LT_CPIO_OWNER_FLAG"
    ) | gzip -9 -n > "$output.tmp"

    mv -f "$output.tmp" "$output"
    touch -d "@$epoch" "$output"
}

main() {
    if [[ "${1:-}" == "--list" ]]; then
        if [[ $# -ne 3 ]]; then
            echo "usage: mkinitramfs.sh --list <rootfs-dir> <source-date-epoch>" >&2
            exit 2
        fi
        lt_list_members "$2" "$3"
        return 0
    fi

    if [[ $# -ne 3 ]]; then
        echo "usage: mkinitramfs.sh <rootfs-dir> <output-file> <source-date-epoch>" >&2
        exit 2
    fi

    local rootfs="$1" output="$2" epoch="$3"

    if [[ ! -d "$rootfs" ]]; then
        echo "error: root filesystem directory not found: $rootfs" >&2
        exit 1
    fi
    if [[ ! "$epoch" =~ ^[0-9]+$ ]]; then
        echo "error: SOURCE_DATE_EPOCH must be a non-negative integer: $epoch" >&2
        exit 1
    fi

    lt_make_initramfs "$rootfs" "$output" "$epoch"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
