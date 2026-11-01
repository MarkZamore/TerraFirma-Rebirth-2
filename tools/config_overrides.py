#!/usr/bin/env python3
"""Project declared TOML boolean overrides without changing author source bytes."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tomllib

POLICY_PATH = 'tools/pack-config-overrides.json'
MAX_POLICY_BYTES = 64 * 1024
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_OVERRIDES = 64
_BOM = b'\xef\xbb\xbf'
_SENTINEL = '__pack_projection_table_marker__'
_ASSIGNMENT = re.compile(
    r'^[ \t]*(?P<key>[A-Za-z0-9_-]+|"(?:[^"\\\r\n]|\\.)*"|\'[^\'\r\n]*\')'
    r'[ \t]*=[ \t]*(?P<value>true|false)[ \t]*(?:\#[^\r\n]*)?(?:\r\n|\n|\r)?$')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _name(value):
    return (isinstance(value, str) and 0 < len(value) <= 128
            and all(ord(character) >= 32 and ord(character) != 127 for character in value))


def _validate_target(table, key, value):
    _require(isinstance(table, list) and 0 < len(table) <= 8 and all(_name(part) for part in table),
             'Override table must contain 1 to 8 TOML key names')
    _require(_name(key), 'Override key must be a nonempty TOML key name')
    _require(type(value) is bool, 'Override value must be a boolean')


def _decode_toml(raw):
    _require(isinstance(raw, bytes) and len(raw) <= MAX_CONFIG_BYTES, 'Config exceeds the byte limit')
    try:
        text = raw.decode('utf-8-sig')
        return text, tomllib.loads(text)
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError('Config must contain valid UTF-8 TOML: ' + str(error)) from error


def _target(document, table, key):
    current = document
    for part in table:
        _require(isinstance(current, dict) and part in current, 'Override table is missing')
        current = current[part]
    _require(isinstance(current, dict) and key in current, 'Override key is missing')
    _require(type(current[key]) is bool, 'Override target is not a TOML boolean')
    return current


def _same_semantics(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_semantics(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_semantics(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and math.isnan(left):
        return math.isnan(right)
    return left == right


def _table_path(line):
    """Let the native TOML parser resolve quoted and dotted table headers."""
    try:
        document = tomllib.loads(line.rstrip('\r\n') + '\n' + _SENTINEL + ' = true\n')
    except tomllib.TOMLDecodeError:
        return None
    path = []
    current = document
    while isinstance(current, dict) and len(current) == 1:
        name, child = next(iter(current.items()))
        if name == _SENTINEL and child is True:
            return tuple(path)
        path.append(name)
        current = child
    return None


def transform_toml_boolean(raw: bytes, table: list[str], key: str, value: bool) -> bytes:
    """Replace one exact boolean token, retaining BOM, comments and line endings.

    Ambiguous lexical matches, missing targets, duplicate/invalid TOML, inline
    tables, and other unsupported representations fail before returning bytes.
    Native parsing checks that every other value retains its original semantics.
    """
    _validate_target(table, key, value)
    text, original = _decode_toml(raw)
    _target(original, table, key)
    expected = copy.deepcopy(original)
    _target(expected, table, key)[key] = value
    current_table = ()
    candidates = []
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.lstrip(' \t').startswith('['):
            current_table = _table_path(line)
        elif current_table == tuple(table):
            match = _ASSIGNMENT.fullmatch(line)
            if match is not None:
                parsed_key = tomllib.loads(match['key'] + ' = true')
                if list(parsed_key) == [key]:
                    candidates.append((offset + match.start('value'), offset + match.end('value')))
        offset += len(line)
    _require(len(candidates) == 1, 'Override requires exactly one unambiguous standalone boolean assignment')
    start, end = candidates[0]
    projected_text = text[:start] + ('true' if value else 'false') + text[end:]
    try:
        projected = tomllib.loads(projected_text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError('Projected config is not valid TOML') from error
    _require(_same_semantics(expected, projected), 'Projection changed TOML values outside the declared override')
    result = (_BOM if raw.startswith(_BOM) else b'') + projected_text.encode('utf-8')
    _require(len(result) <= MAX_CONFIG_BYTES, 'Projected config exceeds the byte limit')
    return result


def _safe_path(pack, relative, config_only=False):
    _require(isinstance(relative, str) and 0 < len(relative) <= 240 and '\\' not in relative
             and ':' not in relative and all(part not in {'', '.', '..'} for part in relative.split('/')),
             'Unsafe override path')
    parsed = PurePosixPath(relative)
    _require(not parsed.is_absolute() and parsed.as_posix() == relative, 'Unsafe override path')
    if config_only:
        _require(len(parsed.parts) >= 2 and parsed.parts[0] == 'config' and parsed.suffix.lower() == '.toml',
                 'Overrides may target only config/*.toml files')
    path = pack.joinpath(*parsed.parts)
    for current in [path, *path.parents]:
        try:
            info = current.lstat()
        except FileNotFoundError:
            info = None
        if info is not None:
            _require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400),
                     'Override path passes through a link')
        if current == pack:
            break
    return path


def _read(path, maximum):
    _require(path.is_file(), 'Override input file is missing: ' + path.name)
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    _require(len(raw) <= maximum, 'Override input exceeds the byte limit: ' + path.name)
    return raw


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'Duplicate JSON policy field: ' + key)
        result[key] = value
    return result


def _rules(pack, require_policy=False):
    policy = _safe_path(pack, POLICY_PATH)
    if not policy.exists():
        _require(not require_policy, 'The config override policy is required')
        return []
    try:
        document = json.loads(_read(policy, MAX_POLICY_BYTES).decode('utf-8-sig'), object_pairs_hook=_unique_json)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid config override policy') from error
    _require(isinstance(document, dict) and set(document) == {'schemaVersion', 'overrides'}
             and type(document['schemaVersion']) is int and document['schemaVersion'] == 1,
             'Unsupported config override policy schema')
    rules = document['overrides']
    _require(isinstance(rules, list) and len(rules) <= MAX_OVERRIDES, 'Invalid config override count')
    _require(not require_policy or len(rules) > 0, 'The config override policy must be nonempty')
    seen = set()
    paths = {}
    for rule in rules:
        _require(isinstance(rule, dict) and set(rule) == {'path', 'table', 'key', 'value', 'reason'},
                 'Invalid config override rule fields')
        _safe_path(pack, rule['path'], config_only=True)
        _validate_target(rule['table'], rule['key'], rule['value'])
        _require(isinstance(rule['reason'], str) and 0 < len(rule['reason'].strip()) <= 1000,
                 'Override reason is required')
        folded_path = rule['path'].casefold()
        _require(paths.setdefault(folded_path, rule['path']) == rule['path'], 'Ambiguous config path casing')
        identity = (folded_path, tuple(rule['table']), rule['key'])
        _require(identity not in seen, 'Duplicate config override target')
        seen.add(identity)
    return rules


def project_configs(pack: Path) -> dict[str, bytes]:
    """Return publication-only config bytes; absence of a policy is a no-op."""
    pack = Path(pack).absolute()
    projected = {}
    for rule in _rules(pack):
        relative = rule['path']
        raw = projected.get(relative)
        if raw is None:
            raw = _read(_safe_path(pack, relative, config_only=True), MAX_CONFIG_BYTES)
        projected[relative] = transform_toml_boolean(raw, rule['table'], rule['key'], rule['value'])
    return projected


def _fingerprint(raw):
    return {'sizeBytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def projection_report(pack: Path, projected=None) -> list[dict]:
    """Describe pinned source and delivery bytes for each declared key override."""
    pack = Path(pack).absolute()
    expected = project_configs(pack)
    if projected is None:
        projected = expected
    _require(isinstance(projected, dict) and projected == expected, 'Projected configs differ from the declared policy')
    report = []
    for rule in _rules(pack):
        source = _read(_safe_path(pack, rule['path'], config_only=True), MAX_CONFIG_BYTES)
        report.append({**rule, 'source': _fingerprint(source), 'delivery': _fingerprint(projected[rule['path']])})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', required=True, type=Path)
    parser.add_argument('--check', action='store_true', required=True,
                        help='verify a present, nonempty publication policy without modifying source files')
    args = parser.parse_args(argv)
    try:
        _rules(args.pack.absolute(), require_policy=True)
        report = projection_report(args.pack)
    except (OSError, ValueError) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps({'status': 'PASS', 'nonMutating': True, 'overrides': report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
