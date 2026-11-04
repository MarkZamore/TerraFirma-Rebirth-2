"""Prepare/verify the reviewed Intel first-person depth layer in pack SOURCES.

Publication must call verify(pack) unconditionally, before collecting assets.
Neither verification nor publication repairs files or accepts new upstream pins.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

RECEIPT = 'tools/hand-depth-policy.json'
PROFILE = 'tools/hand-depth/native-profile.json'
TEMPLATE = 'tools/client-policy/scripts/lanmc_intel_hand_depth.js'
SCRIPT = 'kubejs/startup_scripts/lanmc_intel_hand_depth.js'
OWNED = 'launcher/pack-owned.txt'
LOCK = 'tools/upstream-lock.json'
KIND = 'pack-owned-intel-first-person-depth-clear'
NATIVE_IDS = {'sable', 'kubejs', 'rhino'}
SHA256 = re.compile(r'^[0-9a-f]{64}$')


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def target(root, relative):
    require(isinstance(relative, str) and relative and '\\' not in relative
            and ':' not in relative and '\0' not in relative
            and all(part not in {'', '.', '..'} for part in relative.split('/')),
            'Unsafe hand-depth source path')
    root = Path(root).absolute()
    result = root.joinpath(*relative.split('/'))
    for path in [result, *result.parents]:
        if path.exists() or path.is_symlink():
            attributes = path.lstat()
            require(not path.is_symlink()
                    and not (getattr(attributes, 'st_file_attributes', 0)
                             & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)),
                    'Linked hand-depth source path refused')
    require(result.resolve().is_relative_to(root.resolve()), 'Hand-depth source escaped root')
    return result


def read(root, relative, optional=False):
    path = target(root, relative)
    if optional and not path.exists():
        return None
    require(path.is_file(), 'Required hand-depth source file is absent: ' + relative)
    return path.read_bytes()


def load(root, relative):
    try:
        return json.loads(read(root, relative).decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid hand-depth JSON: ' + relative) from error


def valid_pin(row, label, require_path=True):
    require(isinstance(row, dict) and type(row.get('sizeBytes')) is int and row['sizeBytes'] > 0
            and isinstance(row.get('sha256'), str) and SHA256.fullmatch(row['sha256'])
            and (not require_path or isinstance(row.get('path'), str)),
            'Invalid hand-depth pin: ' + label)


def pin(raw, path):
    return {'path': path, 'sizeBytes': len(raw), 'sha256': digest(raw)}


def pinned(root, row, label):
    valid_pin(row, label)
    raw = read(root, row['path'])
    require(len(raw) == row['sizeBytes'] and digest(raw) == row['sha256'],
            'Hand-depth pinned bytes changed: ' + label)
    return raw


def _text_lines(raw):
    try:
        return [line.split('#', 1)[0].strip() for line in raw.decode('utf-8-sig').splitlines()]
    except UnicodeDecodeError as error:
        raise ValueError('Hand-depth ownership must be UTF-8') from error


def _append(raw, line):
    newline = b'\r\n' if b'\r\n' in raw else b'\n'
    return raw + (b'' if not raw or raw.endswith((b'\r', b'\n')) else newline) + line.encode() + newline


def inspect(pack, allow_repair=False):
    root = Path(pack).absolute()
    require(root.is_dir(), 'Hand-depth source pack is unavailable')
    target(root, RECEIPT)  # Also validates the root and all ancestor reparse points.
    require(not target(root, 'saves').exists() and not target(root, 'logs/latest.log').exists(),
            'Hand-depth preparation/verification is source-only; personal/runtime instance refused')
    receipt = load(root, RECEIPT)
    require(receipt.get('schemaVersion') == 1 and receipt.get('kind') == KIND,
            'Unsupported hand-depth policy receipt')
    require(receipt.get('sourceOnlyVerifier') == 'tools/hand_depth_policy.py',
            'Hand-depth verifier identity changed')
    platform = receipt.get('platform')
    require(isinstance(platform, dict) and platform.get('minecraftVersion') == '1.21.1'
            and isinstance(platform.get('loader'), dict)
            and platform['loader'].get('type') == 'neoforge'
            and isinstance(platform['loader'].get('version'), str)
            and re.fullmatch(r'21\.1\.[0-9]+', platform['loader']['version']),
            'Unreviewed hand-depth platform')
    descriptor = load(root, 'portable-pack.json')
    lock = load(root, LOCK)
    require(platform == {'minecraftVersion': descriptor.get('minecraftVersion'),
                         'loader': descriptor.get('loader')} == lock.get('platform'),
            'Hand-depth platform changed; review native API and GPU behavior before updating pins')
    script = receipt.get('script', {})
    require(script.get('path') == SCRIPT and script.get('template') == TEMPLATE,
            'Unexpected hand-depth script/template identity')
    template = pinned(root, {**script, 'path': TEMPLATE}, 'script template')
    rows = receipt.get('nativeModules')
    require(isinstance(rows, list) and len(rows) == len(NATIVE_IDS)
            and all(isinstance(row, dict) for row in rows)
            and {row.get('modId') for row in rows} == NATIVE_IDS,
            'Hand-depth native module identities changed')
    for row in rows:
        require(isinstance(row.get('path'), str) and len(row['path'].split('/')) == 2
                and row['path'].startswith('mods/') and row['path'].endswith('.jar'),
                'Unexpected hand-depth native JAR path')
        pinned(root, row, 'native module ' + row['modId'])
    embedded = receipt.get('bundledVeil', {})
    valid_pin(embedded, 'embedded Veil', require_path=False)
    require(embedded.get('modId') == 'veil' and embedded.get('parentModId') == 'sable'
            and isinstance(embedded.get('entry'), str)
            and embedded['entry'].startswith('META-INF/jarjar/')
            and len(embedded['entry'].split('/')) == 3 and embedded['entry'].endswith('.jar'),
            'Unexpected embedded Veil identity')
    parent = next(row for row in rows if row['modId'] == 'sable')
    try:
        with zipfile.ZipFile(target(root, parent['path'])) as archive:
            require(archive.namelist().count(embedded['entry']) == 1,
                    'Embedded Veil entry missing or duplicated')
            raw = archive.read(embedded['entry'])
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError('Embedded Veil archive changed') from error
    require(len(raw) == embedded['sizeBytes'] and digest(raw) == embedded['sha256'],
            'Embedded Veil bytes changed')
    profile_pin = receipt.get('nativeProfile', {})
    require(profile_pin.get('path') == PROFILE, 'Hand-depth native profile path changed')
    pinned(root, profile_pin, 'native API profile')
    profile = load(root, PROFILE)
    require(profile.get('schemaVersion') == 1 and profile.get('kind') == 'exact-source-api-identity'
            and profile.get('runtimeAcceptanceClaimed') is False
            and profile.get('platform') == platform
            and profile.get('nativeModules') == rows and profile.get('bundledVeil') == embedded,
            'Hand-depth native profile differs; new pins require a separately reviewed profile')
    require(isinstance(profile.get('publicApiSites'), list) and profile['publicApiSites'],
            'Hand-depth native API sites are absent')
    previous = receipt.get('previousOwnedScripts', [])
    require(isinstance(previous, list), 'Invalid previous hand-depth script identities')
    for old in previous:
        valid_pin(old, 'previous owned script', require_path=False)
    originals = {SCRIPT: read(root, SCRIPT, optional=True), OWNED: read(root, OWNED), LOCK: read(root, LOCK)}
    active = originals[SCRIPT]
    current = active == template
    known_previous = active is not None and any(len(active) == old['sizeBytes']
                        and digest(active) == old['sha256'] for old in previous)
    require(current or (allow_repair and (active is None or known_previous)),
            'Owned hand-depth script is absent or conflicts; unknown bytes are preserved')
    owned = _text_lines(originals[OWNED])
    require(not any(line != SCRIPT and fnmatch.fnmatchcase(SCRIPT, line) for line in owned if line),
            'Broad hand-depth ownership is refused; use only the exact script path')
    require(owned.count(SCRIPT) <= 1, 'Duplicate exact hand-depth ownership')
    require(allow_repair or owned.count(SCRIPT) == 1, 'Exact hand-depth script ownership is required')
    support = lock.get('supportFiles')
    require(isinstance(support, list) and all(isinstance(row, dict) for row in support),
            'Hand-depth support lock is missing')
    for relative in (SCRIPT, OWNED):
        matches = [row for row in support if row.get('path') == relative]
        require(len(matches) <= 1, 'Duplicate hand-depth support lock record: ' + relative)
        if not allow_repair:
            require(len(matches) == 1 and matches[0].get('sizeBytes') == len(originals[relative])
                    and matches[0].get('sha256') == digest(originals[relative]),
                    'Hand-depth support lock differs: ' + relative)
    return root, receipt, template, originals, lock, owned


def _replace_checked(path, expected, desired):
    current = path.read_bytes() if path.exists() else None
    require(current == expected, 'Hand-depth source changed during preparation; retry after review')
    path.parent.mkdir(parents=True, exist_ok=True)
    if expected is None:
        created = None
        try:
            with path.open('xb') as stream:
                created = os.fstat(stream.fileno())
                stream.write(desired)
        except Exception:
            # Clean up only the file this call exclusively created, including
            # a partial write. A concurrently replaced file is preserved.
            if created is not None and path.exists() and path.stat().st_ino == created.st_ino:
                path.unlink()
            raise
        return
    handle, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(desired)
        require(path.read_bytes() == expected, 'Hand-depth metadata changed during preparation')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(pack, apply=False):
    root, receipt, template, originals, lock, owned = inspect(pack, allow_repair=apply)
    desired = {SCRIPT: template, OWNED: originals[OWNED], LOCK: originals[LOCK]}
    if SCRIPT not in owned:
        desired[OWNED] = _append(originals[OWNED], SCRIPT)
    for relative in (SCRIPT, OWNED):
        row = next((row for row in lock['supportFiles'] if row.get('path') == relative), None)
        if row is None:
            row = {'path': relative, 'kind': 'pack-owned-startup-script' if relative == SCRIPT else 'pack-owned-support-data',
                   'reason': 'Intel first-person depth compatibility; original author JARs and mechanics retained.'}
            lock['supportFiles'].append(row)
        row.update(pin(desired[relative], relative))
    if lock != json.loads(originals[LOCK].decode('utf-8-sig')):
        desired[LOCK] = (json.dumps(lock, ensure_ascii=False, indent=2) + '\n').encode()
    changes = [relative for relative in desired if desired[relative] != originals[relative]]
    require(apply or not changes, 'Hand-depth sources need prepare --apply; verification is read-only')
    written = []
    try:
        for relative in changes:
            _replace_checked(target(root, relative), originals[relative], desired[relative])
            written.append(relative)
        # Verification runs again against the resulting complete source plan.
        inspect(root)
    except Exception:
        for relative in reversed(written):
            path = target(root, relative)
            if path.exists() and path.read_bytes() == desired[relative]:
                if originals[relative] is None:
                    path.unlink()
                else:
                    _replace_checked(path, desired[relative], originals[relative])
        raise
    return {'status': 'PASS_SOURCE_ONLY', 'kind': KIND, 'platform': receipt['platform'],
            'changedPaths': changes, 'nativeModules': len(NATIVE_IDS), 'embeddedVeilVerified': True,
            'runtimeOrWorldAcceptanceClaimed': False, 'personalFilesTouched': False}


def verify(pack):
    return prepare(pack, apply=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'verify'))
    parser.add_argument('--pack', '--root', dest='pack', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true', help='Restore only known owned source data and their exact support pins')
    args = parser.parse_args()
    if args.command == 'verify' and args.apply:
        parser.error('verify is read-only; --apply requires prepare')
    print(json.dumps(prepare(args.pack, apply=args.apply), ensure_ascii=False, indent=2))
