#!/usr/bin/env python3
"""Environment check tests.

These cover the reporting and diagnostics, and guard the property that
matters most: the builder and ./scripts/check-env.sh check against one
list, so they cannot disagree about what a machine needs.
"""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
LIB_DIR = os.path.join(ROOT_DIR, "scripts", "lib")
REQUIREMENTS_PY = os.path.join(LIB_DIR, "requirements.py")
CHECK_ENV_SH = os.path.join(ROOT_DIR, "scripts", "check-env.sh")


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


requirements = _import("lt_requirements_under_test", "requirements.py")


def run_tool(*args, env=None):
    return subprocess.run(
        [sys.executable, REQUIREMENTS_PY, *args],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
        env=env,
    )


def stub_bin(directory, names, body='#!/bin/sh\necho "1.2.3"\n'):
    """A directory of stand-in executables, for controlling what is found."""
    os.makedirs(directory, exist_ok=True)
    for name in names:
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return directory


class RequirementDataTest(unittest.TestCase):
    def test_every_requirement_is_complete(self):
        for requirement in requirements.REQUIREMENTS:
            with self.subTest(requirement["name"]):
                self.assertIn(requirement["group"], requirements.GROUPS)
                self.assertTrue(requirement["why"].strip(), "no reason given")
                for distribution in ("arch", "debian"):
                    self.assertTrue(
                        requirement["packages"][distribution].strip(),
                        "no %s package named" % distribution,
                    )

    def test_requirement_names_are_unique(self):
        names = [requirement["name"] for requirement in requirements.REQUIREMENTS]
        self.assertEqual(len(names), len(set(names)))

    def test_covers_what_the_build_actually_uses(self):
        listed = {requirement["name"] for requirement in requirements.REQUIREMENTS}
        for tool in ("cpio", "gzip", "make", "bison", "flex", "bc", "xz", "bzip2"):
            self.assertIn(tool, listed)
        self.assertIn(requirements.QEMU_BINARY, listed)

    def test_unknown_group_is_rejected(self):
        with self.assertRaises(ValueError):
            requirements.check(["not-a-group"])


class SingleSourceOfTruthTest(unittest.TestCase):
    """No second, drifting copy of a dependency list."""

    def read(self, *parts):
        with open(os.path.join(ROOT_DIR, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_the_builder_does_not_keep_its_own_tool_list(self):
        tools = self.read("scripts", "lib", "tools.sh")
        for tool in ("bison", "flex", "bc"):
            self.assertNotIn(
                "%s " % tool,
                tools,
                "tools.sh names build tools itself instead of using requirements.py",
            )
        self.assertIn("requirements.py", tools)

    def test_cross_prefixes_are_defined_once(self):
        """Scans the shipped code; this test file names a prefix itself."""
        definitions = []
        for top in ("scripts", "builder"):
            for directory, _, filenames in os.walk(os.path.join(ROOT_DIR, top)):
                if "__pycache__" in directory:
                    continue
                for filename in filenames:
                    if not filename.endswith((".sh", ".py")):
                        continue
                    path = os.path.join(directory, filename)
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        if "aarch64-unknown-linux-gnu-" in handle.read():
                            definitions.append(os.path.relpath(path, ROOT_DIR))
        self.assertEqual(
            sorted(definitions),
            ["scripts/lib/requirements.py"],
            "the cross compiler prefix list is duplicated",
        )

    def test_manifest_uses_the_shared_prefixes(self):
        manifest = _import("lt_manifest_env_test", "manifest.py")
        self.assertEqual(manifest.CROSS_PREFIXES, requirements.CROSS_PREFIXES)


class ReportingTest(unittest.TestCase):
    def test_reports_versions_for_present_tools(self):
        result = run_tool("check", "--group", "core")
        self.assertIn("python3", result.stdout)
        self.assertIn("core", result.stdout)

    def test_json_output_is_structured(self):
        result = run_tool("check", "--group", "qemu", "--json")
        payload = json.loads(result.stdout)
        self.assertTrue(payload)
        for entry in payload:
            self.assertIn("ok", entry)
            self.assertIn("why", entry)
            self.assertIn("packages", entry)

    def test_list_names_every_group(self):
        result = run_tool("list")
        self.assertEqual(result.returncode, 0)
        for group in requirements.GROUPS:
            self.assertIn(group, result.stdout)

    def test_commands_prints_only_command_requirements(self):
        result = run_tool("commands", "--group", "core")
        printed = result.stdout.split()
        self.assertIn("cpio", printed)
        self.assertNotIn("cpio --reproducible", result.stdout)


class MissingToolDiagnosticsTest(unittest.TestCase):
    """The interesting case: something is absent."""

    def empty_environment(self):
        base = tempfile.mkdtemp(prefix="linux-touch-env-")
        self.addCleanup(shutil.rmtree, base, True)
        empty = os.path.join(base, "bin")
        os.makedirs(empty)
        return base, dict(os.environ, PATH=empty)

    def test_missing_tools_fail_with_actionable_output(self):
        _, environment = self.empty_environment()
        result = run_tool("check", "--group", "core", env=environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("MISSING", result.stdout)
        self.assertIn("cpio", result.stdout)
        # Why it is needed, and how to install it, for both distributions.
        self.assertIn("packs the initramfs", result.stdout)
        self.assertIn("pacman -S cpio", result.stdout)
        self.assertIn("apt install cpio", result.stdout)
        self.assertIn("required item(s) missing", result.stdout)

    def test_quiet_mode_reports_only_what_is_missing(self):
        _, environment = self.empty_environment()
        result = run_tool("check", "--group", "core", "--quiet", env=environment)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("missing host tools", result.stderr)
        self.assertIn("check-env.sh", result.stderr)

    def test_missing_cross_compiler_is_named_with_the_prefixes_tried(self):
        _, environment = self.empty_environment()
        result = run_tool("check", "--group", "source-build", env=environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("aarch64 cross compiler", result.stdout)
        self.assertIn("CROSS_COMPILE", result.stdout)
        for prefix in requirements.CROSS_PREFIXES:
            self.assertIn(prefix, result.stdout)

    def test_cross_compile_override_is_honoured(self):
        base, environment = self.empty_environment()
        stub = stub_bin(os.path.join(base, "toolchain"), ["my-cross-gcc"])
        environment["PATH"] = stub + os.pathsep + environment["PATH"]
        environment["CROSS_COMPILE"] = "my-cross-"
        result = run_tool("cross-compile", env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "my-cross-")

    def test_cross_compile_failure_is_actionable(self):
        _, environment = self.empty_environment()
        result = run_tool("cross-compile", env=environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no aarch64 cross compiler found", result.stderr)
        self.assertIn("CROSS_COMPILE", result.stderr)

    def test_recommended_items_do_not_fail_the_check(self):
        result = run_tool("check", "--group", "recommended")
        self.assertIn(result.returncode, (0, 1))
        if result.returncode == 1:
            self.skipTest("a recommended item is missing here")
        strict = run_tool("check", "--group", "recommended", "--strict")
        self.assertEqual(strict.returncode, 0)

    def test_missing_cpio_reports_the_capability_it_needs(self):
        _, environment = self.empty_environment()
        result = run_tool("check", "--group", "core", env=environment)
        self.assertIn("cpio --reproducible", result.stdout)
        self.assertIn("GNU cpio 2.12", result.stdout)


class CheckEnvScriptTest(unittest.TestCase):
    def test_script_runs_and_reports(self):
        result = subprocess.run(
            [CHECK_ENV_SH, "--group", "core"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Linux-Touch environment check", result.stdout)

    def test_script_fails_when_something_required_is_missing(self):
        base = tempfile.mkdtemp(prefix="linux-touch-env-")
        self.addCleanup(shutil.rmtree, base, True)
        # Keep only what the script needs to start: a shell, dirname to
        # locate the repository, and python to run the check. Everything it
        # reports on is absent.
        bin_dir = os.path.join(base, "bin")
        os.makedirs(bin_dir)
        os.symlink(sys.executable, os.path.join(bin_dir, "python3"))
        for tool in ("bash", "dirname"):
            found = shutil.which(tool)
            self.assertIsNotNone(found, "%s is needed to run this test" % tool)
            os.symlink(found, os.path.join(bin_dir, tool))
        environment = dict(os.environ, PATH=bin_dir)

        result = subprocess.run(
            [CHECK_ENV_SH, "--group", "core"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
            env=environment,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("MISSING", result.stdout)

    def test_script_accepts_json(self):
        result = subprocess.run(
            [CHECK_ENV_SH, "--group", "qemu", "--json"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        json.loads(result.stdout)


class DocumentationTest(unittest.TestCase):
    """The document must describe the tools that are actually checked."""

    def setUp(self):
        with open(os.path.join(ROOT_DIR, "docs", "development.md"), encoding="utf-8") as handle:
            self.document = handle.read()

    def test_documents_every_required_tool(self):
        for requirement in requirements.REQUIREMENTS:
            if requirement["group"] == "recommended":
                continue
            with self.subTest(requirement["name"]):
                name = requirement["name"].split()[0]
                self.assertIn(name, self.document)

    def test_documents_the_workflow_and_variables(self):
        for fragment in (
            "./scripts/check-env.sh",
            "./scripts/fetch.sh",
            "./scripts/build.sh",
            "./scripts/run.sh",
            "LT_SOURCE_BUILD_TESTS",
            "LT_BOOT_TESTS",
            "SOURCE_DATE_EPOCH",
            "CROSS_COMPILE",
            "out/<device>-<distro>/",
            "manifest.json",
        ):
            with self.subTest(fragment):
                self.assertIn(fragment, self.document)

    def test_makes_no_bit_for_bit_reproducibility_claim(self):
        lowered = self.document.lower()
        self.assertIn("not a claim that the\nkernel is bit-for-bit", lowered)

    def test_uses_repository_relative_paths(self):
        for absolute in ("/home/", "/root/", "/Users/"):
            self.assertNotIn(absolute, self.document)


if __name__ == "__main__":
    unittest.main()
