# Builder

The builder turns one validated device profile plus one validated distro
profile into a build output under `out/<device>-<distro>/`.

`scripts/build.sh` is the entry point; it only orders the stages and
reports. Each stage below is a small script that can also be run on its own
as `builder/stage-<name>.sh <device> <distro> [--force]`:

| Stage | Inputs | Outputs |
|---|---|---|
| `stage-inputs.sh` | both profiles, the pinned sources or prebuilt artifacts, host tools | none (checks only) |
| `stage-kernel.sh` | device profile's kernel block + pinned Linux source, or a prebuilt Image | `out/<device>-<distro>/Image` |
| `stage-rootfs.sh` | distro profile + pinned BusyBox source, or a prebuilt binary | `os/rootfs/` (staging tree) |
| `stage-initramfs.sh` | `os/rootfs/`, `SOURCE_DATE_EPOCH` | `out/<device>-<distro>/initramfs.cpio.gz` |
| `stage-manifest.sh` | both profiles, `sources.yaml`, the artifacts | `out/<device>-<distro>/manifest.json` |

Caching is one file per stage under `out/<device>-<distro>/.stamps/`,
holding a hash of that stage's inputs. A stage is skipped when its stamp
matches and its outputs exist; `--force` ignores stamps. There is no
dependency graph and no cache eviction, by design.

`manifest.json` is authoritative for what a build produced: `run.sh`
resolves the kernel and initramfs through it and verifies their hashes
before booting.

## Source builds

The kernel and BusyBox are built from the versions pinned in `sources.yaml`:

```sh
./scripts/fetch.sh                  # download and verify the pinned sources
./scripts/build.sh virtual-phone    # build them
```

Kernel configuration belongs to the device profile (`kernel.defconfig` plus
the fragments under `kernel/config/`); BusyBox configuration belongs to the
distro profile (`bootstrap.build`, with the fragment in the profile
directory). BusyBox is linked statically because the initramfs carries no
dynamic loader.

Three modes, and which one ran is always announced and recorded in
`manifest.json` under `components`:

| Mode | Behaviour |
|---|---|
| default (`auto`) | Build from source when the sources are fetched and a cross toolchain is present; otherwise fall back to a prebuilt artifact **with a warning**, recorded as `prebuilt-unverified`. |
| `--from-source` | Require source builds. Fails rather than falling back. |
| `--prebuilt` | Require prebuilt artifacts. |

Source trees are unpacked into `build/sources/` and compiled out-of-tree
into `build/kernel/<device>/` and `build/busybox/<distro>/`, so the build
directory can be deleted without touching the repository.

Reproducibility: `KBUILD_BUILD_TIMESTAMP`, `KBUILD_BUILD_USER` and
`KBUILD_BUILD_HOST` are pinned, and BusyBox is built with
`SOURCE_DATE_EPOCH`. The manifest records the hashes rather than claiming
bit-for-bit reproducibility, which the kernel does not guarantee in
general.
