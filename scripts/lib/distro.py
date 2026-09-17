#!/usr/bin/env python3
"""Distro profile discovery, loading and validation for Linux-Touch.

The userspace counterpart of profile.py: a distro profile says what runs
(init system, root filesystem contents, overlay), a device profile says
where it runs (architecture, kernel, boot, runner). The two are validated
separately and checked against each other for architecture compatibility.

Exit codes: 0 ok, 1 invalid profile / incompatible, 2 unknown distro.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shlex
import sys

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(LIB_DIR))
DEFAULT_DISTROS_DIR = os.path.join(ROOT_DIR, "distros")
SCHEMA_PATH = os.path.join(DEFAULT_DISTROS_DIR, "schema", "distro-v1.json")
PROFILE_FILENAME = "distro.yaml"
DEFAULT_DISTRO = "busybox-minimal"

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_UNKNOWN_DISTRO = 2


def _load_profile_module():
    """Reuse the YAML reader and schema validator from profile.py."""
    spec = importlib.util.spec_from_file_location(
        "lt_profile", os.path.join(LIB_DIR, "profile.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_profile = _load_profile_module()
DistroError = _profile.ProfileError


def profile_path_for(name, distros_dir=DEFAULT_DISTROS_DIR):
    return os.path.join(distros_dir, name, PROFILE_FILENAME)


def discover(distros_dir=DEFAULT_DISTROS_DIR):
    if not os.path.isdir(distros_dir):
        raise DistroError(
            "missing distros directory: %s" % distros_dir, EXIT_UNKNOWN_DISTRO
        )
    return sorted(
        entry
        for entry in os.listdir(distros_dir)
        if os.path.isfile(profile_path_for(entry, distros_dir))
    )


def resolve(name, distros_dir=DEFAULT_DISTROS_DIR):
    available = discover(distros_dir)
    if name not in available:
        raise DistroError(
            "unknown distro: %s\navailable distros: %s"
            % (name, ", ".join(available) if available else "(none)"),
            EXIT_UNKNOWN_DISTRO,
        )
    return profile_path_for(name, distros_dir)


def semantic_errors(distro, profile_path):
    errors = []
    directory = os.path.basename(os.path.dirname(os.path.abspath(profile_path)))
    identifier = distro.get("id")
    if isinstance(identifier, str) and identifier != directory:
        errors.append(
            "id: '%s' does not match the profile directory name '%s'"
            % (identifier, directory)
        )

    overlay = distro.get("overlay")
    if isinstance(overlay, str):
        overlay_path = os.path.join(os.path.dirname(os.path.abspath(profile_path)), overlay)
        if not os.path.isdir(overlay_path):
            errors.append("overlay: directory not found: %s" % overlay)

    bootstrap = distro.get("bootstrap")
    rootfs = distro.get("rootfs")
    if isinstance(bootstrap, dict) and isinstance(rootfs, dict):
        binary = bootstrap.get("binary")
        directories = rootfs.get("directories")
        if (
            bootstrap.get("method") == "busybox"
            and isinstance(binary, str)
            and isinstance(directories, list)
        ):
            parent = os.path.dirname(binary)
            if parent and parent not in directories:
                errors.append(
                    "bootstrap.binary: '%s' is not inside any directory listed in "
                    "rootfs.directories" % binary
                )
        if not bootstrap.get("method") == "busybox" and rootfs.get("applets"):
            errors.append(
                "rootfs.applets: applets require bootstrap.method 'busybox'"
            )
    return errors


def load_profile(profile_path, schema_path=SCHEMA_PATH):
    if not os.path.isfile(profile_path):
        raise DistroError(
            "missing distro profile: %s" % profile_path, EXIT_UNKNOWN_DISTRO
        )
    with open(profile_path, "r", encoding="utf-8") as handle:
        distro = _profile.load_yaml(handle.read())
    if not isinstance(distro, dict):
        raise DistroError("%s: profile must be a YAML mapping" % profile_path)

    schema = _profile.load_schema(schema_path)
    errors = _profile.validate_against_schema(distro, schema)
    errors.extend(semantic_errors(distro, profile_path))
    if errors:
        raise DistroError(
            "%s: invalid distro profile\n%s"
            % (profile_path, "\n".join("  - %s" % error for error in errors))
        )
    return distro


def check_architecture(distro, architecture):
    """The one cross-profile rule: the userspace must support the device."""
    if architecture and architecture not in distro["arch_support"]:
        raise DistroError(
            "distro '%s' does not support architecture '%s'\nsupported: %s"
            % (distro["id"], architecture, ", ".join(distro["arch_support"]))
        )


def shell_environment(
    name, distros_dir=DEFAULT_DISTROS_DIR, schema_path=SCHEMA_PATH, architecture=None
):
    path = resolve(name, distros_dir)
    distro = load_profile(path, schema_path)
    check_architecture(distro, architecture)

    directory = os.path.dirname(os.path.abspath(path))
    rootfs = distro["rootfs"]
    bootstrap = distro["bootstrap"]
    overlay = distro.get("overlay")

    values = [
        ("LT_DISTRO_ID", distro["id"]),
        ("LT_DISTRO_NAME", distro["name"]),
        ("LT_DISTRO_PROFILE", os.path.abspath(path)),
        ("LT_DISTRO_DIR", directory),
        ("LT_DISTRO_INIT_SYSTEM", distro["init_system"]),
        ("LT_DISTRO_BOOTSTRAP_METHOD", bootstrap["method"]),
        ("LT_DISTRO_BUSYBOX_PATH", bootstrap.get("binary", "")),
        ("LT_DISTRO_DIRECTORIES", " ".join(rootfs["directories"])),
        ("LT_DISTRO_APPLETS", " ".join(rootfs.get("applets") or [])),
        ("LT_DISTRO_OVERLAY", os.path.join(directory, overlay) if overlay else ""),
        ("LT_DISTRO_OUTPUT_FORMAT", distro["output"]["format"]),
    ]

    # A distro either builds its userspace from a pinned source or consumes
    # a prebuilt binary. Empty values mean "no source build configured".
    build = bootstrap.get("build") or {}
    values.extend(
        [
            ("LT_DISTRO_BUILD_SOURCE", build.get("source", "")),
            ("LT_DISTRO_BUILD_DEFCONFIG", build.get("defconfig", "")),
            ("LT_DISTRO_BUILD_FRAGMENT", build.get("config_fragment", "")),
            (
                "LT_DISTRO_BUILD_STATIC",
                "true" if build.get("static") else "false",
            ),
        ]
    )
    return "\n".join("%s=%s" % (key, shlex.quote(str(value))) for key, value in values)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--distros-dir", default=DEFAULT_DISTROS_DIR)
    parser.add_argument("--schema", default=SCHEMA_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list discovered distro names")
    resolve_parser = commands.add_parser("resolve", help="print a distro profile path")
    resolve_parser.add_argument("distro")
    validate_parser = commands.add_parser("validate", help="validate a profile file")
    validate_parser.add_argument("profile")
    commands.add_parser("validate-all", help="validate every discovered profile")
    env_parser = commands.add_parser("env", help="print a validated profile as shell variables")
    env_parser.add_argument("distro")
    env_parser.add_argument(
        "--arch", default=None, help="device architecture to check compatibility against"
    )

    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            for name in discover(args.distros_dir):
                print(name)
        elif args.command == "resolve":
            print(resolve(args.distro, args.distros_dir))
        elif args.command == "validate":
            load_profile(args.profile, args.schema)
            print("%s: ok" % args.profile)
        elif args.command == "validate-all":
            names = discover(args.distros_dir)
            if not names:
                raise DistroError(
                    "no distro profiles found in %s" % args.distros_dir,
                    EXIT_UNKNOWN_DISTRO,
                )
            for name in names:
                load_profile(profile_path_for(name, args.distros_dir), args.schema)
                print("%s: ok" % name)
        elif args.command == "env":
            print(
                shell_environment(
                    args.distro, args.distros_dir, args.schema, args.arch
                )
            )
    except DistroError as error:
        print("error: %s" % error, file=sys.stderr)
        return error.exit_code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
