#!/usr/bin/env python3
"""Boot a build output in QEMU and assert it reaches a working shell.

The canonical output is verified first (manifest present and readable,
artifacts on disk, hashes matching), then ./scripts/run.sh is invoked --
deliberately, rather than assembling a QEMU command line here, so the test
exercises the same arguments a person gets and the two can never drift.

Serial output is captured and scanned for markers. Once the guest reaches
an interactive shell the probe command's echo comes back, which is a
stronger signal than a prompt string: it proves the shell is executing
commands, not merely that some bytes appeared.

QEMU is always terminated, and a hung guest is bounded by --timeout.

Exit codes:
  0  booted and every marker was seen
  1  boot failed: a marker was missed, the kernel panicked, or QEMU exited
  2  usage error
  3  QEMU is not available (never reported as success)
  4  timed out
  5  the build output is missing, incomplete or corrupt
"""

from __future__ import annotations

import argparse
import errno
import importlib.util
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import time

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(LIB_DIR))
QEMU_BINARY = "qemu-system-aarch64"

# The console echoes whatever is typed, so a probe whose output is the same
# text as its input would match its own echo and pass without a shell ever
# running. Make the shell compute the answer: the echoed input contains
# "$((6*7))" while only real shell output can contain "LT_SHELL_OK_42".
SHELL_PROBE_COMMAND = "echo LT_SHELL_OK_$((6*7))"
SHELL_PROBE_MARKER = "LT_SHELL_OK_42"
PROBE_INTERVAL = 3
DEFAULT_TIMEOUT = 120

EXIT_OK = 0
EXIT_BOOT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_QEMU = 3
EXIT_TIMEOUT = 4
EXIT_NO_BUILD_OUTPUT = 5


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_buildout = _load("lt_buildout", "buildout.py")


class BootError(Exception):
    def __init__(self, message, exit_code=EXIT_BOOT_FAILED, transcript=None):
        super().__init__(message)
        self.exit_code = exit_code
        self.transcript = transcript or ""


def tail(text, lines=25):
    kept = text.splitlines()[-lines:]
    return "\n".join("  | %s" % line for line in kept)


def expected_markers(manifest):
    """What a healthy boot must print, in the order it should appear."""
    markers = [
        ("banner", re.compile(r"Welcome to Linux-Touch")),
        ("architecture", re.compile(r"Architecture:\s+aarch64\b")),
    ]

    kernel = (manifest.get("components") or {}).get("kernel") or {}
    source = kernel.get("source") or {}
    version = source.get("version")
    if kernel.get("provenance") == "built-from-source" and version:
        markers.append(
            ("kernel version %s" % version, re.compile(r"Kernel:\s+%s\b" % re.escape(version)))
        )
    markers.append(("interactive shell", re.compile(re.escape(SHELL_PROBE_MARKER))))
    return markers


def verify_output(out_dir, root, device, distro):
    """Preflight the canonical output before starting anything."""
    try:
        manifest, artifacts = _buildout.inspect(out_dir, root, device, distro)
    except _buildout.OutputError as error:
        raise BootError(
            "the build output cannot be booted.\n%s\n"
            "The boot test needs a verified build in %s; the legacy "
            "os/images layout is deliberately not accepted here."
            % (error, out_dir),
            EXIT_NO_BUILD_OUTPUT,
        )
    return manifest, artifacts


def _terminate(process):
    """Stop QEMU, escalating only if it ignores the polite request."""
    if process.poll() is not None:
        return process.returncode
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        process.terminate()
    try:
        return process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()
        try:
            return process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return None


def boot(
    device,
    distro,
    root=ROOT_DIR,
    timeout=DEFAULT_TIMEOUT,
    log_path=None,
    quiet=False,
):
    if shutil.which(QEMU_BINARY) is None:
        raise BootError(
            "%s is not installed, so no boot can be attempted.\n"
            "Install it (for example the qemu-system-arm package) and run "
            "the boot test again. This is never reported as a passing boot."
            % QEMU_BINARY,
            EXIT_NO_QEMU,
        )

    out_dir = os.path.join(root, "out", "%s-%s" % (device, distro))
    manifest, artifacts = verify_output(out_dir, root, device, distro)
    markers = expected_markers(manifest)

    runner = os.path.join(root, "scripts", "run.sh")
    if not os.access(runner, os.X_OK):
        raise BootError("cannot execute %s" % runner, EXIT_USAGE)

    process = subprocess.Popen(
        [runner, device, "--distro", distro],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    transcript = ""
    seen = {}
    console_closed = False
    last_probe = 0.0
    deadline = time.monotonic() + timeout
    panic = re.compile(r"Kernel panic[^\n]*")

    try:
        while True:
            if time.monotonic() > deadline:
                missing = [name for name, _ in markers if name not in seen]
                raise BootError(
                    "timed out after %ds waiting for: %s\n"
                    "Serial output ended with:\n%s"
                    % (timeout, ", ".join(missing), tail(transcript)),
                    EXIT_TIMEOUT,
                    transcript,
                )

            ready, _, _ = select.select([process.stdout], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(process.stdout.fileno(), 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    chunk = b""
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    transcript += text
                    if not quiet:
                        sys.stderr.write(text)
                        sys.stderr.flush()
                else:
                    # An empty read is end of file. select() reports a
                    # closed pipe as readable forever, so this is the only
                    # reliable signal that the console has gone away.
                    console_closed = True

            found = panic.search(transcript)
            if found:
                raise BootError(
                    "the guest panicked: %s\nSerial output ended with:\n%s"
                    % (found.group(0), tail(transcript)),
                    EXIT_BOOT_FAILED,
                    transcript,
                )

            for name, pattern in markers:
                if name not in seen and pattern.search(transcript):
                    seen[name] = True

            # Ask the shell to prove it is alive once init has run. Repeat
            # until it answers: a probe sent before /bin/sh is reading may
            # never be acted on, and a prompt alone would not prove the
            # shell can execute anything.
            now = time.monotonic()
            if (
                "architecture" in seen
                and "interactive shell" not in seen
                and now - last_probe > PROBE_INTERVAL
            ):
                last_probe = now
                try:
                    process.stdin.write(("%s\n" % SHELL_PROBE_COMMAND).encode())
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass

            if len(seen) == len(markers):
                return {
                    "markers": [name for name, _ in markers],
                    "transcript": transcript,
                    "manifest": manifest,
                    "artifacts": artifacts,
                }

            if console_closed or process.poll() is not None:
                try:
                    status = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    status = None
                missing = [name for name, _ in markers if name not in seen]
                raise BootError(
                    "QEMU exited with status %s before the guest finished "
                    "booting.\nStill waiting for: %s\nSerial output ended "
                    "with:\n%s"
                    % (status, ", ".join(missing), tail(transcript)),
                    EXIT_BOOT_FAILED,
                    transcript,
                )
    finally:
        _terminate(process)
        if process.stdin:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        if log_path:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write(transcript)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", default="virtual-phone")
    parser.add_argument("--distro", default="busybox-minimal")
    parser.add_argument("--root", default=ROOT_DIR)
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("LT_BOOT_TIMEOUT") or DEFAULT_TIMEOUT),
        help="seconds to wait for a full boot before giving up",
    )
    parser.add_argument("--log", default=None, help="write the serial transcript here")
    parser.add_argument(
        "--quiet", action="store_true", help="do not mirror serial output while booting"
    )

    args = parser.parse_args(argv)

    try:
        result = boot(
            args.device,
            args.distro,
            root=args.root,
            timeout=args.timeout,
            log_path=args.log,
            quiet=args.quiet,
        )
    except BootError as error:
        print("error: %s" % error, file=sys.stderr)
        return error.exit_code

    print("boot ok: %s / %s" % (args.device, args.distro))
    for name in result["markers"]:
        print("  saw %s" % name)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
