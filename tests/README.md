# Tests

```sh
./tests/run-tests.sh
```

Three tiers. The default tier is fast and needs nothing beyond Python, GNU
cpio and gzip; the other two are opt-in because they compile a kernel and
boot a virtual machine.

| Tier | How to run | What it needs |
|---|---|---|
| default | `./tests/run-tests.sh` | Python 3, cpio, gzip |
| source build | `LT_SOURCE_BUILD_TESTS=1 ./tests/run-tests.sh` | fetched sources, aarch64 cross toolchain |
| real boot | `LT_BOOT_TESTS=1 ./tests/run-tests.sh` | QEMU, plus a build in `out/` |

Tests whose prerequisites are missing are skipped, never passed.

| File | Covers |
|---|---|
| `test_profile.py` | device profile schema, discovery, validation |
| `test_distro.py` | distro profile schema, selection, overlay |
| `test_sources.py` | pinned source manifest, checksum verification |
| `test_build.py` | initramfs determinism, manifest, guarded cleanup |
| `test_output.py` | canonical output, artifact verification, runner |
| `test_sourcebuild.py` | building the pinned Linux and BusyBox, provenance |
| `test_boot.py` | booting in QEMU, and the boot harness itself |

## Booting for real

```sh
./scripts/fetch.sh                                    # pinned sources
./scripts/build.sh virtual-phone --from-source        # kernel + BusyBox
LT_BOOT_TESTS=1 ./tests/run-tests.sh                  # boot it
```

The boot test starts QEMU through `scripts/run.sh`, so it uses the same
arguments a person gets, and reads the artifacts from
`out/<device>-<distro>/` after verifying them against `manifest.json`. It
asserts the banner, the architecture, the kernel version recorded in the
manifest, and a shell that answers a probe command. QEMU is always
terminated and `--timeout` bounds a hung guest.

`scripts/lib/boot.py` can also be run directly:

```sh
python3 scripts/lib/boot.py --device virtual-phone --log /tmp/serial.log
```

Exit codes: 0 booted, 1 boot failed, 2 usage, 3 QEMU unavailable,
4 timed out, 5 build output missing or corrupt.
