#!/usr/bin/env python3
"""Device profile schema and discovery tests.

Run with ./tests/run-tests.sh or `python3 -m unittest discover -s tests`.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
LIB_DIR = os.path.join(ROOT_DIR, "scripts", "lib")
PROFILE_PY = os.path.join(LIB_DIR, "profile.py")
REAL_PROFILE = os.path.join(ROOT_DIR, "devices", "virtual-phone", "device.yaml")

sys.path.insert(0, LIB_DIR)
import profile as profile_lib  # noqa: E402

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_UNKNOWN_DEVICE = 2

VALID_PROFILE = """\
schema_version: 1

id: virtual-phone
name: Linux-Touch Virtual Phone
status: development

architecture: aarch64

form_factor: phone

kernel:
  source: linux
  defconfig: defconfig
  image: arch/arm64/boot/Image
  config_fragments:
    - kernel/config/virtual-phone.fragment

resolution:
  width: 1080
  height: 2400

orientation:
  default: portrait
  supported:
    - portrait
    - landscape

input:
  touchscreen: true

hardware:
  display: not_implemented
  touchscreen: not_implemented

runner:
  type: qemu

  qemu:
    machine: virt
    cpu: cortex-a72
    memory: 1024
    console: ttyAMA0

boot:
  method: qemu
"""


def run_tool(*args):
    return subprocess.run(
        [sys.executable, PROFILE_PY, *args],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
    )


class ProfileFixture:
    """A throwaway devices/ tree holding a single profile."""

    def __init__(self, text, directory="virtual-phone"):
        self.root = tempfile.mkdtemp(prefix="linux-touch-test-")
        self.devices_dir = os.path.join(self.root, "devices")
        device_dir = os.path.join(self.devices_dir, directory)
        os.makedirs(device_dir)
        self.path = os.path.join(device_dir, "device.yaml")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class ValidProfileTest(unittest.TestCase):
    def test_shipped_profile_is_valid(self):
        result = run_tool("validate", REAL_PROFILE)
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)

    def test_validate_all_passes(self):
        result = run_tool("validate-all")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("virtual-phone: ok", result.stdout)

    def test_device_is_discovered_from_disk(self):
        result = run_tool("list")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("virtual-phone", result.stdout.split())

    def test_env_exposes_runner_configuration(self):
        result = run_tool("env", "virtual-phone")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("LT_DEVICE_ID=virtual-phone", result.stdout)
        self.assertIn("LT_QEMU_MACHINE=virt", result.stdout)
        self.assertIn("LT_QEMU_CPU=cortex-a72", result.stdout)
        self.assertIn("LT_QEMU_MEMORY=1024", result.stdout)
        self.assertIn("LT_QEMU_CONSOLE=ttyAMA0", result.stdout)

    def test_fixture_baseline_is_valid(self):
        fixture = ProfileFixture(VALID_PROFILE)
        self.addCleanup(fixture.cleanup)
        result = run_tool("validate", fixture.path)
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)


class InvalidProfileTest(unittest.TestCase):
    def validate_text(self, text, directory="virtual-phone"):
        fixture = ProfileFixture(text, directory)
        self.addCleanup(fixture.cleanup)
        return run_tool("validate", fixture.path)

    def test_missing_kernel_configuration(self):
        text = VALID_PROFILE[: VALID_PROFILE.index("kernel:")] + VALID_PROFILE[
            VALID_PROFILE.index("resolution:") :
        ]
        result = self.validate_text(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("kernel", result.stderr)

    def test_missing_required_field(self):
        text = VALID_PROFILE.replace("architecture: aarch64\n", "")
        result = self.validate_text(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("architecture", result.stderr)

    def test_missing_schema_version(self):
        text = VALID_PROFILE.replace("schema_version: 1\n", "")
        result = self.validate_text(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("schema_version", result.stderr)

    def test_invalid_capability_state(self):
        text = VALID_PROFILE.replace(
            "display: not_implemented", "display: virtual_or_software"
        )
        result = self.validate_text(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("hardware.display", result.stderr)

    def test_id_directory_mismatch(self):
        text = VALID_PROFILE.replace("id: virtual-phone", "id: virtual-phone-01")
        result = self.validate_text(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("does not match the profile directory name", result.stderr)

    def test_unknown_field_is_rejected(self):
        result = self.validate_text(VALID_PROFILE + "\nplatform:\n  type: qemu\n")
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("unknown field", result.stderr)

    def test_qemu_runner_requires_a_qemu_block(self):
        text = VALID_PROFILE[: VALID_PROFILE.index("  qemu:")] + "boot:\n  method: qemu\n"
        result = self.validate_text(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("qemu", result.stderr)

    def test_default_orientation_must_be_supported(self):
        text = VALID_PROFILE.replace("  default: portrait", "  default: landscape")
        text = text.replace("    - portrait\n    - landscape\n", "    - portrait\n")
        result = self.validate_text(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("orientation.default", result.stderr)


class UnknownDeviceTest(unittest.TestCase):
    def test_resolve_reports_available_devices(self):
        result = run_tool("resolve", "pixel-9000")
        self.assertEqual(result.returncode, EXIT_UNKNOWN_DEVICE)
        self.assertIn("unknown device: pixel-9000", result.stderr)
        self.assertIn("virtual-phone", result.stderr)

    def test_build_script_rejects_unknown_device(self):
        result = subprocess.run(
            [os.path.join(ROOT_DIR, "scripts", "build.sh"), "pixel-9000"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        self.assertEqual(result.returncode, EXIT_UNKNOWN_DEVICE, result.stderr)
        self.assertIn("unknown device", result.stderr)

    def test_run_script_rejects_unknown_device(self):
        result = subprocess.run(
            [os.path.join(ROOT_DIR, "scripts", "run.sh"), "pixel-9000"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        self.assertEqual(result.returncode, EXIT_UNKNOWN_DEVICE, result.stderr)
        self.assertIn("unknown device", result.stderr)


class BuiltinYamlReaderTest(unittest.TestCase):
    """The scripts must work without PyYAML installed."""

    def load_without_pyyaml(self, text):
        saved = sys.modules.get("yaml", "absent")
        sys.modules["yaml"] = None  # makes `import yaml` raise ImportError
        try:
            return profile_lib.load_yaml(text)
        finally:
            if saved == "absent":
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = saved

    def test_matches_pyyaml_on_the_shipped_profile(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        with open(REAL_PROFILE, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(self.load_without_pyyaml(text), yaml.safe_load(text))

    def test_parses_scalars_lists_and_nesting(self):
        parsed = self.load_without_pyyaml(VALID_PROFILE)
        self.assertEqual(parsed["schema_version"], 1)
        self.assertIs(parsed["input"]["touchscreen"], True)
        self.assertEqual(parsed["orientation"]["supported"], ["portrait", "landscape"])
        self.assertEqual(parsed["runner"]["qemu"]["memory"], 1024)


if __name__ == "__main__":
    unittest.main()
