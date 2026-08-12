# OS

This directory contains root filesystem configuration and system services.

The v0.1 userspace target is deliberately small:

- systemd
- Wayland session dependencies
- compositor/session startup
- Qt/QML runtime
- Linux-Touch shell

The build should remain reproducible and should not depend on files from the host system beyond explicitly declared build dependencies.
