"""Verify/restore only the reviewed pack-owned NeoForge waypoint startup script.

This runs during source preparation and publication, never inside the launcher.
It validates exact source/template/native/proof pins and makes no runtime claim.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re

RECEIPT = 'tools/journeymap-waypoints-policy.json'
SCRIPT = 'kubejs/startup_scripts/lanmc_journeymap_waypoint_policy.js'
TEMPLATE = 'tools/client-policy/scripts/lanmc_journeymap_waypoint_policy.js'
PROOF = 'tools/qa/journeymap-native-1211.json'
NATIVE_IDS = {'journeymap', 'kubejs', 'rhino'}
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
            'Unsafe waypoint source path')
    path = root
    for part in relative.split('/'):
        path /= part
        require(not path.is_symlink() and not (hasattr(path, 'is_junction') and path.is_junction()),
                'Linked waypoint source path refused')
    require(path.resolve().is_relative_to(root.resolve()), 'Waypoint source path escaped root')
    return path


def load(root, relative):
    path = target(root, relative)
    require(path.is_file(), 'Required waypoint source file is absent: ' + relative)
    return json.loads(path.read_text(encoding='utf-8-sig'))


def pinned(root, row, label):
    require(isinstance(row, dict) and type(row.get('sizeBytes')) is int and row['sizeBytes'] > 0
            and isinstance(row.get('sha256'), str) and SHA256.fullmatch(row['sha256']),
            'Invalid waypoint pin: ' + label)
    path = target(root, row.get('path'))
    require(path.is_file(), label + ' changed or is missing')
    raw = path.read_bytes()
    require(len(raw) == row['sizeBytes'] and digest(raw) == row['sha256'],
            label + ' changed or is missing')
    return raw


def inspect(pack, allow_missing_script=False):
    root = Path(pack).absolute()
    require(root.is_dir(), 'Waypoint pack root is unavailable')
    require(not root.is_symlink() and not (hasattr(root, 'is_junction') and root.is_junction()),
            'Linked waypoint pack root refused')
    receipt = load(root, RECEIPT)
    require(receipt.get('schemaVersion') == 1
            and receipt.get('kind') == 'pack-owned-waypoint-generation-association',
            'Unsupported waypoint source receipt')
    platform = receipt.get('platform', {})
    require(platform.get('minecraftVersion') == '1.21.1'
            and isinstance(platform.get('loader'), dict)
            and platform['loader'].get('type') == 'neoforge'
            and isinstance(platform['loader'].get('version'), str)
            and re.fullmatch(r'21\.1\.[0-9]+', platform['loader']['version']),
            'Unreviewed waypoint platform')
    descriptor = load(root, 'portable-pack.json')
    require(platform == {'minecraftVersion': descriptor.get('minecraftVersion'),
                         'loader': descriptor.get('loader')}, 'Waypoint platform changed')
    script = receipt.get('script', {})
    require(script.get('path') == SCRIPT and script.get('template') == TEMPLATE,
            'Unexpected owned waypoint script/template path')
    owned_path = target(root, 'launcher/pack-owned.txt')
    require(owned_path.is_file(), 'Waypoint script ownership is absent')
    owned = {line.split('#', 1)[0].strip() for line in owned_path.read_text(encoding='utf-8-sig').splitlines()}
    require(SCRIPT in owned, 'Exact waypoint script ownership is required')
    template = pinned(root, {**script, 'path': TEMPLATE}, 'Waypoint template')
    rows = receipt.get('nativeModules', [])
    require(isinstance(rows, list) and len(rows) == len(NATIVE_IDS)
            and all(isinstance(row, dict) for row in rows)
            and {row.get('modId') for row in rows} == NATIVE_IDS,
            'Waypoint native module identities changed')
    for row in rows:
        require(isinstance(row.get('path'), str) and len(row['path'].split('/')) == 2
                and row['path'].startswith('mods/') and row['path'].endswith('.jar'),
                'Unexpected waypoint native JAR path')
        pinned(root, row, 'Waypoint native module ' + row['modId'])
    require(receipt.get('nativePublicApiProof') == PROOF
            and isinstance(receipt.get('nativePublicApiProofSha256'), str)
            and SHA256.fullmatch(receipt['nativePublicApiProofSha256']),
            'Native waypoint API proof pin is absent')
    proof_path = target(root, PROOF)
    require(proof_path.is_file() and digest(proof_path.read_bytes()) == receipt['nativePublicApiProofSha256'],
            'Native waypoint API proof changed or is missing')
    proof = load(root, PROOF)
    require(proof.get('status') == 'PASS_EXACT_SOURCE_JAR_AND_NATIVE_PUBLIC_API_ONLY',
            'Native waypoint API proof did not pass')
    profiles = [row for row in proof.get('profiles', []) if isinstance(row, dict)
                and row.get('minecraftVersion') == platform['minecraftVersion']
                and row.get('loader') == platform['loader']]
    require(len(profiles) == 1, 'Native waypoint API profile changed or is missing')
    modules = profiles[0].get('modules', {})
    for row in rows:
        native = modules.get(row['modId'], {})
        require(native.get('fileName') == row['path'].split('/')[-1]
                and native.get('sizeBytes') == row['sizeBytes']
                and native.get('sha256') == row['sha256'],
                'Native waypoint API profile module changed: ' + row['modId'])
    active = target(root, SCRIPT)
    missing = not active.exists()
    if missing:
        require(allow_missing_script, 'Owned waypoint script is absent; use --apply after source preparation')
    else:
        require(active.is_file() and active.read_bytes() == template,
                'Conflicting owned waypoint script; unknown bytes are preserved')
    return root, template, missing, platform


def prepare(pack, apply=False):
    root, template, missing, platform = inspect(pack, allow_missing_script=apply)
    restored = 0
    if missing:
        active = target(root, SCRIPT)
        active.parent.mkdir(parents=True, exist_ok=True)
        active = target(root, SCRIPT)
        # Exclusive create also refuses a file written after the read-only plan.
        with os.fdopen(os.open(active, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
            output.write(template)
        require(target(root, SCRIPT).read_bytes() == template, 'Restored waypoint script readback differs')
        restored = 1
    return {'status': 'PASS_SOURCE_ONLY', 'kind': 'pack-owned-waypoint-generation-association',
            'platform': platform, 'restoredOwnedScripts': restored, 'nativeModules': len(NATIVE_IDS),
            'runtimeOrWorldAcceptanceClaimed': False, 'personalFilesTouched': False}


def verify(pack):
    return prepare(pack, apply=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('prepare', 'verify'), default='prepare')
    parser.add_argument('--root', '--pack', dest='root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true', help='Restore only a missing exact owned startup script')
    args = parser.parse_args()
    if args.command == 'verify' and args.apply:
        parser.error('verify is read-only; --apply requires prepare')
    print(json.dumps(prepare(args.root, apply=args.apply), ensure_ascii=False, indent=2))
