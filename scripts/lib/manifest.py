#!/usr/bin/env python3
"""Build manifest generation for Linux-Touch.

Records what a build was made from: the device profile, the pinned sources,
the actual input artifacts, the produced artifacts and the build epoch.

Paths are recorded relative to the repository root and no host, user or
environment details are included, so the manifest is safe to publish and is
byte-identical for two builds of the same inputs. There is deliberately no
build timestamp: it would defeat that property.

Exit codes: 0 ok, 1 failure.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(LIB_DIR))

MANIFEST_SCHEMA_VERSION = 1
CROSS_PREFIXES = (
    "aarch64-linux-gnu-",
    "aarch64-unknown-linux-gnu-",
    "aarch64-none-linux-gnu-",
)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_profile = _load("lt_profile", "profile.py")
_sources = _load("lt_sources", "sources.py")

ManifestError = _profile.ProfileError


def _relative(path, root):
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root))


def file_record(path, root):
    if not os.path.isfile(path):
        raise ManifestError("missing file for manifest: %s" % path)
    return {
        "path": _relative(path, root),
        "sha256": _sources.sha256_file(path),
        "size": os.path.getsize(path),
    }


def detect_toolchain():
    """Advisory toolchain info.

    The current pipeline compiles nothing, so this records what a compile
    stage would use rather than what produced the inputs. Only the version
    string is kept; paths would leak host layout.
    """
    prefix = os.environ.get("CROSS_COMPILE") or ""
    candidates = [prefix] if prefix else list(CROSS_PREFIXES)
    for candidate in candidates:
        try:
            output = subprocess.run(
                ["%sgcc" % candidate, "--version"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
        except (OSError, subprocess.CalledProcessError):
            continue
        if output:
            return {"cross_compile": candidate, "compiler": output[0].strip()}
    return {"cross_compile": None, "compiler": None}


def repository_state(root):
    def git(*args):
        try:
            result = subprocess.run(
                ["git", "-C", root, *args], capture_output=True, text=True, check=True
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return result.stdout.strip()

    commit = git("rev-parse", "HEAD")
    if commit is None:
        return {"git_commit": None, "git_dirty": None}
    status = git("status", "--porcelain")
    return {"git_commit": commit, "git_dirty": bool(status)}


def build_manifest(
    root,
    device_profile_path,
    build_id,
    source_date_epoch,
    inputs,
    artifacts,
    sources_manifest_path=None,
):
    profile = _profile.load_profile(device_profile_path)

    pinned = {}
    if sources_manifest_path and os.path.isfile(sources_manifest_path):
        manifest = _sources.load_manifest(sources_manifest_path)
        for source in manifest["sources"]:
            pinned[source["name"]] = {
                "version": source["version"],
                "url": source["url"],
                "sha256": source.get("sha256"),
                "checksum_source": source.get("checksum_source"),
            }

    input_records = {}
    for name, path in sorted(inputs.items()):
        record = file_record(path, root)
        # The prebuilt kernel and BusyBox in this tree were produced by hand
        # before any pin existed, so they cannot be attributed to the pinned
        # sources. Say so rather than implying provenance we do not have.
        record["provenance"] = "prebuilt-unverified"
        input_records[name] = record

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "device": {
            "id": profile["id"],
            "architecture": profile["architecture"],
            "profile": _relative(device_profile_path, root),
            "profile_sha256": _sources.sha256_file(device_profile_path),
        },
        "build": {
            "id": build_id,
            "userspace": "busybox prototype initramfs",
            "source_date_epoch": int(source_date_epoch),
            "compiles_sources": False,
        },
        "pinned_sources": pinned,
        "inputs": input_records,
        "artifacts": {
            name: file_record(path, root) for name, path in sorted(artifacts.items())
        },
        "toolchain": detect_toolchain(),
        "repository": repository_state(root),
    }


def write_manifest(manifest, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return output_path


def _pairs(values):
    result = {}
    for value in values or []:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise ManifestError("expected name=path, got: %s" % value)
        result[name] = path
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=ROOT_DIR)
    parser.add_argument("--device-profile", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    parser.add_argument("--sources", default=os.path.join(ROOT_DIR, "sources.yaml"))
    parser.add_argument("--input", action="append", metavar="NAME=PATH")
    parser.add_argument("--artifact", action="append", metavar="NAME=PATH")
    parser.add_argument("--output", required=True)

    args = parser.parse_args(argv)

    try:
        manifest = build_manifest(
            root=args.root,
            device_profile_path=args.device_profile,
            build_id=args.build_id,
            source_date_epoch=args.source_date_epoch,
            inputs=_pairs(args.input),
            artifacts=_pairs(args.artifact),
            sources_manifest_path=args.sources,
        )
        write_manifest(manifest, args.output)
        print(args.output)
    except ManifestError as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
