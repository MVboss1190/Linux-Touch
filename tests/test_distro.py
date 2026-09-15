#!/usr/bin/env python3
"""Distro profile tests: validation, selection, compatibility and overlay.

The byte-identity test guards the whole point of this change: moving the
root filesystem definition into a distro profile must not alter what is
produced.
"""

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
LIB_DIR = os.path.join(ROOT_DIR, "scripts", "lib")
DISTRO_PY = os.path.join(LIB_DIR, "distro.py")
BUILD_SH = os.path.join(ROOT_DIR, "scripts", "build.sh")
RUN_SH = os.path.join(ROOT_DIR, "scripts", "run.sh")
REAL_PROFILE = os.path.join(ROOT_DIR, "distros", "busybox-minimal", "distro.yaml")
OVERLAY_INIT = os.path.join(ROOT_DIR, "distros", "busybox-minimal", "overlay", "init")

HAVE_CPIO = shutil.which("cpio") is not None

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_UNKNOWN_DISTRO = 2

# sha256 of the initramfs produced by the implementation that predates
# distro profiles (commit e056046), from the stand-in BusyBox below. The
# distro profile must reproduce it exactly.
LEGACY_INITRAMFS_SHA256 = (
    "3a71d93f131954576d5906086ef69b2e4e32a302ec15e343d38a6770759c20fc"
)
STAND_IN_BUSYBOX = b"#!/bin/sh\necho busybox\n"
STAND_IN_KERNEL = b"stand-in kernel image"

VALID_DISTRO = """\
schema_version: 1

id: busybox-minimal
name: BusyBox Minimal
status: development

arch_support:
  - aarch64

init_system: busybox

bootstrap:
  method: busybox
  binary: bin/busybox

rootfs:
  directories:
    - bin
    - etc

  applets:
    - sh

overlay: overlay

output:
  format: cpio.gz
"""


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


distro_lib = _import("lt_distro_under_test", "distro.py")


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def run_tool(*args):
    return subprocess.run(
        [sys.executable, DISTRO_PY, *args], capture_output=True, text=True, cwd=ROOT_DIR
    )


class DistroFixture:
    """A throwaway distros/ tree holding a single profile and overlay."""

    def __init__(self, text, directory="busybox-minimal", overlay=True):
        self.root = tempfile.mkdtemp(prefix="linux-touch-distro-")
        self.distros_dir = os.path.join(self.root, "distros")
        self.directory = os.path.join(self.distros_dir, directory)
        os.makedirs(self.directory)
        shutil.copytree(
            os.path.join(ROOT_DIR, "distros", "schema"),
            os.path.join(self.distros_dir, "schema"),
        )
        if overlay:
            overlay_dir = os.path.join(self.directory, "overlay")
            os.makedirs(overlay_dir)
            shutil.copy2(OVERLAY_INIT, os.path.join(overlay_dir, "init"))
        self.path = os.path.join(self.directory, "distro.yaml")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def temp_repo():
    destination = tempfile.mkdtemp(prefix="linux-touch-distro-build-")
    repo = os.path.join(destination, "repo")
    os.makedirs(repo)
    for entry in ("scripts", "devices", "distros", "schema", "sources.yaml"):
        source = os.path.join(ROOT_DIR, entry)
        target = os.path.join(repo, entry)
        if os.path.isdir(source):
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target)
    boot = os.path.join(repo, "kernel", "linux", "arch", "arm64", "boot")
    os.makedirs(boot)
    with open(os.path.join(boot, "Image"), "wb") as handle:
        handle.write(STAND_IN_KERNEL)
    os.makedirs(os.path.join(repo, "busybox"))
    busybox = os.path.join(repo, "busybox", "busybox")
    with open(busybox, "wb") as handle:
        handle.write(STAND_IN_BUSYBOX)
    os.chmod(busybox, 0o755)
    return repo


def build(repo, *args, env=None):
    return subprocess.run(
        [os.path.join(repo, "scripts", "build.sh"), *args],
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
    )


class ValidDistroTest(unittest.TestCase):
    def test_shipped_profile_is_valid(self):
        result = run_tool("validate", REAL_PROFILE)
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)

    def test_validate_all_passes(self):
        result = run_tool("validate-all")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("busybox-minimal: ok", result.stdout)

    def test_distro_is_discovered_from_disk(self):
        result = run_tool("list")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("busybox-minimal", result.stdout.split())

    def test_env_exposes_userspace_configuration(self):
        result = run_tool("env", "busybox-minimal", "--arch", "aarch64")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("LT_DISTRO_ID=busybox-minimal", result.stdout)
        self.assertIn("LT_DISTRO_INIT_SYSTEM=busybox", result.stdout)
        self.assertIn("LT_DISTRO_BOOTSTRAP_METHOD=busybox", result.stdout)
        self.assertIn("LT_DISTRO_BUSYBOX_PATH=bin/busybox", result.stdout)
        self.assertIn("LT_DISTRO_OUTPUT_FORMAT=cpio.gz", result.stdout)
        self.assertIn("sh mount echo uname cat ls mkdir sleep", result.stdout)

    def test_overlay_init_is_executable(self):
        self.assertTrue(
            os.access(OVERLAY_INIT, os.X_OK),
            "overlay/init must keep its executable bit or the guest cannot boot",
        )

    def test_profile_carries_no_hardware_configuration(self):
        distro = distro_lib.load_profile(REAL_PROFILE)
        for forbidden in ("runner", "platform", "resolution", "boot", "architecture"):
            self.assertNotIn(forbidden, distro)


class InvalidDistroTest(unittest.TestCase):
    def validate_text(self, text, directory="busybox-minimal", overlay=True):
        fixture = DistroFixture(text, directory, overlay)
        self.addCleanup(fixture.cleanup)
        return run_tool("validate", fixture.path)

    def test_fixture_baseline_is_valid(self):
        self.assertEqual(self.validate_text(VALID_DISTRO).returncode, EXIT_OK)

    def test_missing_required_field(self):
        result = self.validate_text(VALID_DISTRO.replace("init_system: busybox\n", ""))
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("init_system", result.stderr)

    def test_missing_schema_version(self):
        result = self.validate_text(VALID_DISTRO.replace("schema_version: 1\n", ""))
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("schema_version", result.stderr)

    def test_missing_arch_support(self):
        text = VALID_DISTRO.replace("arch_support:\n  - aarch64\n", "")
        result = self.validate_text(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("arch_support", result.stderr)

    def test_unsupported_init_system(self):
        result = self.validate_text(
            VALID_DISTRO.replace("init_system: busybox", "init_system: systemd")
        )
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("init_system", result.stderr)

    def test_unsupported_bootstrap_method(self):
        result = self.validate_text(
            VALID_DISTRO.replace("  method: busybox", "  method: pacstrap")
        )
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("bootstrap.method", result.stderr)

    def test_hardware_configuration_is_rejected(self):
        """Device concerns must not leak into a distro profile."""
        result = self.validate_text(
            VALID_DISTRO + "\nrunner:\n  type: qemu\n"
        )
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("unknown field", result.stderr)

    def test_id_directory_mismatch(self):
        result = self.validate_text(
            VALID_DISTRO.replace("id: busybox-minimal", "id: busybox-tiny")
        )
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("does not match the profile directory name", result.stderr)

    def test_missing_overlay_directory(self):
        result = self.validate_text(VALID_DISTRO, overlay=False)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("overlay", result.stderr)

    def test_busybox_bootstrap_requires_a_binary_path(self):
        text = VALID_DISTRO.replace("  binary: bin/busybox\n", "")
        result = self.validate_text(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("binary", result.stderr)


class UnknownDistroTest(unittest.TestCase):
    def test_resolve_reports_available_distros(self):
        result = run_tool("resolve", "arch")
        self.assertEqual(result.returncode, EXIT_UNKNOWN_DISTRO)
        self.assertIn("unknown distro: arch", result.stderr)
        self.assertIn("busybox-minimal", result.stderr)

    def test_build_script_rejects_unknown_distro(self):
        result = subprocess.run(
            [BUILD_SH, "virtual-phone", "--distro", "ubuntu"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        self.assertEqual(result.returncode, EXIT_UNKNOWN_DISTRO, result.stderr)
        self.assertIn("unknown distro", result.stderr)

    def test_run_script_rejects_unknown_distro(self):
        result = subprocess.run(
            [RUN_SH, "virtual-phone", "--distro", "alpine"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        self.assertEqual(result.returncode, EXIT_UNKNOWN_DISTRO, result.stderr)
        self.assertIn("unknown distro", result.stderr)

    def test_distro_option_requires_a_value(self):
        result = subprocess.run(
            [BUILD_SH, "virtual-phone", "--distro"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--distro requires a value", result.stderr)

    def test_unknown_option_is_rejected(self):
        result = subprocess.run(
            [BUILD_SH, "virtual-phone", "--rootfs=/tmp"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option", result.stderr)


class ArchitectureCompatibilityTest(unittest.TestCase):
    def test_incompatible_architecture_is_refused_by_the_loader(self):
        result = run_tool("env", "busybox-minimal", "--arch", "x86_64")
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("does not support architecture 'x86_64'", result.stderr)

    def test_incompatible_architecture_stops_the_build(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        profile = os.path.join(repo, "distros", "busybox-minimal", "distro.yaml")
        with open(profile, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(profile, "w", encoding="utf-8") as handle:
            handle.write(text.replace("  - aarch64", "  - riscv64"))

        result = build(repo, "virtual-phone")
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("does not support architecture 'aarch64'", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(repo, "out")))


class DistroSelectionTest(unittest.TestCase):
    """The distro axis must be selectable without breaking the old CLI."""

    def selected_distro(self, result):
        for line in result.stdout.splitlines():
            if line.startswith("Distro: "):
                return line.split(": ", 1)[1].strip()
        return None

    def test_default_selection(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        result = build(repo, "virtual-phone")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertEqual(self.selected_distro(result), "busybox-minimal")

    def test_explicit_selection(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        result = build(repo, "virtual-phone", "--distro", "busybox-minimal")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertEqual(self.selected_distro(result), "busybox-minimal")

    def test_equals_form_is_accepted(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        result = build(repo, "virtual-phone", "--distro=busybox-minimal")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)

    def test_bare_invocation_defaults_to_both_axes(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        result = build(repo)
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("Target: virtual-phone", result.stdout)
        self.assertEqual(self.selected_distro(result), "busybox-minimal")


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed in this environment")
class OverlayAndByteIdentityTest(unittest.TestCase):
    def build_and_get(self, repo, *args):
        result = build(repo, *args)
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        return os.path.join(
            repo, "out", "virtual-phone-busybox-minimal", "initramfs.cpio.gz"
        )

    def extract(self, archive, member):
        directory = tempfile.mkdtemp(prefix="linux-touch-extract-")
        self.addCleanup(shutil.rmtree, directory, True)
        with open(archive, "rb") as handle:
            subprocess.run(
                "gzip -cd | cpio -idm --quiet",
                shell=True,
                cwd=directory,
                stdin=handle,
                check=True,
                capture_output=True,
            )
        return os.path.join(directory, member)

    def test_initramfs_is_byte_identical_to_the_previous_implementation(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        archive = self.build_and_get(repo, "virtual-phone")
        self.assertEqual(
            sha256(archive),
            LEGACY_INITRAMFS_SHA256,
            "moving the rootfs into a distro profile changed the initramfs",
        )

    def test_staged_init_comes_from_the_overlay(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        self.build_and_get(repo, "virtual-phone")
        staged = os.path.join(repo, "os", "rootfs", "init")
        self.assertEqual(sha256(staged), sha256(OVERLAY_INIT))

    def test_editing_the_overlay_changes_the_image(self):
        """Proves the overlay is the source of /init, not a copy in build.sh."""
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        overlay_init = os.path.join(
            repo, "distros", "busybox-minimal", "overlay", "init"
        )
        with open(overlay_init, "a", encoding="utf-8") as handle:
            handle.write("\necho OVERLAY_MARKER\n")

        archive = self.build_and_get(repo, "virtual-phone")
        self.assertNotEqual(sha256(archive), LEGACY_INITRAMFS_SHA256)
        with open(self.extract(archive, "init"), encoding="utf-8") as handle:
            self.assertIn("OVERLAY_MARKER", handle.read())

    def test_missing_init_in_the_overlay_fails_the_build(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        os.remove(os.path.join(repo, "distros", "busybox-minimal", "overlay", "init"))
        result = build(repo, "virtual-phone")
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("no executable /init", result.stderr)


if __name__ == "__main__":
    unittest.main()
