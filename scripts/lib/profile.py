#!/usr/bin/env python3
"""Device profile discovery, loading and validation for Linux-Touch.

This module is the single source of truth for turning a device name such as
``virtual-phone`` into a validated profile. It is deliberately dependency
free: PyYAML and jsonschema are used when installed, otherwise a small
built-in reader and a small validator handle the subset of YAML / JSON
Schema the profiles actually use. That removes the previous `yq`
requirement, whose flags differ between the Python and Go implementations.

Exit codes: 0 ok, 1 invalid profile, 2 unknown device / missing profile.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(LIB_DIR))
DEFAULT_DEVICES_DIR = os.path.join(ROOT_DIR, "devices")
SCHEMA_PATH = os.path.join(DEFAULT_DEVICES_DIR, "schema", "device-v1.json")
PROFILE_FILENAME = "device.yaml"

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_UNKNOWN_DEVICE = 2


class ProfileError(Exception):
    """A profile could not be read, resolved or validated."""

    def __init__(self, message, exit_code=EXIT_INVALID):
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------

_FLOW_START = ("{", "[", "&", "*", "|", ">")
_INT_RE = re.compile(r"^[+-]?[0-9]+$")
_FLOAT_RE = re.compile(r"^[+-]?[0-9]*\.[0-9]+$")


def _strip_comment(line):
    """Remove a trailing YAML comment, honouring quoted scalars."""
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


def _scalar(text, lineno):
    if text.startswith(_FLOW_START):
        raise ProfileError(
            "line %d: flow collections, anchors and block scalars are not "
            "supported by the built-in YAML reader (install PyYAML)" % lineno
        )
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~", ""):
        return None
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _tokenize(text):
    tokens = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = _strip_comment(raw)
        content = stripped.strip()
        if not content or content == "---":
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        if "\t" in stripped[:indent]:
            raise ProfileError("line %d: tabs may not be used for indentation" % lineno)
        tokens.append((indent, content, lineno))
    return tokens


def _parse_block(tokens, index, indent):
    if tokens[index][1].startswith("-"):
        return _parse_sequence(tokens, index, indent)
    return _parse_mapping(tokens, index, indent)


def _parse_sequence(tokens, index, indent):
    items = []
    while index < len(tokens) and tokens[index][0] == indent:
        _, content, lineno = tokens[index]
        if not content.startswith("-"):
            break
        item = content[1:].strip()
        if not item or ":" in item:
            raise ProfileError(
                "line %d: nested sequences and lists of mappings are not "
                "supported by the built-in YAML reader (install PyYAML)" % lineno
            )
        items.append(_scalar(item, lineno))
        index += 1
    return items, index


def _parse_mapping(tokens, index, indent):
    mapping = {}
    while index < len(tokens) and tokens[index][0] == indent:
        _, content, lineno = tokens[index]
        if content.startswith("-"):
            break
        if ":" not in content:
            raise ProfileError("line %d: expected 'key: value'" % lineno)
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not key:
            raise ProfileError("line %d: empty key" % lineno)
        if rest:
            mapping[key] = _scalar(rest, lineno)
            index += 1
            continue
        index += 1
        if index < len(tokens) and tokens[index][0] > indent:
            mapping[key], index = _parse_block(tokens, index, tokens[index][0])
        elif (
            index < len(tokens)
            and tokens[index][0] == indent
            and tokens[index][1].startswith("-")
        ):
            mapping[key], index = _parse_sequence(tokens, index, indent)
        else:
            mapping[key] = None
    return mapping, index


def load_yaml(text):
    """Parse a YAML document, preferring PyYAML when it is installed."""
    try:
        import yaml  # type: ignore
    except ImportError:
        pass
    else:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:  # pragma: no cover - message passthrough
            raise ProfileError("invalid YAML: %s" % exc)

    tokens = _tokenize(text)
    if not tokens:
        return None
    value, index = _parse_block(tokens, 0, tokens[0][0])
    if index != len(tokens):
        raise ProfileError("line %d: unexpected indentation" % tokens[index][2])
    return value


# ---------------------------------------------------------------------------
# JSON Schema (subset)
# ---------------------------------------------------------------------------

_SUPPORTED_KEYWORDS = {
    "$schema", "$id", "$defs", "$ref", "title", "description",
    "type", "enum", "const", "required", "properties", "additionalProperties",
    "minProperties", "items", "minItems", "uniqueItems", "minLength",
    "pattern", "minimum", "maximum", "allOf", "if", "then", "else",
}


def _type_matches(value, expected):
    if isinstance(expected, list):
        return any(_type_matches(value, option) for option in expected)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ProfileError("schema uses unsupported type '%s'" % expected)


def _resolve_ref(ref, root):
    if not ref.startswith("#/"):
        raise ProfileError("schema uses unsupported $ref '%s'" % ref)
    node = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _describe(path):
    return path or "<root>"


def _validate(value, schema, root, path, errors):
    if "$ref" in schema:
        _validate(value, _resolve_ref(schema["$ref"], root), root, path, errors)
        return

    unsupported = set(schema) - _SUPPORTED_KEYWORDS
    if unsupported:
        raise ProfileError(
            "schema uses unsupported keywords: %s" % ", ".join(sorted(unsupported))
        )

    if "type" in schema and not _type_matches(value, schema["type"]):
        errors.append("%s: expected %s" % (_describe(path), schema["type"]))
        return

    if "const" in schema and value != schema["const"]:
        errors.append(
            "%s: must be %s" % (_describe(path), json.dumps(schema["const"]))
        )
    if "enum" in schema and value not in schema["enum"]:
        errors.append(
            "%s: %s is not one of %s"
            % (
                _describe(path),
                json.dumps(value),
                ", ".join(json.dumps(item) for item in schema["enum"]),
            )
        )
    if "pattern" in schema and isinstance(value, str):
        if not re.search(schema["pattern"], value):
            errors.append(
                "%s: %s does not match %s"
                % (_describe(path), json.dumps(value), schema["pattern"])
            )
    if "minLength" in schema and isinstance(value, str):
        if len(value) < schema["minLength"]:
            errors.append(
                "%s: must be at least %d characters"
                % (_describe(path), schema["minLength"])
            )
    if "minimum" in schema and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema["minimum"]:
            errors.append("%s: must be >= %s" % (_describe(path), schema["minimum"]))
    if "maximum" in schema and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value > schema["maximum"]:
            errors.append("%s: must be <= %s" % (_describe(path), schema["maximum"]))

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(
                    "%s: missing required field '%s'" % (_describe(path), key)
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(
                "%s: must define at least %d entries"
                % (_describe(path), schema["minProperties"])
            )
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child = "%s.%s" % (path, key) if path else key
            if key in properties:
                _validate(item, properties[key], root, child, errors)
            elif additional is False:
                errors.append("%s: unknown field" % child)
            elif isinstance(additional, dict):
                _validate(item, additional, root, child, errors)

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(
                "%s: must list at least %d entries"
                % (_describe(path), schema["minItems"])
            )
        if schema.get("uniqueItems") and len(value) != len(
            {json.dumps(item, sort_keys=True) for item in value}
        ):
            errors.append("%s: entries must be unique" % _describe(path))
        if "items" in schema:
            for position, item in enumerate(value):
                _validate(
                    item, schema["items"], root, "%s[%d]" % (_describe(path), position), errors
                )

    for subschema in schema.get("allOf", []):
        _validate(value, subschema, root, path, errors)

    if "if" in schema:
        probe = []
        _validate(value, schema["if"], root, path, probe)
        branch = "then" if not probe else "else"
        if branch in schema:
            _validate(value, schema[branch], root, path, errors)


def validate_against_schema(profile, schema):
    """Return a list of human readable schema violations."""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        pass
    else:
        validator = jsonschema.Draft202012Validator(schema)
        return [
            "%s: %s"
            % (_describe(".".join(str(part) for part in error.path)), error.message)
            for error in sorted(validator.iter_errors(profile), key=lambda e: list(e.path))
        ]

    errors = []
    _validate(profile, schema, schema, "", errors)
    return errors


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def load_schema(schema_path=SCHEMA_PATH):
    if not os.path.isfile(schema_path):
        raise ProfileError("missing device profile schema: %s" % schema_path)
    with open(schema_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def semantic_errors(profile, profile_path):
    """Checks the schema cannot express."""
    errors = []
    directory = os.path.basename(os.path.dirname(os.path.abspath(profile_path)))
    identifier = profile.get("id")
    if isinstance(identifier, str) and identifier != directory:
        errors.append(
            "id: '%s' does not match the profile directory name '%s'"
            % (identifier, directory)
        )

    orientation = profile.get("orientation")
    if isinstance(orientation, dict):
        default = orientation.get("default")
        supported = orientation.get("supported")
        if isinstance(supported, list) and default is not None and default not in supported:
            errors.append(
                "orientation.default: '%s' is not listed in orientation.supported"
                % default
            )

    runner = profile.get("runner")
    boot = profile.get("boot")
    if isinstance(runner, dict) and isinstance(boot, dict):
        runner_type = runner.get("type")
        method = boot.get("method")
        if runner_type and method and runner_type != method:
            errors.append(
                "boot.method: '%s' does not match runner.type '%s'" % (method, runner_type)
            )
    return errors


def load_profile(profile_path, schema_path=SCHEMA_PATH):
    """Read and fully validate a profile. Raises ProfileError on failure."""
    if not os.path.isfile(profile_path):
        raise ProfileError(
            "missing device profile: %s" % profile_path, EXIT_UNKNOWN_DEVICE
        )
    with open(profile_path, "r", encoding="utf-8") as handle:
        profile = load_yaml(handle.read())
    if not isinstance(profile, dict):
        raise ProfileError("%s: profile must be a YAML mapping" % profile_path)

    schema = load_schema(schema_path)
    errors = validate_against_schema(profile, schema)
    errors.extend(semantic_errors(profile, profile_path))
    if errors:
        raise ProfileError(
            "%s: invalid device profile\n%s"
            % (profile_path, "\n".join("  - %s" % error for error in errors))
        )
    return profile


def profile_path_for(name, devices_dir=DEFAULT_DEVICES_DIR):
    return os.path.join(devices_dir, name, PROFILE_FILENAME)


def discover(devices_dir=DEFAULT_DEVICES_DIR):
    """Return the sorted device names found on disk."""
    if not os.path.isdir(devices_dir):
        raise ProfileError(
            "missing devices directory: %s" % devices_dir, EXIT_UNKNOWN_DEVICE
        )
    names = [
        entry
        for entry in os.listdir(devices_dir)
        if os.path.isfile(profile_path_for(entry, devices_dir))
    ]
    return sorted(names)


def resolve(name, devices_dir=DEFAULT_DEVICES_DIR):
    """Map a device name to its profile path, or raise with the known names."""
    available = discover(devices_dir)
    if name not in available:
        raise ProfileError(
            "unknown device: %s\navailable devices: %s"
            % (name, ", ".join(available) if available else "(none)"),
            EXIT_UNKNOWN_DEVICE,
        )
    return profile_path_for(name, devices_dir)


def shell_environment(name, devices_dir=DEFAULT_DEVICES_DIR, schema_path=SCHEMA_PATH):
    """Validated profile as shell assignments consumed by scripts/lib/profile.sh."""
    path = resolve(name, devices_dir)
    profile = load_profile(path, schema_path)
    runner = profile["runner"]
    kernel = profile["kernel"]
    values = [
        ("LT_DEVICE_ID", profile["id"]),
        ("LT_DEVICE_NAME", profile["name"]),
        ("LT_DEVICE_PROFILE", os.path.abspath(path)),
        ("LT_DEVICE_ARCH", profile["architecture"]),
        ("LT_DEVICE_KERNEL_SOURCE", kernel["source"]),
        ("LT_DEVICE_KERNEL_DEFCONFIG", kernel["defconfig"]),
        ("LT_DEVICE_KERNEL_IMAGE", kernel["image"]),
        ("LT_DEVICE_KERNEL_FRAGMENTS", " ".join(kernel.get("config_fragments") or [])),
        ("LT_RUNNER_TYPE", runner["type"]),
        ("LT_BOOT_METHOD", profile["boot"]["method"]),
    ]
    if runner["type"] == "qemu":
        qemu = runner["qemu"]
        values.extend(
            [
                ("LT_QEMU_MACHINE", qemu["machine"]),
                ("LT_QEMU_CPU", qemu["cpu"]),
                ("LT_QEMU_MEMORY", qemu["memory"]),
                ("LT_QEMU_CONSOLE", qemu["console"]),
            ]
        )
    return "\n".join("%s=%s" % (key, shlex.quote(str(value))) for key, value in values)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--devices-dir", default=DEFAULT_DEVICES_DIR, help="directory holding device profiles"
    )
    parser.add_argument("--schema", default=SCHEMA_PATH, help="device profile schema")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list discovered device names")
    resolve_parser = commands.add_parser("resolve", help="print a device profile path")
    resolve_parser.add_argument("device")
    validate_parser = commands.add_parser("validate", help="validate a profile file")
    validate_parser.add_argument("profile")
    commands.add_parser("validate-all", help="validate every discovered profile")
    env_parser = commands.add_parser("env", help="print a validated profile as shell variables")
    env_parser.add_argument("device")

    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            for name in discover(args.devices_dir):
                print(name)
        elif args.command == "resolve":
            print(resolve(args.device, args.devices_dir))
        elif args.command == "validate":
            load_profile(args.profile, args.schema)
            print("%s: ok" % args.profile)
        elif args.command == "validate-all":
            names = discover(args.devices_dir)
            if not names:
                raise ProfileError(
                    "no device profiles found in %s" % args.devices_dir,
                    EXIT_UNKNOWN_DEVICE,
                )
            for name in names:
                load_profile(profile_path_for(name, args.devices_dir), args.schema)
                print("%s: ok" % name)
        elif args.command == "env":
            print(shell_environment(args.device, args.devices_dir, args.schema))
    except ProfileError as error:
        print("error: %s" % error, file=sys.stderr)
        return error.exit_code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
