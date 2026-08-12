# Hardware Abstraction

This directory defines the boundary between the mobile OS and device-specific hardware.

The long-term model is:

```text
Mobile Shell / Services
        |
        v
Hardware API
        |
        +-- Virtual device
        +-- Generic ARM64 device
        +-- Physical device profile
```

The v0.1 implementation is intentionally minimal. Do not add phone-specific code here yet; first establish stable interfaces and the virtual target.

Planned capabilities include display, touch, rotation, battery, power, audio, network, Bluetooth, camera, sensors and modem.
