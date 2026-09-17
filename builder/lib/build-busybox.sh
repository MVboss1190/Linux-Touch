# shellcheck shell=bash
#
# Build the pinned BusyBox for a distro.
#
# Inputs:  the distro profile's bootstrap.build block, the pinned source in
#          sources.yaml, an aarch64 cross toolchain
# Outputs: a static aarch64 busybox binary in build/busybox/<distro>/, plus
#          a component record.
#
# The binary must be static: the initramfs carries no dynamic loader.

lt_build_busybox() {
    local out_dir="$1"
    local source_tree build_dir config_path binary prefix compiler
    local source_version source_sha fragment_path

    prefix="$(lt_resolve_cross_compile "$LT_DEVICE_ARCH")" || return 1
    compiler="$(lt_compiler_version "$prefix")"

    source_tree="$("$(lt_python)" "$LT_TOOLS_LIB_DIR/sources.py" \
        --manifest "$LT_SOURCES_MANIFEST" \
        extract "$LT_DISTRO_BUILD_SOURCE" --dest-dir "$LT_BUILD_DIR/sources")" || return 1

    source_version="$("$(lt_python)" "$LT_TOOLS_LIB_DIR/sources.py" \
        --manifest "$LT_SOURCES_MANIFEST" describe "$LT_DISTRO_BUILD_SOURCE" \
        | "$(lt_python)" -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
    source_sha="$("$(lt_python)" "$LT_TOOLS_LIB_DIR/sources.py" \
        --manifest "$LT_SOURCES_MANIFEST" describe "$LT_DISTRO_BUILD_SOURCE" \
        | "$(lt_python)" -c 'import json,sys; print(json.load(sys.stdin)["sha256"])')"

    build_dir="$LT_BUILD_DIR/busybox/$LT_DISTRO_ID"
    mkdir -p "$build_dir"
    config_path="$build_dir/.config"

    echo "  source:    $source_tree"
    echo "  build dir: $build_dir"
    echo "  toolchain: ${prefix}gcc"

    local -a make_args=(
        -C "$source_tree"
        O="$build_dir"
        ARCH=arm64
        CROSS_COMPILE="$prefix"
    )

    make "${make_args[@]}" "$LT_DISTRO_BUILD_DEFCONFIG" >/dev/null

    # BusyBox has no merge_config.sh: rewrite the option lines directly,
    # then let olddefconfig settle the dependencies.
    if [[ -n "$LT_DISTRO_BUILD_FRAGMENT" ]]; then
        fragment_path="$LT_DISTRO_DIR/$LT_DISTRO_BUILD_FRAGMENT"
        if [[ ! -f "$fragment_path" ]]; then
            echo "error: BusyBox config fragment not found: $fragment_path" >&2
            return 1
        fi
        "$(lt_python)" - "$config_path" "$fragment_path" <<'PYEOF'
import re, sys

config_path, fragment_path = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as handle:
    lines = handle.read().splitlines()

for raw in open(fragment_path, encoding="utf-8"):
    entry = raw.strip()
    if not entry or entry.startswith("#"):
        continue
    key, _, value = entry.partition("=")
    key = key.strip()
    value = value.strip()
    pattern = re.compile(r"^(%s=|# %s is not set$)" % (re.escape(key), re.escape(key)))
    lines = [line for line in lines if not pattern.match(line)]
    lines.append("# %s is not set" % key if value == "n" else "%s=%s" % (key, value))

with open(config_path, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
PYEOF
        # BusyBox's kconfig has no olddefconfig; oldconfig prompts for new
        # symbols, so feed it empty lines to accept every default. pipefail
        # is scoped off because `yes` is expected to die of SIGPIPE once
        # make stops reading.
        ( set +o pipefail; yes "" | make "${make_args[@]}" oldconfig >/dev/null )
    fi

    if [[ "$LT_DISTRO_BUILD_STATIC" == "true" ]] && ! grep -qx 'CONFIG_STATIC=y' "$config_path"; then
        echo "error: this distro requires a static BusyBox but CONFIG_STATIC" >&2
        echo "       is not set in $config_path" >&2
        return 1
    fi

    SOURCE_DATE_EPOCH="$LT_STAGE_EPOCH" \
    KCONFIG_NOTIMESTAMP=1 \
        make "${make_args[@]}" -j"$(nproc)" >&2

    binary="$build_dir/busybox"
    if [[ ! -x "$binary" ]]; then
        echo "error: the BusyBox build produced no binary at:" >&2
        echo "       $binary" >&2
        return 1
    fi
    lt_require_aarch64_binary "$binary" || return 1

    if [[ "$LT_DISTRO_BUILD_STATIC" == "true" ]] && command -v file >/dev/null 2>&1; then
        if file -b "$binary" | grep -q "dynamically linked"; then
            echo "error: BusyBox was linked dynamically; the initramfs has no" >&2
            echo "       dynamic loader, so it could not execute /bin/sh." >&2
            return 1
        fi
    fi

    lt_write_component_record "$out_dir" busybox \
        --provenance built-from-source \
        --binary "$binary" \
        --config "$config_path" \
        --source-name "$LT_DISTRO_BUILD_SOURCE" \
        --source-version "$source_version" \
        --source-sha256 "$source_sha" \
        --defconfig "$LT_DISTRO_BUILD_DEFCONFIG" \
        --cross-compile "$prefix" \
        --compiler "$compiler" \
        --static "$LT_DISTRO_BUILD_STATIC" \
        ${LT_DISTRO_BUILD_FRAGMENT:+--fragment "$LT_DISTRO_BUILD_FRAGMENT"}

    LT_BUILT_BUSYBOX_BINARY="$binary"
}
