# Developing Linux-Touch

Everything here runs from a clone, with repository-relative paths. Nothing
needs to be installed into the system, and no path in this document depends
on where you put the clone.

```sh
git clone <url> Linux-Touch && cd Linux-Touch
./scripts/check-env.sh          # what is missing, and how to install it
./scripts/fetch.sh              # download the pinned sources
./scripts/build.sh virtual-phone
./scripts/run.sh virtual-phone
```

## 1. Check the environment first

```sh
./scripts/check-env.sh
```

It reports every tool, its version, and for anything missing: why it is
needed and the package that provides it, for both Arch and Debian. It exits
non-zero when something required is absent.

```sh
./scripts/check-env.sh --group source-build   # one group only
./scripts/check-env.sh --strict               # also fail on recommended items
./scripts/check-env.sh --json                 # machine readable
```

The list it reports from is `scripts/lib/requirements.py`, and the builder
checks against that same list. The environment check cannot tell you one
thing and the build another.

## 2. What is required, and why

### core — needed for every build

| Tool | Why | Arch | Debian |
|---|---|---|---|
| `python3` | runs the profile, manifest and source tooling | `python` | `python3` |
| `find` | lists root filesystem members deterministically | `findutils` | `findutils` |
| `sort` | sorts them (GNU `sort`, for `-z`) | `coreutils` | `coreutils` |
| `stat`, `touch`, `chmod` | normalise modes and timestamps | `coreutils` | `coreutils` |
| `cpio` | packs the initramfs | `cpio` | `cpio` |
| `gzip` | compresses it, with `-n` | `gzip` | `gzip` |

`cpio` must be GNU cpio 2.12 or newer: reproducible archives need
`--reproducible` and numeric `--owner`. The check probes for this rather
than trusting a version string.

### source-build — needed to compile the pinned kernel and BusyBox

| Tool | Why | Arch | Debian |
|---|---|---|---|
| aarch64 cross compiler | compiles the kernel and BusyBox | `aarch64-linux-gnu-gcc` | `gcc-aarch64-linux-gnu` |
| `make` | drives both builds | `make` | `make` |
| `tar` | unpacks the tarballs | `tar` | `tar` |
| `xz` | decompresses the kernel tarball | `xz` | `xz-utils` |
| `bzip2` | decompresses the BusyBox tarball | `bzip2` | `bzip2` |
| `bison`, `flex` | build the kernel's kconfig parser | `bison`, `flex` | `bison`, `flex` |
| `bc` | kernel timing constants | `bc` | `bc` |
| `sed`, `awk` | used throughout both build systems | `sed`, `gawk` | `sed`, `gawk` |

`CROSS_COMPILE` overrides the compiler prefix. Without it the build probes
`aarch64-linux-gnu-`, `aarch64-unknown-linux-gnu-`, `aarch64-none-linux-gnu-`.

### qemu — needed to boot

| Tool | Why | Arch | Debian |
|---|---|---|---|
| `qemu-system-aarch64` | runs the built system | `qemu-system-aarch64` | `qemu-system-arm` |

### recommended

| Tool | Why | Arch | Debian |
|---|---|---|---|
| `file` | verifies built binaries are aarch64 and static; those checks are skipped without it | `file` | `file` |
| libelf headers | some kernel configurations need them | `libelf` | `libelf-dev` |
| OpenSSL headers | needed by configurations that sign modules | `openssl` | `libssl-dev` |
| `git` | lets the manifest record the commit built from | `git` | `git` |

On Arch, one line for a full source build and boot:

```sh
sudo pacman -S --needed python findutils coreutils cpio gzip make tar xz \
    bzip2 bison flex bc sed gawk aarch64-linux-gnu-gcc qemu-system-aarch64 \
    file libelf openssl git
```

## 3. The normal workflow

### fetch

```sh
./scripts/fetch.sh              # every pinned source
./scripts/fetch.sh busybox      # just one
```

Downloads the versions pinned in `sources.yaml` into `.cache/sources/` and
verifies each against the recorded sha256. A mismatch is discarded, never
used. Nothing else in the build reaches the network.

### build

```sh
./scripts/build.sh virtual-phone
./scripts/build.sh virtual-phone --distro busybox-minimal
```

A build is one device profile plus one distro profile. The device says
where it runs (architecture, kernel, boot, runner); the distro says what
runs (init, root filesystem, BusyBox).

| Flag | Effect |
|---|---|
| *(default)* | build from source when the sources are fetched and a toolchain is present, otherwise fall back to a prebuilt artifact **with a warning** |
| `--from-source` | require source builds; fail rather than fall back |
| `--prebuilt` | require prebuilt artifacts |
| `--force` | ignore stage caches |
| `--no-legacy-copy` | skip the legacy `os/images/` copy |

The first source build compiles a kernel and takes a while. Later builds
reuse the stage caches; `--force` rebuilds.

### test

```sh
./tests/run-tests.sh                                   # fast, always safe
LT_SOURCE_BUILD_TESTS=1 ./tests/run-tests.sh           # also compile
LT_BOOT_TESTS=1 ./tests/run-tests.sh                   # also boot QEMU
```

### boot

```sh
./scripts/run.sh virtual-phone
python3 scripts/lib/boot.py --log /tmp/serial.log      # non-interactive
```

`run.sh` boots what the manifest describes, after checking the artifacts
are present and their hashes match. `boot.py` does the same but asserts the
boot markers and exits, which is what the boot test uses.

## 4. Environment variables

| Variable | Effect |
|---|---|
| `LT_SOURCE_BUILD_TESTS=1` | run the tests that compile the pinned sources |
| `LT_BOOT_TESTS=1` | run the test that boots QEMU for real |
| `LT_BOOT_TIMEOUT` | seconds the boot test waits (default 180) |
| `CROSS_COMPILE` | cross compiler prefix to use |
| `SOURCE_DATE_EPOCH` | build timestamp; defaults to the constant pinned in `sources.yaml`, never the wall clock |
| `LT_SOURCE_CACHE_DIR` | where fetched tarballs live (default `.cache/sources/`) |
| `LT_BUILD_DIR` | where sources are unpacked and compiled (default `build/`) |
| `LT_DEVICES_DIR`, `LT_DISTROS_DIR` | where profiles are discovered |

Tests whose prerequisites are missing are skipped, never reported as
passing.

## 5. Where things end up

```
.cache/sources/         fetched tarballs (verified against sources.yaml)
build/sources/          unpacked source trees
build/kernel/<device>/  kernel build directory
build/busybox/<distro>/ BusyBox build directory
out/<device>-<distro>/  the build output
os/rootfs/              staging tree for the initramfs
os/images/              legacy copy, kept for compatibility
```

Everything above is generated and git-ignored. Deleting `build/` and
`out/` costs only rebuild time.

`out/<device>-<distro>/` is the canonical output:

```
Image                 the kernel
initramfs.cpio.gz     the userspace
manifest.json         what this build is
.stamps/              stage caches
.build/               per-component build records
```

## 6. manifest.json

The manifest is authoritative: `run.sh` and the boot test resolve artifacts
through it rather than guessing paths, and refuse to boot output whose
artifacts are missing, corrupt, or built for another device or distro.

```sh
python3 scripts/lib/buildout.py --out-dir out/virtual-phone-busybox-minimal verify
```

It records the device and distro profile identities and hashes, the
architecture, artifact paths and sha256 hashes, the pinned source
information, `SOURCE_DATE_EPOCH`, toolchain information, and per-component
provenance: whether the kernel and BusyBox were **built from the pinned
source** or **copied from a prebuilt artifact**. A copied artifact is
recorded as `prebuilt-unverified` and is never attributed to a pinned
source.

Paths inside it are relative to the repository root, and it carries no
build timestamp, so two builds of the same inputs produce the same
manifest. That is a statement about this manifest, not a claim that the
kernel is bit-for-bit reproducible.

## 7. Further reading

- `builder/README.md` — the build stages and how caching works
- `tests/README.md` — the test tiers
- `docs/architecture.md` — the layering
- `sources.yaml` — the pinned sources and how they were pinned
