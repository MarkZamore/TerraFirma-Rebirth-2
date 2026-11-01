"""Exercise the full release-verification CLI used by the publish workflow."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import contextlib
import io
import tomllib
import zipfile

from generate_manifest import generate, verify_only


class ReleaseVerificationTests(unittest.TestCase):
    def test_author_updates_keep_pack_local_popup_policy_without_changing_source_or_other_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / 'source'
            out = Path(temporary) / 'dist'
            relative = 'config/euphoria_patcher/settings.toml'
            config = pack / relative
            config.parent.mkdir(parents=True)
            (pack / 'client.jar').write_bytes(b'unchanged client fixture')
            (pack / 'portable-pack.json').write_text(json.dumps({
                'minecraftVersion': '1.21.1', 'clientJar': 'client.jar',
                'loader': {'type': 'neoforge', 'version': '21.1.248'}}), encoding='utf-8')
            (pack / 'tools').mkdir()
            (pack / 'tools/pack-config-overrides.json').write_text(json.dumps({
                'schemaVersion': 1, 'overrides': [{'path': relative, 'table': ['display'],
                    'key': 'doPopUpLogging', 'value': False, 'reason': 'Local popup preference'}]}), encoding='utf-8')
            authors = [
                b'# author v1\r\n[display]\r\ndoPopUpLogging = true # visible popup\r\nother = 7\r\n',
                b'# author v2\r\n[display]\r\ndoPopUpLogging = true # new default\r\nother = 19\r\nnewOption = "keep"\r\n']
            for version, raw in enumerate(authors):
                config.write_bytes(raw)
                with contextlib.redirect_stdout(io.StringIO()):
                    generate(pack, out, 'author-' + str(version))
                    verify_only(out, verify_all=True)
                manifest = json.loads((out / 'pack-manifest.json').read_text(encoding='utf-8'))
                row = next(row for row in manifest['files'] if row['path'] == relative)
                asset = next(row for row in manifest['assets'] if relative in row['covers'])
                with zipfile.ZipFile(out / 'assets' / asset['name']) as archive:
                    delivered = archive.read(relative)
                self.assertEqual(raw.replace(b'doPopUpLogging = true', b'doPopUpLogging = false'), delivered)
                self.assertFalse(tomllib.loads(delivered.decode())['display']['doPopUpLogging'])
                self.assertEqual(raw, config.read_bytes())
                self.assertEqual(hashlib.sha256(delivered).hexdigest(), row['sha256'])
                self.assertEqual(len(delivered), row['sizeBytes'])
                report = manifest['configOverrides'][0]
                self.assertEqual(hashlib.sha256(raw).hexdigest(), report['source']['sha256'])
                self.assertEqual(row['sha256'], report['delivery']['sha256'])

    def test_verify_all_checks_assets_beyond_the_sample_without_generating(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / 'source'
            pack.mkdir()
            dist = root / 'dist'
            assets = dist / 'assets'
            assets.mkdir(parents=True)
            rows = []
            for index in range(11):
                payload = bytes([index])
                name = f'asset-{index}.bin'
                (assets / name).write_bytes(payload)
                rows.append({'name': name, 'sizeBytes': len(payload),
                             'sha256': hashlib.sha256(payload).hexdigest()})
            manifest = dist / 'pack-manifest.json'
            manifest.write_text(json.dumps({'assets': rows}), encoding='utf-8')
            original_manifest = manifest.read_bytes()
            command = [sys.executable, str(Path(__file__).with_name('generate_manifest.py')),
                       '--pack', str(pack), '--out', str(dist), '--verify-all']
            passed = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, passed.returncode, passed.stdout + passed.stderr)
            self.assertIn('verified 11 of 11 staged assets', passed.stdout)
            self.assertEqual(original_manifest, manifest.read_bytes())
            for row in rows:
                asset = assets / row['name']
                original = asset.read_bytes()
                asset.write_bytes(b'X')
                failed = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(0, failed.returncode, row['name'])
                self.assertIn('hash/size mismatch', failed.stderr)
                self.assertEqual(original_manifest, manifest.read_bytes())
                asset.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
