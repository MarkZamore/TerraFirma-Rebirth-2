"""Execute the shipped marker script with the pinned Rhino and Java collection binding."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.request import urlopen

GSON_URL = 'https://repo.maven.apache.org/maven2/com/google/code/gson/gson/2.10.1/gson-2.10.1.jar'
GSON_SIZE = 283367
GSON_SHA256 = '4241c14a7727c34feea6507ec801318a3d4a90f070e4525681079fb94ee4c593'
PACK = Path(__file__).resolve().parents[2]


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@contextmanager
def gson_path(existing):
    if existing is not None:
        yield existing
        return
    work = PACK / 'artifacts/agent-runs/player-marker-rhino'
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dependency-', dir=work) as temporary:
        root = Path(temporary)
        (root / 'RUN.json').write_text(json.dumps({
            'owner': 'TFR player-marker Rhino regression check',
            'purpose': 'Temporary exact-hash Gson test dependency, never a shipped mod',
            'expectedSizeBytes': GSON_SIZE, 'deleteAfter': 'tool exit',
            'requiredResults': ['stdout regression result']}), encoding='utf-8')
        with urlopen(GSON_URL, timeout=30) as response:
            raw = response.read(GSON_SIZE + 1)
        path = root / 'gson-2.10.1.jar'
        path.write_bytes(raw)
        yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--java', required=True, type=Path)
    parser.add_argument('--gson', type=Path, help='Reuse an exact verified runtime library without downloading')
    args = parser.parse_args()
    receipt = json.loads((PACK / 'tools/host-compatibility-policy.json').read_bytes())
    pin = next(row for row in receipt['nativeModules'] if row['modId'] == 'rhino')
    rhino = PACK / pin['path']
    if rhino.stat().st_size != pin['sizeBytes'] or sha256(rhino) != pin['sha256']:
        raise ValueError('Rhino bytes differ from the reviewed native pin')
    with gson_path(args.gson) as gson:
        if gson.stat().st_size != GSON_SIZE or sha256(gson) != GSON_SHA256:
            raise ValueError('Gson regression dependency does not match its official exact-hash pin')
        result = subprocess.run([
            str(args.java.resolve()), '--class-path', os.pathsep.join((str(rhino), str(gson.resolve()))),
            str(PACK / 'tools/qa/RhinoPlayerMarkersProbe.java'),
            str(PACK / 'kubejs/startup_scripts/lanmc_journeymap_player_markers.js')],
            check=True, capture_output=True, text=True, timeout=60)
    print(result.stdout.strip())


if __name__ == '__main__':
    main()
