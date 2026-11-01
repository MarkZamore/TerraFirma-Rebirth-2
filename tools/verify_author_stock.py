#!/usr/bin/env python3
"""Verify complete unmodified TFR 1.0 author bytes and explicit portable data."""
from __future__ import annotations
import argparse
import hashlib
import json
import pathlib
from config_overrides import project_configs, projection_report


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe(root, relative):
    require(isinstance(relative, str) and relative and not relative.startswith('/')
            and ':' not in relative and '\\' not in relative
            and all(part not in {'', '.', '..'} for part in relative.split('/')), 'Unsafe source path')
    path = root.joinpath(*relative.split('/'))
    for current in [path, *path.parents]:
        if current.exists():
            require(not current.is_symlink() and not (getattr(current.lstat(), 'st_file_attributes', 0) & 0x400),
                    'Source path passes through a link')
        if current == root:
            break
    return path


def verify(root):
    root = root.absolute()
    lock = json.loads((root/'tools/upstream-lock.json').read_text(encoding='utf-8-sig'))
    require(lock['schemaVersion'] == 1 and lock['kind'] == 'clean-author-curseforge-stock', 'Unexpected author lock kind')
    require(lock.get('authorFilesChanged') == [] and lock.get('authorJarsExcluded') == [],
            'This clean author profile allows no content changes or excluded JARs')
    for name in ('nativeClientSourceProof', 'nativeRequiredInputsProof', 'runtimeAcceptanceReport',
                 'publicationConfigOverrides'):
        if name in lock:
            row = lock[name]
            path = safe(root, row['path'])
            require(path.is_file() and digest(path) == row['sha256'],
                    'Pinned source proof SHA256 changed: ' + name)
    snapshot = safe(root, lock['stockSnapshot']['path'])
    require(digest(snapshot) == lock['stockSnapshot']['sha256'], 'Author snapshot SHA256 changed')
    stock = json.loads(snapshot.read_text(encoding='utf-8-sig'))
    require(stock['kind'] == 'complete-unmodified-author-stock' and stock['officialFileId'] == lock['fileId'],
            'Author stock identity changed')
    require(stock['archiveSha256'] == lock['archive']['sha256']
            and stock['minecraftVersion'] == lock['platform']['minecraftVersion']
            and stock['loader'] == lock['platform']['loader'], 'Author snapshot provenance differs')
    manifest_path = safe(root, lock['originalManifest']['path'])
    require(digest(manifest_path) == lock['originalManifest']['sha256'], 'Original manifest SHA256 changed')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    require(manifest['version'] == lock['authorVersion']
            and manifest['minecraft']['version'] == lock['platform']['minecraftVersion']
            and manifest['minecraft']['modLoaders'] == [
                {'id': lock['platform']['loader']['type'] + '-' + lock['platform']['loader']['version'], 'primary': True}],
            'Original author manifest platform/version differs')
    rows = stock['files']
    require(len({row['path'].casefold() for row in rows}) == len(rows), 'Duplicate author source path')
    manifest_pairs = [(row['projectID'], row['fileID']) for row in manifest['files']]
    stock_pairs = [(row['projectId'], row['fileId']) for row in rows
                   if row['origin'] == 'author-curseforge-manifest']
    require(len(set(manifest_pairs)) == len(manifest_pairs)
            and len(set(stock_pairs)) == len(stock_pairs)
            and set(manifest_pairs) == set(stock_pairs), 'Author manifest file identities differ from stock')
    expected_mods = set()
    total = 0
    for row in rows:
        path = safe(root, row['path'])
        require(path.is_file() and path.stat().st_size == row['sizeBytes'] and digest(path) == row['sha256'],
                'Author bytes differ or are missing: ' + row['path'])
        total += row['sizeBytes']
        if row['path'].startswith('mods/') and row['path'].endswith('.jar'):
            expected_mods.add(row['path'])
    actual_mods = {p.relative_to(root).as_posix() for p in (root/'mods').rglob('*') if p.is_file()}
    require(actual_mods == expected_mods, 'Active mods contain excluded/missing/unknown files')
    descriptor = json.loads((root/'portable-pack.json').read_text(encoding='utf-8-sig'))
    require(descriptor['minecraftVersion'] == lock['platform']['minecraftVersion']
            and descriptor['loader'] == lock['platform']['loader'], 'Portable platform differs from author manifest')
    author_options = safe(root, 'options.txt')
    require(digest(safe(root, 'configureddefaults/options.txt')) == digest(author_options),
            'Fresh controls setup must start from exact author options bytes')
    support = lock.get('supportFiles', [])
    for row in support:
        path = safe(root, row['path'])
        require(path.is_file() and path.stat().st_size == row['sizeBytes'] and digest(path) == row['sha256'],
                'Portable support data differ: ' + row['path'])
    roots = {'mods', 'config', 'kubejs', 'scripts', 'defaultconfigs', 'data', 'resourcepacks',
             'shaderpacks', 'configureddefaults', 'launcher', 'ambience_music', 'global_packs', 'journeymap'}
    expected_runtime = {row['path'] for row in rows if row['path'].split('/')[0] in roots}
    expected_runtime.update(row['path'] for row in support if row['path'].split('/')[0] in roots)
    actual_runtime = {path.relative_to(root).as_posix() for name in roots
                      if (root/name).is_dir() for path in (root/name).rglob('*') if path.is_file()}
    require(actual_runtime == expected_runtime, 'Unknown/missing runtime files outside the exact author/support plan')
    projections = project_configs(root)
    if projections:
        require(lock.get('publicationConfigOverrides', {}).get('path') == 'tools/pack-config-overrides.json',
                'Publication config overrides need a pinned policy')
        require(set(projections).issubset({row['path'] for row in rows}),
                'Publication config override must target a recorded author file')
    return {'status': 'PASS', 'kind': 'static complete author source fidelity', 'authorVersion': lock['authorVersion'],
            'authorFiles': len(rows), 'authorBytes': total, 'authorJars': len(expected_mods),
            'modifiedAuthorFiles': 0, 'excludedAuthorJars': 0,
            'portableSupportFiles': len(support),
            'publishedAuthorConfigOverrides': projection_report(root, projections),
            'nativeClientOrWorldAcceptanceClaimed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.root), ensure_ascii=False, indent=2))
