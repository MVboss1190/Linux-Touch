# shellcheck shell=bash
#
# Build the pinned Linux kernel for a device.
#
# Inputs:  the device profile's kernel block, the pinned source in
#          sources.yaml, an aarch64 cross toolchain
# Outputs: the kernel image inside build/kernel/<device>/, plus a component
#          record describing exactly what was built.
#
# Reproducibility: KBUILD_BUILD_TIMESTAMP/USER/HOST are pinned so two builds
# of the same source and config agree. The kernel is not bit-for-bit
# reproducible in general; the manifest records hashes rather than claiming
# it is.

lt_build_kernel() {
    local out_dir="$1"
    local source_tree build_dir config_path image_path prefix compiler
    local source_version source_sha fragment fragments=()

    prefix="$(lt_resolve_cross_compile "$LT_DEVICE_ARCH")" || return 1
    compiler="$(lt_compiler_version "$prefix")"

    source_tree="$("$(lt_python)" "$LT_TOOLS_LIB_DIR/sources.py" \
        --manifest "$LT_SOURCES_MANIFEST" \
        extract "$LT_DEVICE_KERNEL_SOURCE" --dest-dir "$LT_BUILD_DIR/sources")" || return 1

    source_version="$("$(lt_python)" "$LT_TOOLS_LIB_DIR/sources.py" \
        --manifest "$LT_SOURCES_MANIFEST" describe "$LT_DEVICE_KERNEL_SOURCE" \
        | "$(lt_python)" -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
    source_sha="$("$(lt_python)" "$LT_TOOLS_LIB_DIR/sources.py" \
        --manifest "$LT_SOURCES_MANIFEST" describe "$LT_DEVICE_KERNEL_SOURCE" \
        | "$(lt_python)" -c 'import json,sys; print(json.load(sys.stdin)["sha256"])')"

    build_dir="$LT_BUILD_DIR/kernel/$LT_DEVICE_ID"
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

    make "${make_args[@]}" "$LT_DEVICE_KERNEL_DEFCONFIG" >/dev/null

    # Merge the device's Kconfig fragments over the defconfig.
    for fragment in $LT_DEVICE_KERNEL_FRAGMENTS; do
        if [[ ! -f "$LT_ROOT_DIR/$fragment" ]]; then
            echo "error: kernel config fragment not found: $fragment" >&2
            return 1
        fi
        fragments+=("$LT_ROOT_DIR/$fragment")
    done

    if (( ${#fragments[@]} > 0 )); then
        (
            cd "$build_dir"
            ARCH=arm64 "$source_tree/scripts/kconfig/merge_config.sh" \
                -m -O "$build_dir" "$config_path" "${fragments[@]}" >/dev/null
        )
        make "${make_args[@]}" olddefconfig >/dev/null

        # merge_config.sh warns but does not fail when an option is
        # overridden; check the result instead of trusting it.
        for fragment in "${fragments[@]}"; do
            local line
            while IFS= read -r line; do
                [[ "$line" =~ ^CONFIG_[A-Z0-9_]+=.*$ ]] || continue
                if ! grep -qxF "$line" "$config_path"; then
                    echo "error: required kernel option not set after configuration:" >&2
                    echo "       $line (from $fragment)" >&2
                    return 1
                fi
            done < "$fragment"
        done
    fi

    KBUILD_BUILD_TIMESTAMP="$(date -u -d "@$LT_STAGE_EPOCH" 2>/dev/null || date -u)" \
    KBUILD_BUILD_USER=linux-touch \
    KBUILD_BUILD_HOST=linux-touch \
        make "${make_args[@]}" -j"$(nproc)" Image >&2

    image_path="$build_dir/$LT_DEVICE_KERNEL_IMAGE"
    if [[ ! -f "$image_path" ]]; then
        echo "error: the kernel build produced no image at:" >&2
        echo "       $image_path" >&2
        return 1
    fi
    lt_require_aarch64_binary "$image_path" || return 1

    local -a record_fragments=()
    for fragment in $LT_DEVICE_KERNEL_FRAGMENTS; do
        record_fragments+=(--fragment "$fragment")
    done

    lt_write_component_record "$out_dir" kernel \
        --provenance built-from-source \
        --binary "$image_path" \
        --config "$config_path" \
        --source-name "$LT_DEVICE_KERNEL_SOURCE" \
        --source-version "$source_version" \
        --source-sha256 "$source_sha" \
        --defconfig "$LT_DEVICE_KERNEL_DEFCONFIG" \
        --cross-compile "$prefix" \
        --compiler "$compiler" \
        ${record_fragments[@]+"${record_fragments[@]}"}

    LT_BUILT_KERNEL_IMAGE="$image_path"
}
