#!/usr/bin/env python3
"""Read and verify a Linux-Touch build output.

manifest.json is authoritative: it names the artifacts, their paths and
their hashes. Consumers resolve artifacts through it instead of guessing
paths, so a missing, corrupted or foreign build output is caught before
anything tries to boot it.

Exit codes: 0 ok, 1 unusable build output, 2 nothing built yet.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import sys

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(LIB_DIR))
MANIFEST_FILENAME = "manifest.json"
SUPPORTED_SCHEMA_VERSIONS = (4,)
REQUIRED_ARTIFACTS = ("kernel_image", "initramfs")

EXIT_OK = 0
EXIT_UNUSABLE = 1
# Deliberately not 2: argparse exits 2 on a usage error, and a caller must
# never mistake "this tool was called wrongly" for "nothing is built yet"
# and silently fall back.
EXIT_NOT_BUILT = 3


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_sources = _load("lt_sources", "sources.py")


class OutputError(Exception):
    def __init__(self, message, exit_code=EXIT_UNUSABLE):
        super().__init__(message)
        self.exit_code = exit_code


def load_manifest(out_dir):
    path = os.path.join(out_dir, MANIFEST_FILENAME)
    if not os.path.isfile(path):
        raise OutputError(
            "no build output in %s\nRun ./scripts/build.sh first." % out_dir,
            EXIT_NOT_BUILT,
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as error:
        raise OutputError("%s: unreadable manifest: %s" % (path, error))

    version = manifest.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise OutputError(
            "%s: manifest schema_version %r is not supported (expected %s)\n"
            "Rebuild with ./scripts/build.sh."
            % (path, version, ", ".join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS))
        )
    return manifest, path


def check_identity(manifest, manifest_path, device, distro):
    """Refuse a build output that belongs to another device or distro."""
    actual_device = manifest.get("device", {}).get("id")
    actual_distro = manifest.get("distro", {}).get("id")
    if device and actual_device != device:
        raise OutputError(
            "%s: this build output was produced for device '%s', not '%s'.\n"
            "Rebuild with ./scripts/build.sh %s."
            % (manifest_path, actual_device, device, device)
        )
    if distro and actual_distro != distro:
        raise OutputError(
            "%s: this build output was produced for distro '%s', not '%s'.\n"
            "Rebuild with ./scripts/build.sh %s --distro %s."
            % (manifest_path, actual_distro, distro, device or "", distro)
        )


def verify_artifacts(manifest, manifest_path, root, verify_hashes=True):
    """Resolve every artifact, checking presence and content."""
    artifacts = manifest.get("artifacts") or {}
    missing_names = [name for name in REQUIRED_ARTIFACTS if name not in artifacts]
    if missing_names:
        raise OutputError(
            "%s: manifest does not record %s"
            % (manifest_path, ", ".join(sorted(missing_names)))
        )

    resolved = {}
    for name, record in sorted(artifacts.items()):
        path = os.path.join(root, record["path"])
        if not os.path.isfile(path):
            raise OutputError(
                "missing artifact '%s':\n       %s\n"
                "The manifest records it but it is not on disk. Rebuild with "
                "./scripts/build.sh." % (name, path)
            )
        if verify_hashes:
            actual = _sources.sha256_file(path)
            if actual != record["sha256"]:
                raise OutputError(
                    "artifact '%s' does not match the manifest:\n"
                    "       %s\n  expected %s\n  actual   %s\n"
                    "The build output is corrupted or was modified. Rebuild "
                    "with ./scripts/build.sh."
                    % (name, path, record["sha256"], actual)
                )
        resolved[name] = path
    return resolved


def inspect(out_dir, root=ROOT_DIR, device=None, distro=None, verify_hashes=True):
    manifest, manifest_path = load_manifest(out_dir)
    check_identity(manifest, manifest_path, device, distro)
    return manifest, verify_artifacts(manifest, manifest_path, root, verify_hashes)


def shell_environment(out_dir, manifest, artifacts):
    values = [
        ("LT_OUT_DIR", os.path.abspath(out_dir)),
        ("LT_OUT_DEVICE", manifest["device"]["id"]),
        ("LT_OUT_DISTRO", manifest["distro"]["id"]),
        ("LT_OUT_ARCH", manifest["device"]["architecture"]),
        ("LT_OUT_SOURCE_DATE_EPOCH", manifest["build"]["source_date_epoch"]),
        ("LT_OUT_KERNEL", artifacts["kernel_image"]),
        ("LT_OUT_INITRAMFS", artifacts["initramfs"]),
    ]
    return "\n".join("%s=%s" % (key, shlex.quote(str(value))) for key, value in values)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=ROOT_DIR)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--device", default=None)
    parser.add_argument("--distro", default=None)
    parser.add_argument(
        "--skip-hash-check",
        action="store_true",
        help="check that artifacts exist but do not hash them",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("env", help="print the verified artifacts as shell variables")
    commands.add_parser("verify", help="verify the build output and report")

    args = parser.parse_args(argv)

    try:
        manifest, artifacts = inspect(
            args.out_dir,
            args.root,
            args.device,
            args.distro,
            verify_hashes=not args.skip_hash_check,
        )
        if args.command == "env":
            print(shell_environment(args.out_dir, manifest, artifacts))
        else:
            print(
                "%s: ok (%s / %s)"
                % (args.out_dir, manifest["device"]["id"], manifest["distro"]["id"])
            )
            for name, path in sorted(artifacts.items()):
                print("  %-13s %s" % (name, os.path.relpath(path, args.root)))
    except OutputError as error:
        print("error: %s" % error, file=sys.stderr)
        return error.exit_code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
