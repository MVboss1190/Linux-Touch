# Builder

The builder turns one validated device profile plus one validated distro
profile into a build output under `out/<device>-<distro>/`.

`scripts/build.sh` is the entry point; it only orders the stages and
reports. Each stage below is a small script that can also be run on its own
as `builder/stage-<name>.sh <device> <distro> [--force]`:

| Stage | Inputs | Outputs |
|---|---|---|
| `stage-inputs.sh` | both profiles, prebuilt kernel and BusyBox, host tools | none (checks only) |
| `stage-kernel.sh` | prebuilt kernel Image | `out/<device>-<distro>/Image` |
| `stage-rootfs.sh` | distro profile, prebuilt BusyBox | `os/rootfs/` (staging tree) |
| `stage-initramfs.sh` | `os/rootfs/`, `SOURCE_DATE_EPOCH` | `out/<device>-<distro>/initramfs.cpio.gz` |
| `stage-manifest.sh` | both profiles, `sources.yaml`, the artifacts | `out/<device>-<distro>/manifest.json` |

Caching is one file per stage under `out/<device>-<distro>/.stamps/`,
holding a hash of that stage's inputs. A stage is skipped when its stamp
matches and its outputs exist; `--force` ignores stamps. There is no
dependency graph and no cache eviction, by design.

`manifest.json` is authoritative for what a build produced: `run.sh`
resolves the kernel and initramfs through it and verifies their hashes
before booting.

Not yet implemented: compiling the kernel or BusyBox from the pinned
sources in `sources.yaml`. Both are consumed as prebuilt inputs, and the
manifest marks them `prebuilt-unverified`.
