#!/usr/bin/env python3
"""Source manifest validation and checksum verification tests.

No test here reaches the network: downloads are exercised through file://
URLs, which take the same code path as https:// ones.
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
SOURCES_PY = os.path.join(LIB_DIR, "sources.py")
REAL_MANIFEST = os.path.join(ROOT_DIR, "sources.yaml")

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_UNKNOWN_SOURCE = 2


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sources_lib = _import("lt_sources_under_test", "sources.py")

MANIFEST_TEMPLATE = """\
schema_version: 1

defaults:
  source_date_epoch: 1704067200

sources:
  - name: payload
    description: test payload
    version: "1.0"
    url: {url}
    sha256: {sha256}
"""


def run_tool(*args):
    return subprocess.run(
        [sys.executable, SOURCES_PY, *args], capture_output=True, text=True, cwd=ROOT_DIR
    )


class ManifestFixture:
    def __init__(self, text):
        self.root = tempfile.mkdtemp(prefix="linux-touch-sources-")
        self.path = os.path.join(self.root, "sources.yaml")
        self.cache_dir = os.path.join(self.root, "cache")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def payload(self, content=b"pinned payload\n"):
        path = os.path.join(self.root, "payload-1.0.tar.gz")
        with open(path, "wb") as handle:
            handle.write(content)
        return "file://" + path, hashlib.sha256(content).hexdigest()

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class ShippedManifestTest(unittest.TestCase):
    def test_repository_manifest_is_valid(self):
        result = run_tool("--manifest", REAL_MANIFEST, "validate")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)

    def test_every_shipped_source_pins_a_checksum(self):
        manifest = sources_lib.load_manifest(REAL_MANIFEST)
        for source in manifest["sources"]:
            self.assertIsNotNone(
                source.get("sha256"), "%s has no pinned checksum" % source["name"]
            )
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("checksum_source", source, source["name"])

    def test_epoch_is_a_fixed_constant(self):
        manifest = sources_lib.load_manifest(REAL_MANIFEST)
        self.assertEqual(sources_lib.source_date_epoch(manifest), 1704067200)


class InvalidManifestTest(unittest.TestCase):
    def validate(self, text):
        fixture = ManifestFixture(text)
        self.addCleanup(fixture.cleanup)
        return run_tool("--manifest", fixture.path, "validate")

    def base(self):
        return MANIFEST_TEMPLATE.format(
            url="https://example.invalid/payload-1.0.tar.gz", sha256="a" * 64
        )

    def test_baseline_is_valid(self):
        self.assertEqual(self.validate(self.base()).returncode, EXIT_OK)

    def test_missing_required_field(self):
        result = self.validate(self.base().replace('    version: "1.0"\n', ""))
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("version", result.stderr)

    def test_missing_defaults_section(self):
        text = self.base().replace(
            "defaults:\n  source_date_epoch: 1704067200\n", ""
        )
        result = self.validate(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("defaults", result.stderr)

    def test_malformed_checksum_is_rejected(self):
        result = self.validate(self.base().replace("a" * 64, "not-a-sha256"))
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("sha256", result.stderr)

    def test_unknown_field_is_rejected(self):
        result = self.validate(self.base() + "    mirror: https://example.invalid/\n")
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("unknown field", result.stderr)

    def test_unsupported_url_scheme_is_rejected(self):
        result = self.validate(
            self.base().replace("https://example.invalid", "ftp://example.invalid")
        )
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("url", result.stderr)

    def test_duplicate_source_names_are_rejected(self):
        text = self.base() + """
  - name: payload
    version: "2.0"
    url: https://example.invalid/payload-2.0.tar.gz
    sha256: {sha}
""".format(sha="b" * 64)
        result = self.validate(text)
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("duplicate source name", result.stderr)

    def test_unknown_source_name(self):
        fixture = ManifestFixture(self.base())
        self.addCleanup(fixture.cleanup)
        result = run_tool("--manifest", fixture.path, "describe", "nonexistent")
        self.assertEqual(result.returncode, EXIT_UNKNOWN_SOURCE)
        self.assertIn("unknown source", result.stderr)


class ChecksumVerificationTest(unittest.TestCase):
    def fixture(self, content=b"pinned payload\n", sha256=None, url=None):
        placeholder = ManifestFixture(MANIFEST_TEMPLATE.format(url="x", sha256="y"))
        self.addCleanup(placeholder.cleanup)
        real_url, real_sha = placeholder.payload(content)
        text = MANIFEST_TEMPLATE.format(
            url=url or real_url, sha256=sha256 or real_sha
        )
        with open(placeholder.path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return placeholder, real_sha

    def test_matching_checksum_is_accepted(self):
        fixture, sha = self.fixture()
        result = run_tool(
            "--manifest", fixture.path, "--cache-dir", fixture.cache_dir, "fetch"
        )
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        cached = os.path.join(fixture.cache_dir, "payload-1.0.tar.gz")
        self.assertTrue(os.path.isfile(cached))
        self.assertEqual(sources_lib.sha256_file(cached), sha)

    def test_mismatched_checksum_fails_and_discards_the_download(self):
        fixture, _ = self.fixture(sha256="c" * 64)
        result = run_tool(
            "--manifest", fixture.path, "--cache-dir", fixture.cache_dir, "fetch"
        )
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse(
            os.path.exists(os.path.join(fixture.cache_dir, "payload-1.0.tar.gz"))
        )
        self.assertFalse(
            os.path.exists(os.path.join(fixture.cache_dir, "payload-1.0.tar.gz.part"))
        )

    def test_tampered_cache_entry_is_detected(self):
        fixture, _ = self.fixture()
        self.assertEqual(
            run_tool(
                "--manifest", fixture.path, "--cache-dir", fixture.cache_dir, "fetch"
            ).returncode,
            EXIT_OK,
        )
        cached = os.path.join(fixture.cache_dir, "payload-1.0.tar.gz")
        with open(cached, "ab") as handle:
            handle.write(b"tampered")
        result = run_tool(
            "--manifest", fixture.path, "--cache-dir", fixture.cache_dir, "fetch"
        )
        self.assertEqual(result.returncode, EXIT_INVALID)
        self.assertIn("is pinned", result.stderr)

    def test_unverified_source_is_refused_without_opt_in(self):
        fixture, _ = self.fixture()
        with open(fixture.path, "r", encoding="utf-8") as handle:
            text = handle.read()
        import re

        text = re.sub(r"sha256: [0-9a-f]{64}", "sha256: null", text)
        with open(fixture.path, "w", encoding="utf-8") as handle:
            handle.write(text)

        refused = run_tool(
            "--manifest", fixture.path, "--cache-dir", fixture.cache_dir, "fetch"
        )
        self.assertEqual(refused.returncode, EXIT_INVALID)
        self.assertIn("no sha256 is pinned", refused.stderr)

        allowed = run_tool(
            "--manifest",
            fixture.path,
            "--cache-dir",
            fixture.cache_dir,
            "fetch",
            "--allow-unverified",
        )
        self.assertEqual(allowed.returncode, EXIT_OK, allowed.stderr)

    def test_verify_helper_reports_mismatch(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"content\n")
            path = handle.name
        self.addCleanup(os.remove, path)
        expected = sources_lib.sha256_file(path)
        self.assertEqual(sources_lib.verify(path, expected), (True, expected))
        ok, actual = sources_lib.verify(path, "d" * 64)
        self.assertFalse(ok)
        self.assertEqual(actual, expected)
        self.assertEqual(sources_lib.verify(path, None), (None, None))


if __name__ == "__main__":
    unittest.main()
