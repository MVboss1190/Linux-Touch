# Builder

The builder will eventually turn a device profile plus OS configuration into a bootable image.

Planned responsibilities:

1. Validate the device profile.
2. Resolve the kernel/toolchain configuration.
3. Build or fetch the selected kernel.
4. Assemble the root filesystem.
5. Add required firmware and device files.
6. Generate boot artifacts.
7. Run validation tests.
8. Produce artifacts for QEMU or a physical device.

For v0.1 the builder is intentionally only a planned component; the shell scripts are the temporary entry points while the build pipeline is designed.
