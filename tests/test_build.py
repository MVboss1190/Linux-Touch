#!/usr/bin/env python3
"""Reproducible build tests: initramfs determinism, manifest generation,
input validation and guarded cleanup.

Tests that need GNU cpio are skipped where it is not installed; everything
else runs anywhere. No test reaches the network.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
LIB_DIR = os.path.join(ROOT_DIR, "scripts", "lib")
MKINITRAMFS = os.path.join(LIB_DIR, "mkinitramfs.sh")
TOOLS_SH = os.path.join(LIB_DIR, "tools.sh")
BUILD_SH = os.path.join(ROOT_DIR, "scripts", "build.sh")
DEVICE_PROFILE = os.path.join(ROOT_DIR, "devices", "virtual-phone", "device.yaml")
DISTRO_PROFILE = os.path.join(ROOT_DIR, "distros", "busybox-minimal", "distro.yaml")
EPOCH = "1704067200"
BUILD_ID = "busybox-minimal"

HAVE_CPIO = shutil.which("cpio") is not None
REAL_KERNEL = os.path.join(ROOT_DIR, "kernel", "linux", "arch", "arm64", "boot", "Image")


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest_lib = _import("lt_manifest_under_test", "manifest.py")


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def make_rootfs(directory, mode=0o700):
    """A staged tree resembling the prototype rootfs."""
    for name in ("bin", "etc", "proc", "sys", "dev", "tmp"):
        os.makedirs(os.path.join(directory, name), mode=mode, exist_ok=True)
    busybox = os.path.join(directory, "bin", "busybox")
    with open(busybox, "wb") as handle:
        handle.write(b"#!/bin/sh\necho busybox\n")
    os.chmod(busybox, 0o755)
    for applet in ("sh", "mount", "echo"):
        link = os.path.join(directory, "bin", applet)
        if not os.path.lexists(link):
            os.symlink("busybox", link)
    init = os.path.join(directory, "init")
    with open(init, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\nexec /bin/sh\n")
    os.chmod(init, 0o755)
    return directory


def temp_repo():
    """A copy of the scripts and data a build needs, without build output."""
    destination = tempfile.mkdtemp(prefix="linux-touch-build-")
    repo = os.path.join(destination, "repo")
    os.makedirs(repo)
    for entry in ("builder", "scripts", "devices", "distros", "schema", "sources.yaml"):
        source = os.path.join(ROOT_DIR, entry)
        target = os.path.join(repo, entry)
        if os.path.isdir(source):
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target)
    return repo


class NormalizationTest(unittest.TestCase):
    """Runs without cpio: checks what the archive is built from."""

    def list_members(self, rootfs):
        result = subprocess.run(
            [MKINITRAMFS, "--list", rootfs, EPOCH],
            capture_output=True,
            text=True,
            check=True,
        )
        return [line.split(" ", 2) for line in result.stdout.splitlines()]

    def test_members_are_sorted_and_normalized(self):
        rootfs = make_rootfs(tempfile.mkdtemp(prefix="linux-touch-rootfs-"))
        self.addCleanup(shutil.rmtree, rootfs, True)
        members = self.list_members(rootfs)

        paths = [member[2] for member in members]
        self.assertEqual(paths, sorted(paths), "members are not C-sorted")
        self.assertIn("./init", paths)
        for mode, mtime, path in members:
            self.assertEqual(mtime, EPOCH, "%s kept a non-deterministic mtime" % path)
            self.assertIn(mode, ("755", "644", "777"), "%s has mode %s" % (path, mode))

    def test_umask_does_not_leak_into_the_listing(self):
        """Two trees staged under different umasks normalize identically."""
        first = make_rootfs(tempfile.mkdtemp(prefix="linux-touch-rootfs-"), mode=0o700)
        second = make_rootfs(tempfile.mkdtemp(prefix="linux-touch-rootfs-"), mode=0o777)
        self.addCleanup(shutil.rmtree, first, True)
        self.addCleanup(shutil.rmtree, second, True)
        self.assertEqual(self.list_members(first), self.list_members(second))


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed in this environment")
class DeterministicInitramfsTest(unittest.TestCase):
    def build(self, rootfs, output):
        subprocess.run([MKINITRAMFS, rootfs, output, EPOCH], check=True)
        return sha256(output)

    def test_two_builds_are_byte_identical(self):
        workdir = tempfile.mkdtemp(prefix="linux-touch-repro-")
        self.addCleanup(shutil.rmtree, workdir, True)
        first_rootfs = make_rootfs(os.path.join(workdir, "rootfs-a"), mode=0o700)
        second_rootfs = make_rootfs(os.path.join(workdir, "rootfs-b"), mode=0o777)

        first = self.build(first_rootfs, os.path.join(workdir, "a.cpio.gz"))
        second = self.build(second_rootfs, os.path.join(workdir, "b.cpio.gz"))
        self.assertEqual(first, second, "initramfs output is not reproducible")

    def test_rebuilding_the_same_tree_is_stable(self):
        workdir = tempfile.mkdtemp(prefix="linux-touch-repro-")
        self.addCleanup(shutil.rmtree, workdir, True)
        rootfs = make_rootfs(os.path.join(workdir, "rootfs"))
        first = self.build(rootfs, os.path.join(workdir, "a.cpio.gz"))
        second = self.build(rootfs, os.path.join(workdir, "b.cpio.gz"))
        self.assertEqual(first, second)


class MkinitramfsArgumentTest(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run([MKINITRAMFS, *args], capture_output=True, text=True)

    def test_missing_rootfs_is_reported(self):
        result = self.run_tool("/nonexistent/rootfs", "/tmp/out.cpio.gz", EPOCH)
        self.assertEqual(result.returncode, 1)
        self.assertIn("root filesystem directory not found", result.stderr)

    def test_invalid_epoch_is_rejected(self):
        rootfs = tempfile.mkdtemp(prefix="linux-touch-rootfs-")
        self.addCleanup(shutil.rmtree, rootfs, True)
        result = self.run_tool(rootfs, os.path.join(rootfs, "out.cpio.gz"), "yesterday")
        self.assertEqual(result.returncode, 1)
        self.assertIn("SOURCE_DATE_EPOCH", result.stderr)

    def test_wrong_argument_count(self):
        self.assertEqual(self.run_tool("only-one").returncode, 2)


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.workdir = os.path.join(ROOT_DIR, "out", "test-manifest")
        os.makedirs(self.workdir, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.kernel = os.path.join(self.workdir, "Image")
        self.busybox = os.path.join(self.workdir, "busybox")
        self.initramfs = os.path.join(self.workdir, "initramfs.cpio.gz")
        for path, content in (
            (self.kernel, b"kernel-bytes"),
            (self.busybox, b"busybox-bytes"),
            (self.initramfs, b"initramfs-bytes"),
        ):
            with open(path, "wb") as handle:
                handle.write(content)

    def generate(self):
        return manifest_lib.build_manifest(
            root=ROOT_DIR,
            device_profile_path=DEVICE_PROFILE,
            distro_profile_path=DISTRO_PROFILE,
            build_id=BUILD_ID,
            source_date_epoch=int(EPOCH),
            inputs={"kernel_image": self.kernel, "busybox_binary": self.busybox},
            artifacts={"initramfs": self.initramfs},
            sources_manifest_path=os.path.join(ROOT_DIR, "sources.yaml"),
        )

    def test_records_device_build_sources_and_artifacts(self):
        manifest = self.generate()
        self.assertEqual(manifest["device"]["id"], "virtual-phone")
        self.assertEqual(manifest["device"]["architecture"], "aarch64")
        self.assertEqual(
            manifest["device"]["profile"], "devices/virtual-phone/device.yaml"
        )
        self.assertEqual(manifest["device"]["profile_sha256"], sha256(DEVICE_PROFILE))
        self.assertEqual(manifest["distro"]["id"], "busybox-minimal")
        self.assertEqual(
            manifest["distro"]["profile"], "distros/busybox-minimal/distro.yaml"
        )
        self.assertEqual(
            manifest["distro"]["profile_sha256"], sha256(DISTRO_PROFILE)
        )
        self.assertEqual(manifest["distro"]["init_system"], "busybox")
        self.assertEqual(manifest["build"]["id"], BUILD_ID)
        self.assertEqual(manifest["build"]["source_date_epoch"], int(EPOCH))
        self.assertIn("linux", manifest["pinned_sources"])
        self.assertIn("busybox", manifest["pinned_sources"])
        self.assertEqual(manifest["pinned_sources"]["linux"]["version"], "6.12.109")
        self.assertEqual(
            manifest["artifacts"]["initramfs"]["sha256"], sha256(self.initramfs)
        )
        self.assertIn("cross_compile", manifest["toolchain"])

    def test_prebuilt_inputs_are_marked_unverified(self):
        manifest = self.generate()
        for name in ("kernel_image", "busybox_binary"):
            self.assertEqual(manifest["inputs"][name]["provenance"], "prebuilt-unverified")

    def test_is_deterministic_and_carries_no_timestamp(self):
        first = json.dumps(self.generate(), sort_keys=True)
        second = json.dumps(self.generate(), sort_keys=True)
        self.assertEqual(first, second)
        self.assertNotIn("timestamp", first)
        self.assertNotIn("built_at", first)

    def test_contains_no_host_specific_paths(self):
        text = json.dumps(self.generate())
        self.assertNotIn(ROOT_DIR, text)
        self.assertNotIn(os.path.expanduser("~"), text)
        for path in (
            manifest["path"]
            for manifest in list(self.generate()["inputs"].values())
            + list(self.generate()["artifacts"].values())
        ):
            self.assertFalse(os.path.isabs(path), path)

    def test_missing_input_file_is_an_error(self):
        os.remove(self.kernel)
        with self.assertRaises(manifest_lib.ManifestError):
            self.generate()


class BuildInputTest(unittest.TestCase):
    """build.sh must fail clearly, and before any destructive work."""

    def build_in(self, repo, *args):
        return subprocess.run(
            [os.path.join(repo, "scripts", "build.sh"), *args],
            capture_output=True,
            text=True,
            cwd=repo,
        )

    def test_missing_kernel_is_reported(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        result = self.build_in(repo, "virtual-phone")
        self.assertEqual(result.returncode, 1)
        # Neither a source build nor a prebuilt image is possible here, and
        # the error must name both remedies rather than just one.
        self.assertIn("cannot produce kernel", result.stderr)
        self.assertIn("No prebuilt artifact", result.stderr)
        self.assertIn("fetch.sh", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(repo, "os", "rootfs")))
        self.assertFalse(os.path.exists(os.path.join(repo, "out")))

    def test_missing_busybox_is_reported(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        kernel = os.path.join(repo, "kernel", "linux", "arch", "arm64", "boot")
        os.makedirs(kernel)
        with open(os.path.join(kernel, "Image"), "wb") as handle:
            handle.write(b"stand-in kernel")
        result = self.build_in(repo, "virtual-phone")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot produce busybox", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(repo, "out")))

    def test_invalid_source_date_epoch_is_rejected(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        environment = dict(os.environ, SOURCE_DATE_EPOCH="not-a-number")
        result = subprocess.run(
            [os.path.join(repo, "scripts", "build.sh"), "virtual-phone"],
            capture_output=True,
            text=True,
            cwd=repo,
            env=environment,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("SOURCE_DATE_EPOCH", result.stderr)


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed in this environment")
class FullBuildTest(unittest.TestCase):
    def prepare(self):
        repo = temp_repo()
        self.addCleanup(shutil.rmtree, os.path.dirname(repo), True)
        boot = os.path.join(repo, "kernel", "linux", "arch", "arm64", "boot")
        os.makedirs(boot)
        with open(os.path.join(boot, "Image"), "wb") as handle:
            handle.write(b"stand-in kernel image")
        os.makedirs(os.path.join(repo, "busybox"))
        busybox = os.path.join(repo, "busybox", "busybox")
        with open(busybox, "wb") as handle:
            handle.write(b"#!/bin/sh\necho busybox\n")
        os.chmod(busybox, 0o755)
        return repo

    def test_build_produces_artifacts_and_a_manifest(self):
        repo = self.prepare()
        result = subprocess.run(
            [os.path.join(repo, "scripts", "build.sh"), "virtual-phone"],
            capture_output=True,
            text=True,
            cwd=repo,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out_dir = os.path.join(repo, "out", "virtual-phone-" + BUILD_ID)
        initramfs = os.path.join(out_dir, "initramfs.cpio.gz")
        self.assertTrue(os.path.isfile(initramfs))
        self.assertTrue(
            os.path.isfile(os.path.join(repo, "os", "images", "initramfs.cpio.gz"))
        )
        with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["device"]["id"], "virtual-phone")
        self.assertEqual(
            manifest["artifacts"]["initramfs"]["sha256"], sha256(initramfs)
        )

    def test_rebuild_is_byte_identical(self):
        repo = self.prepare()
        initramfs = os.path.join(
            repo, "out", "virtual-phone-" + BUILD_ID, "initramfs.cpio.gz"
        )
        digests = []
        for umask in (0o022, 0o077):
            subprocess.run(
                "umask %04o && ./scripts/build.sh virtual-phone" % umask,
                shell=True,
                cwd=repo,
                check=True,
                capture_output=True,
            )
            digests.append(sha256(initramfs))
        self.assertEqual(digests[0], digests[1], "rebuild was not reproducible")


class SafeCleanupTest(unittest.TestCase):
    def safe_rm(self, target, root=ROOT_DIR):
        script = 'source "%s"; lt_safe_rm_rf "%s"' % (TOOLS_SH, target)
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, cwd=root
        )

    def test_refuses_paths_outside_the_build_directories(self):
        outside = tempfile.mkdtemp(prefix="linux-touch-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        for target in ("/", os.path.expanduser("~"), ROOT_DIR, outside):
            result = self.safe_rm(target)
            self.assertEqual(result.returncode, 1, "allowed removal of %s" % target)
            self.assertIn("refusing", result.stderr)
        self.assertTrue(os.path.isdir(outside), "a refused path was still removed")

    def test_refuses_an_empty_path(self):
        result = self.safe_rm("")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to remove an empty path", result.stderr)

    def test_refuses_traversal_out_of_the_build_directories(self):
        outside = tempfile.mkdtemp(prefix="linux-touch-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        os.makedirs(os.path.join(ROOT_DIR, "out"), exist_ok=True)
        target = os.path.join(ROOT_DIR, "out", "..", os.path.basename(outside))
        # Point the traversal at a real directory outside the repository.
        link = os.path.join(ROOT_DIR, "out", "escape")
        os.symlink(outside, link)
        self.addCleanup(lambda: os.path.islink(link) and os.remove(link))
        result = self.safe_rm(os.path.join(link, "."))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(os.path.isdir(outside))
        del target

    def test_removes_a_path_inside_the_build_directories(self):
        target = os.path.join(ROOT_DIR, "out", "safe-cleanup-probe")
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "file"), "w", encoding="utf-8") as handle:
            handle.write("x")
        result = self.safe_rm(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
