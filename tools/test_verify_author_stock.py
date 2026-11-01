"""Publication guard regressions using tiny independent byte fixtures, never installed games."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from verify_author_stock import verify


class AuthorFidelityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = []
        for path, data in [('mods/author.jar', b'author-jar'), ('config/author.toml', b'author=true\r\n'),
                           ('options.txt', b'key_jump:key.keyboard.space\n')]:
            self.put(path, data)
            row = self.pin(path)
            row['origin'] = 'author-curseforge-manifest' if path.startswith('mods/') else 'author-archive-overrides'
            if path.startswith('mods/'):
                row.update(projectId=12, fileId=34)
            self.rows.append(row)
        self.put('configureddefaults/options.txt', (self.root/'options.txt').read_bytes())
        self.platform = {'minecraftVersion': '1.21.1', 'loader': {'type': 'neoforge', 'version': '21.1.248'}}
        self.write_json('portable-pack.json', self.platform)
        self.manifest = {'version': '1.0-author', 'minecraft': {'version': '1.21.1',
            'modLoaders': [{'id': 'neoforge-21.1.248', 'primary': True}]},
            'files': [{'projectID': 12, 'fileID': 34, 'required': True}]}
        self.write_json('tools/author-source/manifest.json', self.manifest)
        self.snapshot = {'kind': 'complete-unmodified-author-stock', 'officialFileId': 56,
            'archiveSha256': 'a'*64, **self.platform, 'files': self.rows}
        self.write_json('tools/author-source/stock-snapshot.json', self.snapshot)
        self.lock = {'schemaVersion': 1, 'kind': 'clean-author-curseforge-stock', 'fileId': 56,
            'authorVersion': '1.0-author', 'platform': self.platform, 'archive': {'sha256': 'a'*64},
            'stockSnapshot': self.pin('tools/author-source/stock-snapshot.json'),
            'originalManifest': self.pin('tools/author-source/manifest.json'),
            'authorFilesChanged': [], 'authorJarsExcluded': [],
            'supportFiles': [self.pin('configureddefaults/options.txt'), self.pin('portable-pack.json')]}
        self.write_json('tools/upstream-lock.json', self.lock)

    def put(self, path, data):
        target = self.root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def write_json(self, path, value):
        self.put(path, (json.dumps(value, sort_keys=True)+'\n').encode())

    def pin(self, path):
        data = (self.root/path).read_bytes()
        return {'path': path, 'sizeBytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def persist_snapshot(self):
        self.write_json('tools/author-source/stock-snapshot.json', self.snapshot)
        self.lock['stockSnapshot'] = self.pin('tools/author-source/stock-snapshot.json')
        self.write_json('tools/upstream-lock.json', self.lock)

    def test_exact_author_stock_is_read_only_and_repeatable(self):
        before = {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.root.rglob('*') if p.is_file()}
        for _ in range(2):
            result = verify(self.root)
            self.assertEqual((result['authorFiles'], result['authorJars'], result['modifiedAuthorFiles']), (3, 1, 0))
            self.assertFalse(result['nativeClientOrWorldAcceptanceClaimed'])
        self.assertEqual(before, {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                                 for p in self.root.rglob('*') if p.is_file()})

    def test_changed_author_jar_or_config_is_rejected(self):
        for path in ['mods/author.jar', 'config/author.toml']:
            with self.subTest(path=path):
                previous = (self.root/path).read_bytes()
                self.put(path, b'changed')
                with self.assertRaisesRegex(ValueError, 'Author bytes differ'):
                    verify(self.root)
                self.put(path, previous)

    def test_publication_projection_is_declared_but_author_source_stays_exact(self):
        raw = b'[display]\r\ndoPopUpLogging = true\r\nother = 21\r\n'
        self.put('config/author.toml', raw)
        self.rows[1].update(self.pin('config/author.toml'))
        self.persist_snapshot()
        policy = 'tools/pack-config-overrides.json'
        self.write_json(policy, {'schemaVersion': 1, 'overrides': [{
            'path': 'config/author.toml', 'table': ['display'],
            'key': 'doPopUpLogging', 'value': False, 'reason': 'Local popup preference'}]})
        with self.assertRaisesRegex(ValueError, 'pinned policy'):
            verify(self.root)
        self.lock['publicationConfigOverrides'] = self.pin(policy)
        self.write_json('tools/upstream-lock.json', self.lock)
        result = verify(self.root)
        self.assertEqual(0, result['modifiedAuthorFiles'])
        self.assertEqual(1, len(result['publishedAuthorConfigOverrides']))
        self.assertEqual(raw, (self.root/'config/author.toml').read_bytes())
        self.put('config/author.toml', raw.replace(b'other = 21', b'other = 22'))
        with self.assertRaisesRegex(ValueError, 'Author bytes differ'):
            verify(self.root)

    def test_extra_orphan_runtime_files_are_rejected(self):
        for path in ['mods/old.jar', 'config/old.toml', 'shaderpacks/old.zip', 'launcher/old.txt']:
            with self.subTest(path=path):
                self.put(path, b'leftover')
                with self.assertRaises(ValueError):
                    verify(self.root)
                (self.root/path).unlink()

    def test_portable_platform_drift_is_rejected(self):
        self.write_json('portable-pack.json', {**self.platform, 'minecraftVersion': '1.21.2'})
        with self.assertRaisesRegex(ValueError, 'Portable platform'):
            verify(self.root)

    def test_snapshot_and_manifest_hashes_are_not_advisory(self):
        for path in ['tools/author-source/stock-snapshot.json', 'tools/author-source/manifest.json']:
            with self.subTest(path=path):
                previous = (self.root/path).read_bytes()
                self.put(path, previous+b' ')
                with self.assertRaisesRegex(ValueError, 'SHA256 changed'):
                    verify(self.root)
                self.put(path, previous)

    def test_declared_author_file_id_mismatch_is_rejected_even_with_new_snapshot_pin(self):
        self.rows[0]['fileId'] = 999
        self.persist_snapshot()
        with self.assertRaisesRegex(ValueError, 'file identities'):
            verify(self.root)

    def test_optional_pinned_source_proofs_cannot_be_tampered_or_escape_root(self):
        path = 'tools/author-source/proof.json'
        self.put(path, b'{"checked":true}\n')
        for name in ('nativeClientSourceProof', 'nativeRequiredInputsProof', 'runtimeAcceptanceReport'):
            with self.subTest(name=name):
                self.lock[name] = self.pin(path)
                self.write_json('tools/upstream-lock.json', self.lock)
                self.assertEqual(verify(self.root)['status'], 'PASS')
                self.put(path, b'{"checked":false}\n')
                with self.assertRaisesRegex(ValueError, 'Pinned source proof SHA256 changed'):
                    verify(self.root)
                self.put(path, b'{"checked":true}\n')
                self.lock[name]['path'] = '../outside-proof.json'
                self.write_json('tools/upstream-lock.json', self.lock)
                with self.assertRaisesRegex(ValueError, 'Unsafe source path'):
                    verify(self.root)
                del self.lock[name]

    def test_case_duplicate_paths_and_traversal_are_rejected(self):
        self.rows.append({**self.rows[0], 'path': 'MODS/AUTHOR.JAR'})
        self.persist_snapshot()
        with self.assertRaisesRegex(ValueError, 'Duplicate author'):
            verify(self.root)
        self.rows.pop()
        self.rows[1]['path'] = '../outside.toml'
        self.persist_snapshot()
        with self.assertRaisesRegex(ValueError, 'Unsafe source path'):
            verify(self.root)

    def test_support_bytes_and_clean_profile_claims_are_enforced(self):
        self.put('configureddefaults/options.txt', b'non-author settings')
        with self.assertRaisesRegex(ValueError, 'exact author options'):
            verify(self.root)
        self.put('configureddefaults/options.txt', (self.root/'options.txt').read_bytes())
        self.lock['authorJarsExcluded'] = ['author.jar']
        self.write_json('tools/upstream-lock.json', self.lock)
        with self.assertRaisesRegex(ValueError, 'no content changes'):
            verify(self.root)


if __name__ == '__main__':
    unittest.main()
