"""Restore and verify exact pack-owned host compatibility scripts in sources."""
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path

from hand_depth_policy import (digest, load, pin, pinned, read, require,
                               target, _append, _replace_checked)

RECEIPT = 'tools/host-compatibility-policy.json'
OWNED = 'launcher/pack-owned.txt'
LOCK = 'tools/upstream-lock.json'
SCRIPTS = ('lanmc_journeymap_player_markers.js', 'lanmc_lss_config_migration.js')
NATIVE_IDS = {'journeymap', 'lss', 'kubejs', 'rhino'}


def inspect(pack, repair=False):
    root = Path(pack).absolute()
    require(not (root / 'saves').exists() and not (root / 'logs/latest.log').exists(),
            'Host compatibility preparation requires source files, not a personal instance')
    receipt = load(root, RECEIPT)
    require(receipt.get('schemaVersion') == 1, 'Unsupported host compatibility policy')
    descriptor = load(root, 'portable-pack.json')
    require(receipt.get('platform') == {
        'minecraftVersion': descriptor['minecraftVersion'], 'loader': descriptor['loader']}
        == {'minecraftVersion': '1.21.1', 'loader': {'type': 'neoforge', 'version': '21.1.248'}},
        'Host compatibility platform changed')
    modules = receipt.get('nativeModules', [])
    require(len(modules) == len(NATIVE_IDS)
            and {row.get('modId') for row in modules} == NATIVE_IDS,
            'Host compatibility native module pins are incomplete')
    for row in modules:
        require(row['path'].startswith('mods/'), 'Native pin must identify a mod')
        pinned(root, row, row['modId'])
    scripts = receipt.get('scripts', [])
    require([row.get('path') for row in scripts] ==
            ['kubejs/startup_scripts/' + name for name in SCRIPTS],
            'Host compatibility script set changed')
    originals = {OWNED: read(root, OWNED), LOCK: read(root, LOCK)}
    desired = dict(originals)
    ownership = [line.strip() for line in originals[OWNED].decode('utf-8-sig').splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
    lock = json.loads(originals[LOCK].decode('utf-8-sig'))
    for row, name in zip(scripts, SCRIPTS):
        relative = row['path']
        template = 'tools/client-policy/scripts/' + name
        require(row.get('template') == template, 'Host compatibility template changed')
        raw = read(root, template)
        require(len(raw) == row['sizeBytes'] and digest(raw) == row['sha256'],
                'Host compatibility template differs from reviewed bytes')
        originals[relative] = read(root, relative, optional=True)
        require(originals[relative] == raw or (repair and originals[relative] is None),
                'Unknown host compatibility runtime bytes preserved: ' + relative)
        desired[relative] = raw
        require(not any(line != relative and fnmatch.fnmatchcase(relative, line) for line in ownership),
                'Broad host compatibility ownership refused')
        require(ownership.count(relative) <= 1, 'Duplicate host compatibility ownership')
        if relative not in ownership:
            desired[OWNED] = _append(desired[OWNED], relative)
    for relative in desired:
        if relative == LOCK:
            continue
        rows = [row for row in lock['supportFiles'] if row.get('path') == relative]
        require(len(rows) <= 1, 'Duplicate host compatibility support pin')
        expected = pin(desired[relative], relative)
        if not repair:
            require(len(rows) == 1 and all(rows[0].get(key) == value for key, value in expected.items()),
                    'Host compatibility support pin differs: ' + relative)
        elif rows:
            rows[0].update(expected)
        else:
            lock['supportFiles'].append({**expected, 'kind': 'pack-owned-startup-script',
                'reason': 'Native JourneyMap player tracking and LSS author-config compatibility.'})
    if lock != json.loads(originals[LOCK].decode('utf-8-sig')):
        desired[LOCK] = (json.dumps(lock, ensure_ascii=False, indent=2) + '\n').encode()
    return root, originals, desired


def prepare(pack, apply=False):
    root, originals, desired = inspect(pack, repair=apply)
    changed = [relative for relative in desired if desired[relative] != originals[relative]]
    require(apply or not changed, 'Host compatibility source repair requires prepare --apply')
    written = []
    try:
        for relative in changed:
            _replace_checked(target(root, relative), originals[relative], desired[relative])
            written.append(relative)
        inspect(root)
    except Exception:
        for relative in reversed(written):
            path = target(root, relative)
            if path.read_bytes() == desired[relative]:
                if originals[relative] is None:
                    path.unlink()
                else:
                    _replace_checked(path, desired[relative], originals[relative])
        raise
    return {'status': 'PASS_SOURCE_ONLY', 'changedPaths': changed,
            'runtimeAcceptanceClaimed': False}


def verify(pack):
    return prepare(pack)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'verify'))
    parser.add_argument('--pack', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and args.command != 'prepare':
        parser.error('verify is read-only')
    print(json.dumps(prepare(args.pack, args.apply), indent=2))
