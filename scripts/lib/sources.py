#!/usr/bin/env python3
"""Pinned source manifest handling for Linux-Touch.

Reads sources.yaml, validates it against schema/sources-v1.json, and
downloads pinned tarballs into a local cache, verifying the recorded
sha256. A source whose sha256 is null is refused unless the caller opts in
with --allow-unverified, so a build can never silently pick up whatever
upstream happens to serve today.

Exit codes: 0 ok, 1 invalid manifest / checksum mismatch, 2 unknown source.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(LIB_DIR))
DEFAULT_MANIFEST = os.path.join(ROOT_DIR, "sources.yaml")
SCHEMA_PATH = os.path.join(ROOT_DIR, "schema", "sources-v1.json")
DEFAULT_CACHE_DIR = os.path.join(ROOT_DIR, ".cache", "sources")

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_UNKNOWN_SOURCE = 2


def _load_profile_module():
    """Reuse the YAML reader and schema validator from profile.py.

    Loaded by path so the local 'profile' module never shadows the standard
    library module of the same name.
    """
    spec = importlib.util.spec_from_file_location(
        "lt_profile", os.path.join(LIB_DIR, "profile.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_profile = _load_profile_module()
SourceError = _profile.ProfileError


def sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(manifest_path=DEFAULT_MANIFEST, schema_path=SCHEMA_PATH):
    """Read and validate the source manifest."""
    if not os.path.isfile(manifest_path):
        raise SourceError("missing source manifest: %s" % manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = _profile.load_yaml(handle.read())
    if not isinstance(manifest, dict):
        raise SourceError("%s: manifest must be a YAML mapping" % manifest_path)

    schema = _profile.load_schema(schema_path)
    errors = _profile.validate_against_schema(manifest, schema)

    names = [
        source.get("name")
        for source in manifest.get("sources", [])
        if isinstance(source, dict)
    ]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    for name in duplicates:
        errors.append("sources: duplicate source name '%s'" % name)

    if errors:
        raise SourceError(
            "%s: invalid source manifest\n%s"
            % (manifest_path, "\n".join("  - %s" % error for error in errors))
        )
    return manifest


def source_date_epoch(manifest):
    return int(manifest["defaults"]["source_date_epoch"])


def get_source(manifest, name):
    for source in manifest["sources"]:
        if source["name"] == name:
            return source
    raise SourceError(
        "unknown source: %s\npinned sources: %s"
        % (name, ", ".join(source["name"] for source in manifest["sources"])),
        EXIT_UNKNOWN_SOURCE,
    )


def cached_path(source, cache_dir=DEFAULT_CACHE_DIR):
    return os.path.join(cache_dir, os.path.basename(source["url"]))


def verify(path, expected_sha256):
    """Return (ok, actual). ok is None when there is nothing to check against."""
    if expected_sha256 is None:
        return None, None
    actual = sha256_file(path)
    return actual == expected_sha256, actual


def fetch_source(source, cache_dir=DEFAULT_CACHE_DIR, allow_unverified=False):
    """Download one pinned source if needed and verify it. Returns its path."""
    expected = source.get("sha256")
    if expected is None and not allow_unverified:
        raise SourceError(
            "%s: no sha256 is pinned for this source.\n"
            "Record the checksum published by upstream in sources.yaml, or "
            "re-run with --allow-unverified to accept an unverified download."
            % source["name"]
        )

    destination = cached_path(source, cache_dir)
    if os.path.isfile(destination):
        ok, actual = verify(destination, expected)
        if ok is None:
            return destination
        if ok:
            return destination
        raise SourceError(
            "%s: cached file %s has sha256 %s but %s is pinned.\n"
            "Delete the cached file if you trust the pin, or correct the pin."
            % (source["name"], destination, actual, expected)
        )

    os.makedirs(cache_dir, exist_ok=True)
    partial = destination + ".part"
    try:
        with urllib.request.urlopen(source["url"]) as response, open(
            partial, "wb"
        ) as handle:
            shutil.copyfileobj(response, handle)
    except (urllib.error.URLError, OSError, ValueError) as error:
        if os.path.exists(partial):
            os.remove(partial)
        raise SourceError("%s: download failed: %s" % (source["name"], error))

    ok, actual = verify(partial, expected)
    if ok is False:
        os.remove(partial)
        raise SourceError(
            "%s: checksum mismatch for %s\n  expected %s\n  actual   %s\n"
            "The download was discarded."
            % (source["name"], source["url"], expected, actual)
        )

    os.replace(partial, destination)
    return destination


def describe(source, cache_dir=DEFAULT_CACHE_DIR):
    """Manifest-ready description of a pinned source."""
    return {
        "version": source["version"],
        "url": source["url"],
        "sha256": source.get("sha256"),
        "checksum_source": source.get("checksum_source"),
        "cached": os.path.isfile(cached_path(source, cache_dir)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--schema", default=SCHEMA_PATH)
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="validate the source manifest")
    commands.add_parser("list", help="list pinned sources")
    commands.add_parser("epoch", help="print the pinned SOURCE_DATE_EPOCH default")
    fetch_parser = commands.add_parser("fetch", help="download and verify pinned sources")
    fetch_parser.add_argument("names", nargs="*", help="sources to fetch (default: all)")
    fetch_parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="permit sources that have no pinned sha256",
    )
    describe_parser = commands.add_parser("describe", help="print a source as JSON")
    describe_parser.add_argument("name")

    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest, args.schema)

        if args.command == "validate":
            print("%s: ok (%d pinned sources)" % (args.manifest, len(manifest["sources"])))
        elif args.command == "list":
            for source in manifest["sources"]:
                print(
                    "%-10s %-10s %s"
                    % (
                        source["name"],
                        source["version"],
                        "sha256 pinned" if source.get("sha256") else "UNVERIFIED",
                    )
                )
        elif args.command == "epoch":
            print(source_date_epoch(manifest))
        elif args.command == "describe":
            print(
                json.dumps(
                    describe(get_source(manifest, args.name), args.cache_dir),
                    indent=2,
                    sort_keys=True,
                )
            )
        elif args.command == "fetch":
            names = args.names or [source["name"] for source in manifest["sources"]]
            for name in names:
                source = get_source(manifest, name)
                path = fetch_source(source, args.cache_dir, args.allow_unverified)
                print("%s %s: %s" % (name, source["version"], path))
    except SourceError as error:
        print("error: %s" % error, file=sys.stderr)
        return error.exit_code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
