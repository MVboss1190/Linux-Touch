# Architecture

## Layers

```text
+--------------------------------------------------+
|                 Mobile Shell / Apps              |
+--------------------------------------------------+
|             Mobile system services               |
+--------------------------------------------------+
|             Hardware Abstraction API             |
+--------------------------------------------------+
|          Linux kernel + device drivers            |
+--------------------------------------------------+
|                 Device hardware                   |
+--------------------------------------------------+
```

The OS core must not depend on a specific phone model. Device-specific behavior belongs in the hardware layer and device profiles.

## Device profile model

A device profile describes hardware capabilities and the components needed to boot and operate the device. A profile may eventually reference:

- SoC/platform
- architecture
- kernel source/configuration
- Device Tree / overlays
- firmware
- display and touch
- storage
- networking
- GPU
- audio
- sensors
- camera
- modem
- boot method
- partition layout

Profiles should describe evidence and compatibility rather than pretending unsupported hardware works.

## Compatibility states

Use explicit states for hardware capabilities:

- `supported` — tested and working
- `experimental` — partially working or insufficiently tested
- `unsupported` — known not to work
- `not_implemented` — support has not been developed yet
- `unknown` — insufficient information

## v0.1 dependency direction

```text
Device Profile
      |
      v
Build / Run tooling
      |
      +--> Kernel
      +--> Root filesystem
      +--> QEMU
               |
               v
        Wayland / compositor
               |
               v
          Qt/QML shell
```

This separation is intentional. Later a physical phone can replace QEMU without forcing the shell to understand the underlying hardware.
