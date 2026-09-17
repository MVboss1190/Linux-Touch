#!/usr/bin/env python3
"""Boot tests.

Two tiers:

* Harness tests always run. They drive scripts/lib/boot.py against a stand-in
  console program named qemu-system-aarch64, so the marker detection,
  diagnostics, clean termination and timeout can be exercised anywhere. These
  boot no guest and prove nothing about the kernel; they check the harness.
* The real boot test runs only with LT_BOOT_TESTS=1, with QEMU installed and
  a verified build in out/. It starts the actual guest.

A missing QEMU is always a skip or an error, never a pass.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
BOOT_PY = os.path.join(ROOT_DIR, "scripts", "lib", "boot.py")

DEVICE = "virtual-phone"
DISTRO = "busybox-minimal"
OUT_NAME = "%s-%s" % (DEVICE, DISTRO)
REAL_OUT_DIR = os.path.join(ROOT_DIR, "out", OUT_NAME)

EXIT_OK = 0
EXIT_BOOT_FAILED = 1
EXIT_NO_QEMU = 3
EXIT_TIMEOUT = 4
EXIT_NO_BUILD_OUTPUT = 5

HAVE_CPIO = shutil.which("cpio") is not None
HAVE_QEMU = shutil.which("qemu-system-aarch64") is not None
BOOT_TESTS = os.environ.get("LT_BOOT_TESTS") == "1"
REAL_BUILD_PRESENT = os.path.isfile(os.path.join(REAL_OUT_DIR, "manifest.json"))

EXPECTED_QEMU_ARGV = [
    "-machine", "virt",
    "-cpu", "cortex-a72",
    "-m", "1024",
    "-nographic",
    "-kernel", os.path.join(REAL_OUT_DIR, "Image"),
    "-initrd", os.path.join(REAL_OUT_DIR, "initramfs.cpio.gz"),
    "-append", "console=ttyAMA0",
]

# What a healthy guest prints, reproduced by the stand-in console.
SIMULATED_BOOT = r"""
echo "[    0.000000] Booting Linux on physical CPU 0x0000000000"
echo "[    1.212522] Run /init as init process"
echo
echo "================================"
echo "       Welcome to Linux-Touch"
echo "================================"
echo
echo "Architecture: aarch64"
echo "Kernel:       %(version)s"
echo
while IFS= read -r line; do
    # A real console echoes the typed line back before the shell runs it.
    echo "$line"
    case "$line" in
        *'echo LT_SHELL_OK_'*) echo "LT_SHELL_OK_42" ;;
    esac
done
"""


class Workspace:
    """A repository copy with a stand-in console program on PATH."""

    def __init__(self):
        self.base = tempfile.mkdtemp(prefix="linux-touch-boot-")
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(self.repo)
        for entry in ("builder", "scripts", "devices", "distros", "schema", "kernel", "sources.yaml"):
            source = os.path.join(ROOT_DIR, entry)
            target = os.path.join(self.repo, entry)
            if os.path.isdir(source):
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__", "linux")
                )
            else:
                shutil.copy2(source, target)

        boot = os.path.join(self.repo, "kernel", "linux", "arch", "arm64", "boot")
        os.makedirs(boot, exist_ok=True)
        with open(os.path.join(boot, "Image"), "wb") as handle:
            handle.write(b"stand-in kernel image")
        os.makedirs(os.path.join(self.repo, "busybox"), exist_ok=True)
        busybox = os.path.join(self.repo, "busybox", "busybox")
        with open(busybox, "wb") as handle:
            handle.write(b"#!/bin/sh\necho busybox\n")
        os.chmod(busybox, 0o755)

        self.bin = os.path.join(self.base, "bin")
        os.makedirs(self.bin)
        self.argv_log = os.path.join(self.base, "qemu-argv")
        self.signal_log = os.path.join(self.base, "qemu-signal")

    @property
    def out_dir(self):
        return os.path.join(self.repo, "out", OUT_NAME)

    def build(self):
        result = subprocess.run(
            [os.path.join(self.repo, "scripts", "build.sh"), DEVICE, "--distro", DISTRO, "--prebuilt"],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )
        assert result.returncode == 0, result.stderr
        return result

    def install_console(self, body):
        """Install a stand-in program named like QEMU. It boots nothing."""
        path = os.path.join(self.bin, "qemu-system-aarch64")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\n")
            handle.write('printf "%s\\n" "$@" > ' + self.argv_log + "\n")
            handle.write(body)
        os.chmod(path, 0o755)
        return path

    def manifest(self):
        with open(os.path.join(self.out_dir, "manifest.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def write_manifest(self, manifest):
        with open(os.path.join(self.out_dir, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")

    def run_boot(self, *args, with_qemu=True, timeout=60):
        environment = dict(os.environ)
        path = environment["PATH"]
        if with_qemu:
            environment["PATH"] = self.bin + os.pathsep + path
        else:
            # Point PATH at a directory with nothing in it. Emptying PATH
            # instead would be useless: shutil.which falls back to
            # os.defpath when PATH is empty, and would find QEMU again.
            empty = os.path.join(self.base, "empty-bin")
            os.makedirs(empty, exist_ok=True)
            environment["PATH"] = empty
        return subprocess.run(
            [
                sys.executable, BOOT_PY,
                "--root", self.repo,
                "--device", DEVICE,
                "--distro", DISTRO,
                "--quiet",
                *args,
            ],
            capture_output=True,
            text=True,
            cwd=self.repo,
            env=environment,
            timeout=timeout,
        )

    def argv(self):
        with open(self.argv_log, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        self.addCleanup(self.workspace.cleanup)


class MissingQemuTest(WorkspaceTest):
    def test_missing_qemu_is_reported_not_passed(self):
        """Requirement: never report success when QEMU is unavailable."""
        result = self.workspace.run_boot(with_qemu=False)
        self.assertEqual(result.returncode, EXIT_NO_QEMU)
        self.assertIn("qemu-system-aarch64 is not installed", result.stderr)
        self.assertNotIn("boot ok", result.stdout)


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed")
class BuildOutputDiagnosticsTest(WorkspaceTest):
    def test_reports_when_nothing_has_been_built(self):
        self.workspace.install_console("exit 0\n")
        result = self.workspace.run_boot()
        self.assertEqual(result.returncode, EXIT_NO_BUILD_OUTPUT)
        self.assertIn("no build output", result.stderr)
        self.assertIn("build.sh", result.stderr)

    def test_reports_a_missing_artifact(self):
        self.workspace.build()
        self.workspace.install_console("exit 0\n")
        os.remove(os.path.join(self.workspace.out_dir, "Image"))
        result = self.workspace.run_boot()
        self.assertEqual(result.returncode, EXIT_NO_BUILD_OUTPUT)
        self.assertIn("missing artifact 'kernel_image'", result.stderr)

    def test_reports_a_corrupt_artifact(self):
        self.workspace.build()
        self.workspace.install_console("exit 0\n")
        with open(os.path.join(self.workspace.out_dir, "initramfs.cpio.gz"), "ab") as handle:
            handle.write(b"tampered")
        result = self.workspace.run_boot()
        self.assertEqual(result.returncode, EXIT_NO_BUILD_OUTPUT)
        self.assertIn("does not match the manifest", result.stderr)

    def test_reports_a_corrupt_manifest(self):
        self.workspace.build()
        self.workspace.install_console("exit 0\n")
        with open(os.path.join(self.workspace.out_dir, "manifest.json"), "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        result = self.workspace.run_boot()
        self.assertEqual(result.returncode, EXIT_NO_BUILD_OUTPUT)
        self.assertIn("unreadable manifest", result.stderr)

    def test_refuses_output_built_for_another_device(self):
        self.workspace.build()
        self.workspace.install_console("exit 0\n")
        manifest = self.workspace.manifest()
        manifest["device"]["id"] = "other-phone"
        self.workspace.write_manifest(manifest)
        result = self.workspace.run_boot()
        self.assertEqual(result.returncode, EXIT_NO_BUILD_OUTPUT)
        self.assertIn("produced for device 'other-phone'", result.stderr)

    def test_does_not_accept_the_legacy_layout(self):
        """The legacy path still exists for run.sh, but never for a boot test."""
        self.workspace.build()
        self.workspace.install_console("exit 0\n")
        shutil.rmtree(os.path.join(self.workspace.repo, "out"))
        result = self.workspace.run_boot()
        self.assertEqual(result.returncode, EXIT_NO_BUILD_OUTPUT)
        self.assertIn("legacy os/images layout is deliberately not accepted", result.stderr)


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed")
class HarnessTest(WorkspaceTest):
    """Drives the harness with a stand-in console. No guest is booted."""

    def test_detects_every_marker_on_a_healthy_console(self):
        self.workspace.build()
        version = self.workspace.manifest()["components"]["kernel"].get("source")
        # The prebuilt build has no source version, so the version marker is
        # not asserted; the other three must all be found.
        self.assertIsNone(version)
        self.workspace.install_console(SIMULATED_BOOT % {"version": "6.12.109"})
        result = self.workspace.run_boot()
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        for marker in ("banner", "architecture", "interactive shell"):
            self.assertIn("saw %s" % marker, result.stdout)

    def test_passes_the_exact_qemu_arguments_through(self):
        self.workspace.build()
        self.workspace.install_console(SIMULATED_BOOT % {"version": "6.12.109"})
        self.assertEqual(self.workspace.run_boot().returncode, EXIT_OK)
        self.assertEqual(
            self.workspace.argv(),
            [
                "-machine", "virt",
                "-cpu", "cortex-a72",
                "-m", "1024",
                "-nographic",
                "-kernel", os.path.join(self.workspace.out_dir, "Image"),
                "-initrd", os.path.join(self.workspace.out_dir, "initramfs.cpio.gz"),
                "-append", "console=ttyAMA0",
            ],
        )

    def test_a_probe_echo_alone_does_not_count_as_a_shell(self):
        """A console that only echoes input must not satisfy the shell marker."""
        self.workspace.build()
        self.workspace.install_console(
            SIMULATED_BOOT.replace(
                "        *'echo LT_SHELL_OK_'*) echo \"LT_SHELL_OK_42\" ;;\n", ""
            )
            % {"version": "6.12.109"}
        )
        result = self.workspace.run_boot("--timeout", "8", timeout=90)
        self.assertEqual(result.returncode, EXIT_TIMEOUT)
        self.assertIn("interactive shell", result.stderr)

    def test_reports_a_kernel_panic(self):
        self.workspace.build()
        self.workspace.install_console(
            'echo "[    1.2] Run /init as init process"\n'
            'echo "[    1.5] Kernel panic - not syncing: Attempted to kill init!"\n'
            "exit 1\n"
        )
        result = self.workspace.run_boot()
        self.assertEqual(result.returncode, EXIT_BOOT_FAILED)
        self.assertIn("panicked", result.stderr)
        self.assertIn("Attempted to kill init", result.stderr)

    def test_reports_an_early_exit(self):
        self.workspace.build()
        self.workspace.install_console('echo "[    0.1] Booting"\nexit 0\n')
        result = self.workspace.run_boot()
        self.assertEqual(result.returncode, EXIT_BOOT_FAILED)
        self.assertIn("exited with status", result.stderr)
        self.assertIn("Still waiting for", result.stderr)

    def test_times_out_instead_of_hanging_forever(self):
        self.workspace.build()
        self.workspace.install_console(
            'trap "echo terminated > %s; exit 0" TERM\n'
            'echo "[    0.1] Booting"\n'
            "while true; do sleep 0.2; done\n" % self.workspace.signal_log
        )
        result = self.workspace.run_boot("--timeout", "5", timeout=90)
        self.assertEqual(result.returncode, EXIT_TIMEOUT)
        self.assertIn("timed out after 5s", result.stderr)
        self.assertIn("Serial output ended with", result.stderr)

    def test_terminates_the_console_cleanly(self):
        """Requirement: QEMU is stopped politely, not left running."""
        self.workspace.build()
        self.workspace.install_console(
            'trap "echo terminated > %s; exit 0" TERM\n'
            'echo "[    0.1] Booting"\n'
            "while true; do sleep 0.2; done\n" % self.workspace.signal_log
        )
        self.workspace.run_boot("--timeout", "5", timeout=90)
        self.assertTrue(
            os.path.isfile(self.workspace.signal_log),
            "the console was not terminated with SIGTERM",
        )

    def test_writes_a_transcript_when_asked(self):
        self.workspace.build()
        self.workspace.install_console(SIMULATED_BOOT % {"version": "6.12.109"})
        log = os.path.join(self.workspace.base, "serial.log")
        self.assertEqual(self.workspace.run_boot("--log", log).returncode, EXIT_OK)
        with open(log, encoding="utf-8") as handle:
            self.assertIn("Welcome to Linux-Touch", handle.read())


@unittest.skipUnless(
    BOOT_TESTS and HAVE_QEMU and REAL_BUILD_PRESENT,
    "set LT_BOOT_TESTS=1 with QEMU installed and a build in out/ to boot for real",
)
class RealBootTest(unittest.TestCase):
    """Boots the actual guest in QEMU."""

    @classmethod
    def setUpClass(cls):
        cls.log = tempfile.NamedTemporaryFile(suffix=".log", delete=False).name
        cls.result = subprocess.run(
            [
                sys.executable, BOOT_PY,
                "--device", DEVICE,
                "--distro", DISTRO,
                "--quiet",
                "--log", cls.log,
                "--timeout", os.environ.get("LT_BOOT_TIMEOUT", "180"),
            ],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
            timeout=600,
        )
        with open(cls.log, encoding="utf-8") as handle:
            cls.transcript = handle.read()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.log)

    def test_the_guest_boots(self):
        self.assertEqual(self.result.returncode, EXIT_OK, self.result.stderr[-3000:])

    def test_reaches_the_linux_touch_banner(self):
        self.assertIn("Welcome to Linux-Touch", self.transcript)

    def test_reports_the_expected_architecture(self):
        self.assertRegex(self.transcript, r"Architecture:\s+aarch64")

    def test_runs_the_kernel_version_from_the_manifest(self):
        with open(os.path.join(REAL_OUT_DIR, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        kernel = manifest["components"]["kernel"]
        if kernel["provenance"] != "built-from-source":
            self.skipTest("this output's kernel was not built from a pinned source")
        version = kernel["source"]["version"]
        self.assertRegex(self.transcript, r"Kernel:\s+%s\b" % version.replace(".", r"\."))

    def test_reaches_an_interactive_shell(self):
        # Only the shell can produce this: the echoed input says "$((6*7))".
        self.assertIn("LT_SHELL_OK_42", self.transcript)
        self.assertIn("saw interactive shell", self.result.stdout)

    def test_does_not_panic(self):
        self.assertNotIn("Kernel panic", self.transcript)


if __name__ == "__main__":
    unittest.main()
