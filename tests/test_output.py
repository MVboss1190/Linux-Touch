#!/usr/bin/env python3
"""Build output tests: canonical layout, stage caching, manifest
authority, and run.sh refusing to boot anything it cannot verify.

A stand-in qemu-system-aarch64 on PATH reports its argv instead of booting,
so the runner's behaviour is testable without QEMU or a real kernel. No
test claims a guest booted.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
BUILDER_DIR = os.path.join(ROOT_DIR, "builder")

HAVE_CPIO = shutil.which("cpio") is not None

DEVICE = "virtual-phone"
DISTRO = "busybox-minimal"
OUT_NAME = "%s-%s" % (DEVICE, DISTRO)
REQUIRED_ARTIFACTS = ("Image", "initramfs.cpio.gz", "manifest.json")

STAND_IN_KERNEL = b"stand-in kernel image"
STAND_IN_BUSYBOX = b"#!/bin/sh\necho busybox\n"

EXPECTED_QEMU_ARGV = (
    "-machine virt -cpu cortex-a72 -m 1024 -nographic "
    "-kernel {kernel} -initrd {initramfs} -append console=ttyAMA0"
)


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class Workspace:
    """A repository copy with stand-in inputs and a stand-in QEMU."""

    def __init__(self):
        self.base = tempfile.mkdtemp(prefix="linux-touch-out-")
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(self.repo)
        for entry in ("builder", "scripts", "devices", "distros", "schema", "sources.yaml"):
            source = os.path.join(ROOT_DIR, entry)
            target = os.path.join(self.repo, entry)
            if os.path.isdir(source):
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                shutil.copy2(source, target)

        boot = os.path.join(self.repo, "kernel", "linux", "arch", "arm64", "boot")
        os.makedirs(boot)
        with open(os.path.join(boot, "Image"), "wb") as handle:
            handle.write(STAND_IN_KERNEL)
        os.makedirs(os.path.join(self.repo, "busybox"))
        busybox = os.path.join(self.repo, "busybox", "busybox")
        with open(busybox, "wb") as handle:
            handle.write(STAND_IN_BUSYBOX)
        os.chmod(busybox, 0o755)

        self.bin = os.path.join(self.base, "bin")
        os.makedirs(self.bin)
        qemu = os.path.join(self.bin, "qemu-system-aarch64")
        with open(qemu, "w", encoding="utf-8") as handle:
            handle.write('#!/bin/sh\necho "QEMU ARGV: $*"\n')
        os.chmod(qemu, 0o755)

    @property
    def out_dir(self):
        return os.path.join(self.repo, "out", OUT_NAME)

    @property
    def manifest_path(self):
        return os.path.join(self.out_dir, "manifest.json")

    def manifest(self):
        with open(self.manifest_path, encoding="utf-8") as handle:
            return json.load(handle)

    def write_manifest(self, manifest):
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")

    def environment(self):
        return dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"])

    def build(self, *args):
        return subprocess.run(
            [os.path.join(self.repo, "scripts", "build.sh"), DEVICE, *args],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )

    def run(self, *args):
        return subprocess.run(
            [os.path.join(self.repo, "scripts", "run.sh"), DEVICE, *args],
            capture_output=True,
            text=True,
            cwd=self.repo,
            env=self.environment(),
        )

    def stage(self, name, *args):
        return subprocess.run(
            [os.path.join(self.repo, "builder", "stage-%s.sh" % name), DEVICE, DISTRO, *args],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )

    def digests(self):
        return {
            name: sha256(os.path.join(self.out_dir, name))
            for name in REQUIRED_ARTIFACTS
        }

    def cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        self.addCleanup(self.workspace.cleanup)


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed in this environment")
class CanonicalOutputTest(WorkspaceTest):
    def test_output_directory_holds_the_required_artifacts(self):
        result = self.workspace.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in REQUIRED_ARTIFACTS:
            self.assertTrue(
                os.path.isfile(os.path.join(self.workspace.out_dir, name)),
                "missing %s in the canonical output" % name,
            )

    def test_manifest_hashes_match_the_artifacts(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        manifest = self.workspace.manifest()
        self.assertEqual(manifest["schema_version"], 3)
        for name, filename in (
            ("kernel_image", "Image"),
            ("initramfs", "initramfs.cpio.gz"),
        ):
            record = manifest["artifacts"][name]
            path = os.path.join(self.workspace.repo, record["path"])
            self.assertEqual(record["path"], "out/%s/%s" % (OUT_NAME, filename))
            self.assertEqual(record["sha256"], sha256(path))

    def test_kernel_is_recorded_as_a_copy_not_a_build_product(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        manifest = self.workspace.manifest()
        artifact = manifest["artifacts"]["kernel_image"]
        self.assertEqual(artifact["copied_from"], "kernel_image")
        self.assertEqual(artifact["provenance"], "prebuilt-unverified")
        self.assertEqual(
            artifact["sha256"], manifest["inputs"]["kernel_image"]["sha256"]
        )
        self.assertFalse(manifest["build"]["compiles_sources"])

    def test_manifest_records_both_profile_identities_and_hashes(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        manifest = self.workspace.manifest()
        self.assertEqual(manifest["device"]["id"], DEVICE)
        self.assertEqual(manifest["distro"]["id"], DISTRO)
        self.assertEqual(manifest["device"]["architecture"], "aarch64")
        self.assertEqual(manifest["build"]["source_date_epoch"], 1704067200)
        for section in ("device", "distro"):
            self.assertRegex(manifest[section]["profile_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("linux", manifest["pinned_sources"])
        self.assertIn("toolchain", manifest)

    def test_manifest_has_no_host_specific_paths(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        with open(self.workspace.manifest_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(self.workspace.repo, text)
        self.assertNotIn(os.path.expanduser("~"), text)


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed in this environment")
class RebuildTest(WorkspaceTest):
    def test_rerun_is_safe_and_reuses_cached_stages(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        first = self.workspace.digests()

        second = self.workspace.build()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Image: cached", second.stdout)
        self.assertIn("root filesystem: cached", second.stdout)
        self.assertIn("initramfs: cached", second.stdout)
        self.assertEqual(first, self.workspace.digests())

    def test_forced_rebuild_reproduces_identical_artifacts(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        first = self.workspace.digests()

        forced = self.workspace.build("--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertIn("Image: staged", forced.stdout)
        self.assertEqual(
            first,
            self.workspace.digests(),
            "a forced rebuild changed the output",
        )

    def test_changed_input_invalidates_the_cache(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        before = self.workspace.digests()

        overlay_init = os.path.join(
            self.workspace.repo, "distros", DISTRO, "overlay", "init"
        )
        with open(overlay_init, "a", encoding="utf-8") as handle:
            handle.write("\necho CACHE_BUSTER\n")

        rebuilt = self.workspace.build()
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertIn("root filesystem: staged", rebuilt.stdout)
        self.assertNotEqual(
            before["initramfs.cpio.gz"],
            self.workspace.digests()["initramfs.cpio.gz"],
        )


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed in this environment")
class StageTest(WorkspaceTest):
    def test_stages_run_independently_in_order(self):
        for name in ("inputs", "kernel", "rootfs", "initramfs", "manifest"):
            result = self.workspace.stage(name)
            self.assertEqual(result.returncode, 0, "%s: %s" % (name, result.stderr))
        for name in REQUIRED_ARTIFACTS:
            self.assertTrue(os.path.isfile(os.path.join(self.workspace.out_dir, name)))

    def test_initramfs_stage_refuses_to_run_without_a_rootfs(self):
        self.assertEqual(self.workspace.stage("kernel").returncode, 0)
        result = self.workspace.stage("initramfs")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no staged root filesystem", result.stderr)

    def test_manifest_stage_refuses_to_run_without_artifacts(self):
        result = self.workspace.stage("manifest")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing artifact", result.stderr)

    def test_stage_is_cached_on_a_second_run(self):
        self.assertEqual(self.workspace.stage("kernel").returncode, 0)
        again = self.workspace.stage("kernel")
        self.assertIn("cached", again.stdout)
        forced = self.workspace.stage("kernel", "--force")
        self.assertIn("staged", forced.stdout)

    def test_stage_rejects_missing_arguments(self):
        result = subprocess.run(
            [os.path.join(BUILDER_DIR, "stage-kernel.sh")],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_stages_do_not_touch_unrelated_directories(self):
        untouched = os.path.join(self.workspace.repo, "devices")
        before = sorted(os.listdir(untouched))
        self.assertEqual(self.workspace.build().returncode, 0)
        self.assertEqual(before, sorted(os.listdir(untouched)))
        # Only os/ and out/ may appear as generated trees.
        generated = {
            entry
            for entry in os.listdir(self.workspace.repo)
            if entry not in {
                "builder", "scripts", "devices", "distros", "schema",
                "sources.yaml", "kernel", "busybox",
            }
        }
        self.assertEqual(generated, {"os", "out"})


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed in this environment")
class RunnerTest(WorkspaceTest):
    def qemu_argv(self, result):
        for line in result.stdout.splitlines():
            if line.startswith("QEMU ARGV: "):
                return line[len("QEMU ARGV: ") :]
        return None

    def test_boots_the_artifacts_from_the_canonical_output(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        result = self.workspace.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.qemu_argv(result),
            EXPECTED_QEMU_ARGV.format(
                kernel=os.path.join(self.workspace.out_dir, "Image"),
                initramfs=os.path.join(self.workspace.out_dir, "initramfs.cpio.gz"),
            ),
        )

    def test_explicit_distro_selects_the_same_output(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        result = self.workspace.run("--distro", DISTRO)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("out/%s/Image" % OUT_NAME, self.qemu_argv(result))

    def test_rejects_a_missing_artifact(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        os.remove(os.path.join(self.workspace.out_dir, "Image"))
        result = self.workspace.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing artifact 'kernel_image'", result.stderr)
        self.assertIsNone(self.qemu_argv(result), "QEMU was started anyway")

    def test_rejects_a_corrupted_artifact(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        with open(
            os.path.join(self.workspace.out_dir, "initramfs.cpio.gz"), "ab"
        ) as handle:
            handle.write(b"tampered")
        result = self.workspace.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match the manifest", result.stderr)
        self.assertIsNone(self.qemu_argv(result), "QEMU was started anyway")

    def test_rejects_output_built_for_another_device(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        manifest = self.workspace.manifest()
        manifest["device"]["id"] = "other-phone"
        self.workspace.write_manifest(manifest)
        result = self.workspace.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("produced for device 'other-phone'", result.stderr)
        self.assertIsNone(self.qemu_argv(result))

    def test_rejects_output_built_for_another_distro(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        manifest = self.workspace.manifest()
        manifest["distro"]["id"] = "some-other-distro"
        self.workspace.write_manifest(manifest)
        result = self.workspace.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("produced for distro 'some-other-distro'", result.stderr)

    def test_rejects_an_unsupported_manifest_version(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        manifest = self.workspace.manifest()
        manifest["schema_version"] = 99
        self.workspace.write_manifest(manifest)
        result = self.workspace.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("schema_version", result.stderr)

    def test_reports_when_nothing_has_been_built(self):
        result = self.workspace.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no build output", result.stderr)
        self.assertIn("build.sh", result.stderr)


@unittest.skipUnless(HAVE_CPIO, "GNU cpio is not installed in this environment")
class LegacyFallbackTest(WorkspaceTest):
    """os/images is retained as an explicitly legacy path, so it is tested."""

    def test_build_still_writes_the_legacy_copy(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        legacy = os.path.join(self.workspace.repo, "os", "images", "initramfs.cpio.gz")
        self.assertTrue(os.path.isfile(legacy))
        self.assertEqual(
            sha256(legacy),
            sha256(os.path.join(self.workspace.out_dir, "initramfs.cpio.gz")),
        )

    def test_legacy_copy_can_be_switched_off(self):
        self.assertEqual(self.workspace.build("--no-legacy-copy").returncode, 0)
        self.assertFalse(
            os.path.exists(
                os.path.join(self.workspace.repo, "os", "images", "initramfs.cpio.gz")
            )
        )

    def test_runner_falls_back_to_the_legacy_layout_with_a_warning(self):
        self.assertEqual(self.workspace.build().returncode, 0)
        shutil.rmtree(os.path.join(self.workspace.repo, "out"))

        result = self.workspace.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("falling back to the legacy os/images layout", result.stderr)
        self.assertIn("unverified", result.stdout)
        self.assertIn("os/images/initramfs.cpio.gz", result.stdout)

    def test_a_corrupt_output_never_falls_back_to_legacy(self):
        """A broken build must fail, not silently boot something older."""
        self.assertEqual(self.workspace.build().returncode, 0)
        with open(
            os.path.join(self.workspace.out_dir, "initramfs.cpio.gz"), "ab"
        ) as handle:
            handle.write(b"tampered")
        result = self.workspace.run()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("falling back", result.stderr)


class BuildOptionTest(WorkspaceTest):
    def test_unknown_option_is_rejected(self):
        result = self.workspace.build("--wat")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option: --wat", result.stderr)

    def test_runner_rejects_build_only_options(self):
        result = self.workspace.run("--force")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option: --force", result.stderr)


if __name__ == "__main__":
    unittest.main()
