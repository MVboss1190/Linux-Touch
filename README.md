# Linux-Touch

Linux-Touch is an experimental Linux distribution and development platform designed for touchscreen phones and tablets.

The long-term goal is to provide a mobile-first Linux userspace, a hardware abstraction layer, device profiles, and a PC builder/installer capable of producing images for supported mobile devices.

## Current status

**Development stage: v0.1 — foundation / virtual phone**

The first milestone deliberately targets QEMU rather than physical phones. We want a reproducible ARM64 development target before dealing with device-specific bootloaders, kernels, firmware, and drivers.

### v0.1 target

- ARM64 Linux system
- QEMU virtual-phone target
- systemd userspace
- Wayland graphics stack
- touchscreen-oriented shell foundation
- device profile format
- reproducible build/run scripts

Physical phone support, Android compatibility, cellular modem support, camera support, and automated flashing are out of scope for v0.1.

## Building it

```sh
./scripts/check-env.sh          # what this machine is missing
./scripts/fetch.sh              # download the pinned sources
./scripts/build.sh virtual-phone
./scripts/run.sh virtual-phone
```

See [docs/development.md](docs/development.md) for prerequisites, the full
workflow and where the output goes.

## Repository layout

```text
Linux-Touch/
├── docs/              Project documentation
├── devices/           Hardware/device profiles
├── kernel/            Kernel configuration and future patches
├── os/                Root filesystem and system configuration
├── shell/             Mobile shell
├── hardware/          Hardware abstraction interfaces
├── builder/           Future image builder
├── tests/             Boot and integration tests
└── scripts/           Development/build helpers
```

## Development philosophy

1. Build the OS independently from device-specific hardware ports.
2. Treat hardware support as data-driven device profiles.
3. Prefer upstream Linux drivers and standards where possible.
4. Keep proprietary firmware/vendor components isolated from the OS core.
5. Test first in QEMU, then on development hardware.
6. Never flash an image without explicit device and boot-chain validation.

## Roadmap

See [ROADMAP.md](ROADMAP.md) and [docs/v0.1.md](docs/v0.1.md).

## License

Linux-Touch is currently released under the MIT License. See [LICENSE](LICENSE).
