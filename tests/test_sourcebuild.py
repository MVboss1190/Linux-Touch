#!/usr/bin/env python3
"""Source-build tests: provenance, mode selection and toolchain handling.

Two tiers:

* Fast tests always run. They cover mode selection, the refusal to fall
  back silently, prebuilt provenance and manifest consistency, using
  stand-in inputs and a cross compiler that does not exist.
* Real source-build tests compile the pinned Linux and BusyBox. They are
  expensive, so they run only with LT_SOURCE_BUILD_TESTS=1 and only when
  the sources are fetched and a cross toolchain is installed. They reuse
  the repository's build/ directory, so a second run is cheap.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
BUILD_DIR = os.path.join(ROOT_DIR, "build")
CACHE_DIR = os.path.join(ROOT_DIR, ".cache", "sources")

DEVICE = "virtual-phone"
DISTRO = "busybox-minimal"
OUT_NAME = "%s-%s" % (DEVICE, DISTRO)

HAVE_CPIO = shutil.which("cpio") is not None
HAVE_CROSS = shutil.which("aarch64-linux-gnu-gcc") is not None
HAVE_FILE = shutil.which("file") is not None
SOURCES_FETCHED = all(
    os.path.isfile(os.path.join(CACHE_DIR, name))
    for name in ("linux-6.12.109.tar.xz", "busybox-1.37.0.tar.bz2")
)
SOURCE_BUILD_TESTS = os.environ.get("LT_SOURCE_BUILD_TESTS") == "1"

requires_source_build = unittest.skipUnless(
    SOURCE_BUILD_TESTS and HAVE_CROSS and SOURCES_FETCHED and HAVE_CPIO,
    "set LT_SOURCE_BUILD_TESTS=1 with the sources fetched and an aarch64 "
    "cross toolchain installed to run the real source build",
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Workspace:
    """A repository copy. Sources and build cache are shared, not copied."""

    def __init__(self, with_prebuilt=True, share_sources=False):
        self.base = tempfile.mkdtemp(prefix="linux-touch-src-")
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(self.repo)
        for entry in (
            "builder", "scripts", "devices", "distros", "schema", "kernel",
            "sources.yaml",
        ):
            source = os.path.join(ROOT_DIR, entry)
            target = os.path.join(self.repo, entry)
            if os.path.isdir(source):
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__", "linux")
                )
            else:
                shutil.copy2(source, target)

        if with_prebuilt:
            boot = os.path.join(self.repo, "kernel", "linux", "arch", "arm64", "boot")
            os.makedirs(boot, exist_ok=True)
            with open(os.path.join(boot, "Image"), "wb") as handle:
                handle.write(b"stand-in kernel image")
            os.makedirs(os.path.join(self.repo, "busybox"), exist_ok=True)
            busybox = os.path.join(self.repo, "busybox", "busybox")
            with open(busybox, "wb") as handle:
                handle.write(b"#!/bin/sh\necho busybox\n")
            os.chmod(busybox, 0o755)

        self.share_sources = share_sources

    @property
    def out_dir(self):
        return os.path.join(self.repo, "out", OUT_NAME)

    def manifest(self):
        with open(os.path.join(self.out_dir, "manifest.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def component(self, name):
        with open(
            os.path.join(self.out_dir, ".build", "%s.json" % name), encoding="utf-8"
        ) as handle:
            return json.load(handle)

    def environment(self, **overrides):
        environment = dict(os.environ)
        if self.share_sources:
            # Reuse the repository's fetched tarballs and compiled trees so a
            # real build is done once, not once per test.
            environment["LT_SOURCES_MANIFEST"] = os.path.join(self.repo, "sources.yaml")
            environment["LT_SOURCE_CACHE_DIR"] = CACHE_DIR
            environment["LT_BUILD_DIR"] = BUILD_DIR
        environment.update({k: v for k, v in overrides.items() if v is not None})
        for key, value in overrides.items():
            if value is None:
                environment.pop(key, None)
        return environment

    def build(self, *args, **overrides):
        return subprocess.run(
            [os.path.join(self.repo, "scripts", "build.sh"), DEVICE, "--distro", DISTRO, *args],
            capture_output=True,
            text=True,
            cwd=self.repo,
            env=self.environment(**overrides),
        )

    def cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)


class BuildModeTest(unittest.TestCase):
    """Mode selection must be explicit and must never fall back silently."""

    def workspace(self, **kwargs):
        workspace = Workspace(**kwargs)
        self.addCleanup(workspace.cleanup)
        return workspace

    @unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed")
    def test_auto_falls_back_to_prebuilt_and_says_so(self):
        """No fetched sources in this workspace, so a prebuilt is used."""
        workspace = self.workspace()
        result = workspace.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("falling back to the prebuilt", result.stderr)
        self.assertIn("prebuilt-unverified", result.stderr)
        self.assertEqual(
            workspace.component("kernel")["provenance"], "prebuilt-unverified"
        )
        self.assertEqual(
            workspace.component("busybox")["provenance"], "prebuilt-unverified"
        )

    @unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed")
    def test_prebuilt_mode_records_prebuilt_provenance(self):
        workspace = self.workspace()
        result = workspace.build("--prebuilt")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = workspace.manifest()
        self.assertEqual(manifest["schema_version"], 4)
        for component in ("kernel", "busybox"):
            self.assertEqual(
                manifest["components"][component]["provenance"], "prebuilt-unverified"
            )
            self.assertIsNone(manifest["components"][component]["source"])
        self.assertEqual(
            manifest["inputs"]["kernel_image"]["provenance"], "prebuilt-unverified"
        )

    def test_prebuilt_mode_fails_when_no_prebuilt_exists(self):
        workspace = self.workspace(with_prebuilt=False)
        result = workspace.build("--prebuilt")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--prebuilt was requested but no prebuilt", result.stderr)

    def test_from_source_fails_without_fetched_sources(self):
        workspace = self.workspace()
        result = workspace.build("--from-source")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--from-source was requested", result.stderr)
        self.assertIn("./scripts/fetch.sh", result.stderr)

    def test_from_source_never_falls_back_to_a_prebuilt_artifact(self):
        """The whole point: an expected source build must not be faked."""
        workspace = self.workspace()
        result = workspace.build("--from-source")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("falling back", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(workspace.out_dir, "Image")))

    @unittest.skipUnless(SOURCES_FETCHED, "pinned sources are not fetched")
    def test_from_source_fails_without_a_cross_compiler(self):
        workspace = self.workspace(share_sources=True)
        result = workspace.build("--from-source", CROSS_COMPILE="definitely-not-a-prefix-")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cross compiler", result.stderr)
        self.assertNotIn("falling back", result.stderr)

    def test_nothing_available_at_all_is_an_error(self):
        workspace = self.workspace(with_prebuilt=False)
        result = workspace.build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot produce kernel", result.stderr)
        self.assertIn("No prebuilt artifact", result.stderr)


class ProfileBuildConfigurationTest(unittest.TestCase):
    """The build configuration lives in the profiles, on the right side."""

    def load(self, script, name, *args):
        result = subprocess.run(
            ["python3", os.path.join(ROOT_DIR, "scripts", "lib", script), "env", name, *args],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )

    def test_device_profile_owns_the_kernel_configuration(self):
        values = self.load("profile.py", DEVICE)
        self.assertEqual(values["LT_DEVICE_KERNEL_SOURCE"], "linux")
        self.assertEqual(values["LT_DEVICE_KERNEL_DEFCONFIG"], "defconfig")
        self.assertIn("arch/arm64/boot/Image", values["LT_DEVICE_KERNEL_IMAGE"])
        self.assertIn("virtual-phone.fragment", values["LT_DEVICE_KERNEL_FRAGMENTS"])

    def test_distro_profile_owns_the_busybox_configuration(self):
        values = self.load("distro.py", DISTRO, "--arch", "aarch64")
        self.assertEqual(values["LT_DISTRO_BUILD_SOURCE"], "busybox")
        self.assertEqual(values["LT_DISTRO_BUILD_STATIC"], "true")

    def test_kernel_configuration_is_not_in_the_distro_profile(self):
        with open(
            os.path.join(ROOT_DIR, "distros", DISTRO, "distro.yaml"), encoding="utf-8"
        ) as handle:
            text = handle.read()
        for forbidden in ("defconfig: defconfig\nimage:", "arch/arm64", "kernel:"):
            self.assertNotIn(forbidden, text)

    def test_kernel_fragment_declares_what_the_prototype_needs(self):
        with open(
            os.path.join(ROOT_DIR, "kernel", "config", "virtual-phone.fragment"),
            encoding="utf-8",
        ) as handle:
            fragment = handle.read()
        for option in (
            "CONFIG_BLK_DEV_INITRD=y",
            "CONFIG_DEVTMPFS=y",
            "CONFIG_SERIAL_AMBA_PL011=y",
        ):
            self.assertIn(option, fragment)

    def test_busybox_fragment_requires_a_static_link(self):
        with open(
            os.path.join(ROOT_DIR, "distros", DISTRO, "config", "busybox.fragment"),
            encoding="utf-8",
        ) as handle:
            self.assertIn("CONFIG_STATIC=y", handle.read())


@requires_source_build
class RealSourceBuildTest(unittest.TestCase):
    """Compiles the pinned Linux and BusyBox. Slow; opt-in."""

    @classmethod
    def setUpClass(cls):
        cls.workspace = Workspace(with_prebuilt=False, share_sources=True)
        cls.result = cls.workspace.build("--from-source")
        if cls.result.returncode != 0:
            cls.workspace.cleanup()
            raise AssertionError(
                "source build failed:\n%s" % cls.result.stderr[-4000:]
            )

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def test_build_succeeds_without_any_prebuilt_artifact(self):
        self.assertEqual(self.result.returncode, 0)
        for name in ("Image", "initramfs.cpio.gz", "manifest.json"):
            self.assertTrue(
                os.path.isfile(os.path.join(self.workspace.out_dir, name)), name
            )

    def test_kernel_provenance_is_recorded(self):
        kernel = self.workspace.manifest()["components"]["kernel"]
        self.assertEqual(kernel["provenance"], "built-from-source")
        self.assertEqual(kernel["source"]["name"], "linux")
        self.assertEqual(kernel["source"]["version"], "6.12.109")
        self.assertRegex(kernel["source"]["tarball_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(kernel["config_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(kernel["defconfig"], "defconfig")
        self.assertEqual(kernel["architecture"], "aarch64")
        self.assertIn("aarch64", kernel["toolchain"]["cross_compile"])

    def test_busybox_provenance_is_recorded(self):
        busybox = self.workspace.manifest()["components"]["busybox"]
        self.assertEqual(busybox["provenance"], "built-from-source")
        self.assertEqual(busybox["source"]["name"], "busybox")
        self.assertEqual(busybox["source"]["version"], "1.37.0")
        self.assertTrue(busybox["static"])
        self.assertRegex(busybox["config_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("aarch64", busybox["toolchain"]["cross_compile"])

    def test_manifest_hashes_match_the_artifacts(self):
        manifest = self.workspace.manifest()
        image = os.path.join(self.workspace.out_dir, "Image")
        self.assertEqual(manifest["artifacts"]["kernel_image"]["sha256"], sha256(image))
        self.assertEqual(
            manifest["components"]["kernel"]["binary_sha256"], sha256(image)
        )
        self.assertEqual(
            manifest["artifacts"]["initramfs"]["sha256"],
            sha256(os.path.join(self.workspace.out_dir, "initramfs.cpio.gz")),
        )
        self.assertEqual(
            manifest["inputs"]["kernel_image"]["provenance"], "built-from-source"
        )

    @unittest.skipUnless(HAVE_FILE, "file(1) is not installed")
    def test_artifacts_are_aarch64_and_busybox_is_static(self):
        image = os.path.join(self.workspace.out_dir, "Image")
        description = subprocess.run(
            ["file", "-b", image], capture_output=True, text=True, check=True
        ).stdout
        self.assertIn("ARM64", description.replace("aarch64", "ARM64"))

        busybox = os.path.join(BUILD_DIR, "busybox", DISTRO, "busybox")
        description = subprocess.run(
            ["file", "-b", busybox], capture_output=True, text=True, check=True
        ).stdout
        self.assertIn("ARM aarch64", description)
        self.assertIn("statically linked", description)
        self.assertNotIn("dynamically linked", description)

    def test_busybox_rebuild_is_reproducible(self):
        """BusyBox links deterministically from the same source and config."""
        busybox = os.path.join(BUILD_DIR, "busybox", DISTRO, "busybox")
        before = sha256(busybox)
        rebuilt = self.workspace.build("--from-source", "--force")
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertEqual(before, sha256(busybox), "BusyBox rebuild was not identical")

    def test_rebuild_reuses_the_cached_stages(self):
        again = self.workspace.build("--from-source")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("cached (source)", again.stdout)


if __name__ == "__main__":
    unittest.main()
