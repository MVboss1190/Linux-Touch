#!/usr/bin/env python3
"""Write the component record for one built or copied build component.

Every value is passed explicitly rather than read from the environment: an
inherited-variable mistake here would silently produce a record that
misstates where an artifact came from, which is the one thing these records
exist to get right.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--provenance", required=True, choices=["built-from-source", "prebuilt-unverified"]
    )
    parser.add_argument("--binary", required=True, help="the produced or copied file")
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--source-name")
    parser.add_argument("--source-version")
    parser.add_argument("--source-sha256")
    parser.add_argument("--defconfig")
    parser.add_argument("--config")
    parser.add_argument("--fragment", action="append", default=[])
    parser.add_argument("--cross-compile")
    parser.add_argument("--compiler")
    parser.add_argument("--static", choices=["true", "false"])
    parser.add_argument("--prebuilt-path")

    args = parser.parse_args(argv)

    if not os.path.isfile(args.binary):
        print("error: no such file: %s" % args.binary, file=sys.stderr)
        return 1

    record = {
        "provenance": args.provenance,
        "architecture": args.architecture,
        "binary_sha256": sha256(args.binary),
        "source_date_epoch": args.source_date_epoch,
    }

    if args.provenance == "built-from-source":
        for required, value in (
            ("--source-name", args.source_name),
            ("--source-version", args.source_version),
            ("--config", args.config),
            ("--cross-compile", args.cross_compile),
        ):
            if not value:
                print("error: %s is required for a source build" % required, file=sys.stderr)
                return 1
        if not os.path.isfile(args.config):
            print("error: no such config: %s" % args.config, file=sys.stderr)
            return 1

        record.update(
            {
                "source": {
                    "name": args.source_name,
                    "version": args.source_version,
                    "tarball_sha256": args.source_sha256,
                },
                "defconfig": args.defconfig,
                "config_fragments": args.fragment,
                "config_sha256": sha256(args.config),
                "toolchain": {
                    "cross_compile": args.cross_compile,
                    "compiler": args.compiler,
                },
            }
        )
        if args.static is not None:
            record["static"] = args.static == "true"
    else:
        record.update(
            {
                "source": None,
                "prebuilt_path": args.prebuilt_path,
                "note": "copied from a prebuilt artifact; this project did not "
                "build it and cannot attribute it to the pinned source",
            }
        )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
