#!/usr/bin/env python3
"""The host tools Linux-Touch needs, in one place.

This module is the single source of truth. scripts/check-env.sh reports
from it, and the builder checks against it, so a developer cannot be told
one thing by the environment check and another by the build.

Groups:
  core          always needed, even to assemble an initramfs from prebuilts
  source-build  needed to compile the pinned kernel and BusyBox
  qemu          needed to boot a build
  recommended   not required, but checks are weaker without them
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

GROUPS = ("core", "source-build", "qemu", "recommended")

# Probed in order when CROSS_COMPILE is not set. Every consumer resolves the
# cross toolchain through this list.
CROSS_PREFIXES = (
    "aarch64-linux-gnu-",
    "aarch64-unknown-linux-gnu-",
    "aarch64-none-linux-gnu-",
)
QEMU_BINARY = "qemu-system-aarch64"

# kind: "command" | "cross-compiler" | "cpio-reproducible" | "pkgconfig"
REQUIREMENTS = (
    {
        "name": "python3",
        "group": "core",
        "why": "runs the profile, manifest and source tooling",
        "packages": {"arch": "python", "debian": "python3"},
    },
    {
        "name": "find",
        "group": "core",
        "why": "lists root filesystem members in a deterministic order",
        "packages": {"arch": "findutils", "debian": "findutils"},
    },
    {
        "name": "sort",
        "group": "core",
        "why": "sorts those members (GNU sort, for -z)",
        "packages": {"arch": "coreutils", "debian": "coreutils"},
    },
    {
        "name": "stat",
        "group": "core",
        "why": "reads modes and timestamps when normalising the tree",
        "packages": {"arch": "coreutils", "debian": "coreutils"},
    },
    {
        "name": "touch",
        "group": "core",
        "why": "pins mtimes to SOURCE_DATE_EPOCH",
        "packages": {"arch": "coreutils", "debian": "coreutils"},
    },
    {
        "name": "chmod",
        "group": "core",
        "why": "normalises modes so the builder's umask cannot leak in",
        "packages": {"arch": "coreutils", "debian": "coreutils"},
    },
    {
        "name": "cpio",
        "group": "core",
        "why": "packs the initramfs archive",
        "packages": {"arch": "cpio", "debian": "cpio"},
    },
    {
        "name": "gzip",
        "group": "core",
        "why": "compresses the initramfs (with -n, for reproducibility)",
        "packages": {"arch": "gzip", "debian": "gzip"},
    },
    {
        "kind": "cpio-reproducible",
        "name": "cpio --reproducible",
        "group": "core",
        "why": "reproducible archives need GNU cpio 2.12 or newer",
        "packages": {"arch": "cpio", "debian": "cpio"},
    },
    {
        "kind": "cross-compiler",
        "name": "aarch64 cross compiler",
        "group": "source-build",
        "why": "compiles the kernel and BusyBox for aarch64",
        "packages": {
            "arch": "aarch64-linux-gnu-gcc",
            "debian": "gcc-aarch64-linux-gnu",
        },
    },
    {
        "name": "make",
        "group": "source-build",
        "why": "drives the kernel and BusyBox builds",
        "packages": {"arch": "make", "debian": "make"},
    },
    {
        "name": "tar",
        "group": "source-build",
        "why": "unpacks the pinned source tarballs",
        "packages": {"arch": "tar", "debian": "tar"},
    },
    {
        "name": "xz",
        "group": "source-build",
        "why": "decompresses the kernel tarball (.tar.xz)",
        "packages": {"arch": "xz", "debian": "xz-utils"},
    },
    {
        "name": "bzip2",
        "group": "source-build",
        "why": "decompresses the BusyBox tarball (.tar.bz2)",
        "packages": {"arch": "bzip2", "debian": "bzip2"},
    },
    {
        "name": "bison",
        "group": "source-build",
        "why": "builds the kernel's kconfig parser",
        "packages": {"arch": "bison", "debian": "bison"},
    },
    {
        "name": "flex",
        "group": "source-build",
        "why": "builds the kernel's kconfig lexer",
        "packages": {"arch": "flex", "debian": "flex"},
    },
    {
        "name": "bc",
        "group": "source-build",
        "why": "used by the kernel build to compute timing constants",
        "packages": {"arch": "bc", "debian": "bc"},
    },
    {
        "name": "sed",
        "group": "source-build",
        "why": "used throughout the kernel and BusyBox build scripts",
        "packages": {"arch": "sed", "debian": "sed"},
    },
    {
        "name": "awk",
        "group": "source-build",
        "why": "used throughout the kernel and BusyBox build scripts",
        "packages": {"arch": "gawk", "debian": "gawk"},
    },
    {
        "name": QEMU_BINARY,
        "group": "qemu",
        "why": "runs the built system; required by run.sh and the boot test",
        "packages": {"arch": "qemu-system-aarch64", "debian": "qemu-system-arm"},
    },
    {
        "name": "file",
        "group": "recommended",
        "why": "verifies built binaries really are aarch64 and statically "
        "linked; without it those checks are skipped",
        "packages": {"arch": "file", "debian": "file"},
    },
    {
        "kind": "pkgconfig",
        "name": "libelf",
        "group": "recommended",
        "why": "some kernel configurations need libelf headers; the arm64 "
        "defconfig used here has built without them",
        "packages": {"arch": "libelf", "debian": "libelf-dev"},
    },
    {
        "kind": "pkgconfig",
        "name": "openssl",
        "group": "recommended",
        "why": "kernel configurations that sign modules need OpenSSL headers",
        "packages": {"arch": "openssl", "debian": "libssl-dev"},
    },
    {
        "name": "git",
        "group": "recommended",
        "why": "lets the build manifest record the commit it was built from",
        "packages": {"arch": "git", "debian": "git"},
    },
)


def requirement_kind(requirement):
    return requirement.get("kind", "command")


def version_of(command):
    """First line of `command --version`, or None when it says nothing."""
    try:
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or result.stderr).splitlines()
    return output[0].strip()[:78] if output else None


def resolve_cross_compile(environment=None):
    """The cross prefix to use, or None. CROSS_COMPILE wins when set."""
    environment = os.environ if environment is None else environment
    configured = environment.get("CROSS_COMPILE") or ""
    candidates = [configured] if configured else list(CROSS_PREFIXES)
    for candidate in candidates:
        if shutil.which("%sgcc" % candidate):
            return candidate
    return None


def cpio_owner_flag():
    """GNU cpio spells forced-numeric ownership '+0:+0'; older builds '0:0'.

    Probing is the only reliable answer, and the archive is unreproducible
    without one of them.
    """
    if shutil.which("cpio") is None:
        return None
    probe = tempfile.mkdtemp(prefix="linux-touch-cpio-")
    try:
        for candidate in ("--owner=+0:+0", "--owner=0:0"):
            try:
                result = subprocess.run(
                    [
                        "cpio", "--null", "--create", "--quiet",
                        "--format=newc", "--reproducible", candidate,
                    ],
                    input=b".\0",
                    capture_output=True,
                    cwd=probe,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0:
                return candidate
        return None
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def check_requirement(requirement, environment=None):
    """Return {name, group, ok, detail, why, packages, optional}."""
    kind = requirement_kind(requirement)
    result = {
        "name": requirement["name"],
        "group": requirement["group"],
        "why": requirement["why"],
        "packages": requirement["packages"],
        "optional": requirement["group"] == "recommended",
        "ok": False,
        "detail": "",
    }

    if kind == "command":
        path = shutil.which(requirement["name"])
        if path:
            result["ok"] = True
            result["detail"] = version_of(requirement["name"]) or path
    elif kind == "cross-compiler":
        prefix = resolve_cross_compile(environment)
        if prefix:
            result["ok"] = True
            result["detail"] = "%sgcc: %s" % (
                prefix,
                version_of("%sgcc" % prefix) or "version unknown",
            )
        else:
            result["detail"] = "tried: %s (or set CROSS_COMPILE)" % ", ".join(
                CROSS_PREFIXES
            )
    elif kind == "cpio-reproducible":
        flag = cpio_owner_flag()
        if flag:
            result["ok"] = True
            result["detail"] = "supports %s" % flag
        else:
            result["detail"] = "cpio accepts neither --owner=+0:+0 nor --owner=0:0"
    elif kind == "pkgconfig":
        if shutil.which("pkg-config") is None:
            result["detail"] = "pkg-config is not installed, so this was not checked"
        else:
            probe = subprocess.run(
                ["pkg-config", "--exists", requirement["name"]], capture_output=True
            )
            if probe.returncode == 0:
                modversion = subprocess.run(
                    ["pkg-config", "--modversion", requirement["name"]],
                    capture_output=True,
                    text=True,
                )
                result["ok"] = True
                result["detail"] = modversion.stdout.strip() or "present"
    else:
        raise ValueError("unknown requirement kind: %s" % kind)
    return result


def check(groups=None, environment=None):
    selected = tuple(groups) if groups else GROUPS
    unknown = [group for group in selected if group not in GROUPS]
    if unknown:
        raise ValueError("unknown group(s): %s" % ", ".join(unknown))
    return [
        check_requirement(requirement, environment)
        for requirement in REQUIREMENTS
        if requirement["group"] in selected
    ]


def install_hint(result):
    packages = result["packages"]
    return "pacman -S %s   |   apt install %s" % (packages["arch"], packages["debian"])


def report(results, stream=sys.stdout):
    missing_required = []
    missing_optional = []

    for group in GROUPS:
        in_group = [result for result in results if result["group"] == group]
        if not in_group:
            continue
        stream.write("\n%s\n" % group)
        for result in in_group:
            if result["ok"]:
                stream.write("  ok       %-24s %s\n" % (result["name"], result["detail"]))
                continue
            label = "missing" if result["optional"] else "MISSING"
            stream.write("  %-8s %-24s %s\n" % (label, result["name"], result["why"]))
            if result["detail"]:
                stream.write("           %-24s %s\n" % ("", result["detail"]))
            stream.write("           %-24s install: %s\n" % ("", install_hint(result)))
            (missing_optional if result["optional"] else missing_required).append(result)

    stream.write("\n")
    if missing_required:
        stream.write(
            "%d required item(s) missing: %s\n"
            % (len(missing_required), ", ".join(r["name"] for r in missing_required))
        )
        stream.write("Install them, then run ./scripts/check-env.sh again.\n")
    elif missing_optional:
        stream.write(
            "Everything required is present. Optional: %s\n"
            % ", ".join(r["name"] for r in missing_optional)
        )
    else:
        stream.write("Everything checked is present.\n")
    return missing_required, missing_optional


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    check_parser = commands.add_parser("check", help="check the host environment")
    check_parser.add_argument(
        "--group", action="append", choices=GROUPS, dest="groups",
        help="limit the check to a group (repeatable; default: all)",
    )
    check_parser.add_argument("--json", action="store_true")
    check_parser.add_argument(
        "--strict", action="store_true", help="also fail on missing recommended items"
    )
    check_parser.add_argument(
        "--quiet", action="store_true", help="print only what is missing"
    )

    list_parser = commands.add_parser("list", help="list requirements")
    list_parser.add_argument("--group", action="append", choices=GROUPS, dest="groups")

    commands_parser = commands.add_parser(
        "commands", help="print the command names in a group, one per line"
    )
    commands_parser.add_argument("--group", action="append", choices=GROUPS, dest="groups")

    commands.add_parser("cross-compile", help="print the resolved cross prefix")
    commands.add_parser("cpio-owner-flag", help="print the cpio ownership flag to use")

    args = parser.parse_args(argv)

    if args.command == "list":
        for requirement in REQUIREMENTS:
            if args.groups and requirement["group"] not in args.groups:
                continue
            print(
                "%-14s %-24s %s"
                % (requirement["group"], requirement["name"], requirement["why"])
            )
        return 0

    if args.command == "commands":
        for requirement in REQUIREMENTS:
            if args.groups and requirement["group"] not in args.groups:
                continue
            if requirement_kind(requirement) == "command":
                print(requirement["name"])
        return 0

    if args.command == "cross-compile":
        prefix = resolve_cross_compile()
        if not prefix:
            print(
                "error: no aarch64 cross compiler found\n"
                "       tried: %s\n"
                "       install one (pacman -S aarch64-linux-gnu-gcc, "
                "apt install gcc-aarch64-linux-gnu) or set CROSS_COMPILE."
                % ", ".join(CROSS_PREFIXES),
                file=sys.stderr,
            )
            return 1
        print(prefix)
        return 0

    if args.command == "cpio-owner-flag":
        flag = cpio_owner_flag()
        if not flag:
            print(
                "error: cpio cannot create reproducible archives\n"
                "       GNU cpio 2.12 or newer is required "
                "(--reproducible and numeric --owner).",
                file=sys.stderr,
            )
            return 1
        print(flag)
        return 0

    results = check(args.groups)

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
        failed = [r for r in results if not r["ok"] and (args.strict or not r["optional"])]
        return 1 if failed else 0

    stream = sys.stderr if args.quiet else sys.stdout
    if args.quiet:
        missing = [r for r in results if not r["ok"] and (args.strict or not r["optional"])]
        if not missing:
            return 0
        stream.write("error: missing host tools required to continue:\n")
        for result in missing:
            stream.write("  %-24s %s\n" % (result["name"], result["why"]))
            stream.write("  %-24s install: %s\n" % ("", install_hint(result)))
        stream.write("Run ./scripts/check-env.sh for the full report.\n")
        return 1

    print("Linux-Touch environment check")
    missing_required, missing_optional = report(results, stream)
    if missing_required or (args.strict and missing_optional):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
