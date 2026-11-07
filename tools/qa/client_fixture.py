"""Prepare and launch an isolated TFR client with an existing verified runtime.

This is an opt-in QA tool. It never reads personal instances or worlds. Java,
libraries and assets are shared read-only inputs. Pack files, native extraction,
home, temporary files and worlds are independent copies under the marked run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOTS = ('mods', 'config', 'kubejs', 'scripts', 'defaultconfigs', 'data',
         'resourcepacks', 'shaderpacks', 'configureddefaults', 'launcher',
         'ambience_music', 'global_packs', 'journeymap')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path, algorithm='sha256'):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def reject_links(path):
    for item in (path, *path.parents):
        if item.exists():
            require(not item.is_symlink() and not (getattr(item.lstat(), 'st_file_attributes', 0) & 0x400),
                    'Linked input refused: ' + str(item))


def allowed(rules):
    if not rules:
        return True
    result = False
    for rule in rules:
        operating_system = rule.get('os', {})
        matches = operating_system.get('name', 'windows') == 'windows'
        matches = matches and operating_system.get('arch', 'x86_64') in ('x86_64', 'amd64')
        matches = matches and not rule.get('features')
        if matches:
            result = rule['action'] == 'allow'
    return result


def arguments(items, values):
    result = []
    for item in items:
        if isinstance(item, dict):
            if not allowed(item.get('rules')):
                continue
            item = item['value']
        for token in item if isinstance(item, list) else [item]:
            for key, value in values.items():
                token = token.replace('${' + key + '}', str(value))
            require('${' not in token, 'Unresolved launch token: ' + token)
            result.append(token)
    return result


def runtime_inputs(cache, run):
    shared = cache / 'Minecraft/Launcher/Shared'
    profiles = [shared / 'versions' / name / (name + '.json')
                for name in ('1.21.1', 'neoforge-21.1.248')]
    base, loader = [read_json(path) for path in profiles]
    require(loader['inheritsFrom'] == '1.21.1' and loader['id'] == 'neoforge-21.1.248', 'Unexpected loader')
    require(base['id'] == '1.21.1' and base['javaVersion']['majorVersion'] == 21, 'Unexpected Minecraft')
    java_root = cache / 'Minecraft/Launcher/JavaRuntimes/runtime/windows-x64/java-21'
    receipt = read_json(java_root / '.portable-java.json')
    require(receipt['RuntimeId'] == 'temurin-21.0.12.1+1'
            and receipt['ArchiveSha256'] == 'f9d6e191ab098c0d416e7d588a24420a8621cd2f4720dab2459b8b7b2d2d8b4e',
            'Unexpected Java receipt')
    release = (java_root / 'release').read_text(encoding='utf-8')
    require('JAVA_VERSION="21.0.12.1"' in release and 'IMPLEMENTOR="Eclipse Adoptium"' in release,
            'Unexpected Java release')
    libraries = {}
    for profile in (base, loader):
        for library in profile['libraries']:
            if allowed(library.get('rules')):
                coordinate = library['name'].split(':')
                identity = tuple(coordinate[:2] + coordinate[3:])
                libraries[identity] = library['downloads']['artifact']
    classpath = []
    verified = []
    for artifact in libraries.values():
        path = shared / 'libraries' / artifact['path']
        reject_links(path)
        require(path.is_file() and path.stat().st_size == artifact['size']
                and sha(path, 'sha1') == artifact['sha1'], 'Library mismatch: ' + str(path))
        classpath.append(str(path))
        verified.append({'path': str(path), 'sha256': sha(path)})
    index = shared / 'assets/indexes' / (base['assetIndex']['id'] + '.json')
    require(sha(index, 'sha1') == base['assetIndex']['sha1'], 'Asset index mismatch')
    # Include the profile JAR in exactly the position expected by the loader.
    # NeoForge uses installed patched artifacts in libraryDirectory; this path
    # can be absent, matching CmlLib's normal generated classpath.
    classpath.append(str(shared / 'versions/neoforge-21.1.248/neoforge-21.1.248.jar'))
    native_source = shared / 'versions/neoforge-21.1.248/natives'
    require(native_source.is_dir(), 'Prepared native libraries missing')
    natives = run / 'natives'
    if not natives.exists():
        shutil.copytree(native_source, natives)
    values = {'version_name': loader['id'], 'library_directory': shared / 'libraries',
              'classpath_separator': ';', 'classpath': ';'.join(classpath),
              'natives_directory': natives, 'launcher_name': 'TFR-independent-QA', 'launcher_version': '1',
              'auth_player_name': 'TfrQaHost', 'game_directory': run / 'game',
              'assets_root': shared / 'assets', 'assets_index_name': base['assetIndex']['id'],
              'auth_uuid': 'b3963f9e58ba4215be9a11b069c46285', 'auth_access_token': '0',
              'clientid': '0', 'auth_xuid': '0', 'user_type': 'legacy', 'version_type': 'release'}
    args = arguments(base['arguments']['jvm'], values) + arguments(loader['arguments']['jvm'], values)
    args += ['-Xmx6G', '-Xms6G', '-Dfile.encoding=UTF-8',
             '-Duser.home=' + str(run / 'home'), '-Djava.io.tmpdir=' + str(run / 'temp')]
    pack_args = (run / 'game/launcher/jvm-args.txt').read_text(encoding='utf-8-sig')
    args += [line.strip() for line in pack_args.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    args += [loader['mainClass']]
    args += arguments(base['arguments']['game'], values) + arguments(loader['arguments']['game'], values)
    return java_root / 'bin/java.exe', args, {'profiles': [{'path': str(p), 'sha256': sha(p)} for p in profiles],
                                             'javaReceipt': receipt, 'libraries': verified, 'assetIndexSha256': sha(index)}


def prepare(source, cache, run):
    require(not run.exists(), 'Use a new run directory')
    require('artifacts/agent-runs' in run.as_posix(), 'Run must be inside artifacts/agent-runs')
    reject_links(source)
    reject_links(cache)
    require(not (source / 'logs/latest.log').exists() and not (source / 'saves').exists(), 'Source is a personal instance')
    descriptor = read_json(source / 'portable-pack.json')
    require(descriptor['minecraftVersion'] == '1.21.1'
            and descriptor['loader'] == {'type': 'neoforge', 'version': '21.1.248'}, 'Unexpected pack runtime')
    run.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    write_json(run / 'RUN.json', {'owner': 'Codex / root TFR host fixes',
               'purpose': 'Independent source-copy client acceptance for host marker and latency fixes',
               'createdAtUtc': now.isoformat(), 'deleteAfterUtc': (now + timedelta(hours=24)).isoformat(),
               'expectedSizeGiB': 5, 'requiredResults': ['runtime-inputs.json', 'source-copy.json', 'game logs', 'acceptance report']})
    game = run / 'game'
    game.mkdir()
    copied = []
    for root_name in ROOTS:
        origin = source / root_name
        if not origin.is_dir():
            continue
        for path in sorted(origin.rglob('*')):
            reject_links(path)
            if path.is_file():
                relative = path.relative_to(source)
                target = game / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                require(sha(target) == sha(path), 'Copy mismatch')
                copied.append({'path': relative.as_posix(), 'sha256': sha(target)})
    # Apply the same author-preserving projection used by published assets.
    sys.path.insert(0, str(source / 'tools'))
    from config_overrides import project_configs
    projected = project_configs(source)
    for relative, raw in projected.items():
        (game / relative).write_bytes(raw)
    options = game / 'configureddefaults/options.txt'
    if options.exists():
        shutil.copy2(options, game / 'options.txt')
    for name in ('temp', 'home'):
        (run / name).mkdir()
    write_json(run / 'source-copy.json', {'source': str(source), 'files': copied,
               'personalWorldsCopied': False, 'personalSettingsCopied': False,
               'projectedConfigs': list(projected)})
    java, args, proof = runtime_inputs(cache, run)
    write_json(run / 'runtime-inputs.json', proof)
    write_json(run / 'launch.json', {'java': str(java), 'args': args, 'workingDirectory': str(game)})
    print('PREPARED ' + str(run))


def launch(run):
    require((run / 'RUN.json').is_file(), 'Not a marked run')
    plan = read_json(run / 'launch.json')
    require(Path(plan['workingDirectory']).resolve() == (run / 'game').resolve(), 'Unexpected working directory')
    # Java argument files avoid the Win32 command-length limit. Every token
    # is quoted with Java's backslash escaping, not shell interpolation.
    argfile = run / 'client.args'
    argfile.write_text('\n'.join('"' + item.replace('\\', '\\\\').replace('"', '\\"') + '"'
                                 for item in plan['args']) + '\n', encoding='utf-8')
    with (run / 'console.log').open('wb') as output:
        process = subprocess.Popen([plan['java'], '@' + str(argfile)], cwd=plan['workingDirectory'],
                                   stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    write_json(run / 'process.json', {'pid': process.pid, 'java': plan['java'], 'startedAtUtc': datetime.now(timezone.utc).isoformat()})
    print('STARTED pid=' + str(process.pid))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'launch'])
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--cache-root', type=Path)
    options = parser.parse_args()
    run = options.run.absolute()
    reject_links(run)
    if options.command == 'prepare':
        require(options.source is not None and options.cache_root is not None, 'prepare needs --source and --cache-root')
        prepare(options.source.absolute(), options.cache_root.absolute(), run)
    else:
        launch(run)


if __name__ == '__main__':
    main()
