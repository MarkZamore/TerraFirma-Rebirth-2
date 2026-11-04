"""Source/import failure tests. Synthetic fixtures do not claim GPU/game PASS."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import hand_depth_policy as policy


class HandDepthPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.platform = {'minecraftVersion': '1.21.1', 'loader': {'type': 'neoforge', 'version': '21.1.248'}}
        self.write('portable-pack.json', json.dumps({**self.platform, 'schemaVersion': 1}).encode())
        self.script = b'// SYNTHETIC SOURCE FIXTURE; no native compatibility claim\n'
        self.write(policy.TEMPLATE, self.script)
        self.write(policy.SCRIPT, self.script)
        self.ownership = b'\xef\xbb\xbf# preserve comment and bytes\r\nconfig/fml.toml\r\n' + policy.SCRIPT.encode() + b'\r\n'
        self.write(policy.OWNED, self.ownership)
        embedded_raw = b'synthetic embedded jar identity, not executable code'
        archive = BytesIO()
        with zipfile.ZipFile(archive, 'w') as jar:
            jar.writestr('META-INF/jarjar/veil.jar', embedded_raw)
        self.write('mods/sable.jar', archive.getvalue())
        self.write('mods/kubejs.jar', b'synthetic kubejs source pin')
        self.write('mods/rhino.jar', b'synthetic rhino source pin')
        rows = [dict(self.pin('mods/' + mod + '.jar'), modId=mod) for mod in sorted(policy.NATIVE_IDS)]
        embedded = dict(policy.pin(embedded_raw, 'ignored'))
        embedded.pop('path')
        embedded.update(modId='veil', parentModId='sable', entry='META-INF/jarjar/veil.jar')
        profile = {'schemaVersion': 1, 'kind': 'exact-source-api-identity', 'runtimeAcceptanceClaimed': False,
                   'platform': self.platform, 'nativeModules': rows, 'bundledVeil': embedded,
                   'publicApiSites': ['synthetic non-runtime API fixture']}
        self.dump(policy.PROFILE, profile)
        self.receipt = {'schemaVersion': 1, 'kind': policy.KIND, 'platform': self.platform,
                        'sourceOnlyVerifier': 'tools/hand_depth_policy.py',
                        'script': dict(self.pin(policy.SCRIPT), template=policy.TEMPLATE),
                        'nativeModules': rows, 'bundledVeil': embedded,
                        'nativeProfile': self.pin(policy.PROFILE), 'previousOwnedScripts': []}
        self.dump(policy.RECEIPT, self.receipt)
        self.lock = {'schemaVersion': 1, 'platform': self.platform,
                     'authorVersion': 'preserve-author-version', 'arbitraryAuthorField': {'preserve': [1, 2]},
                     'supportFiles': [dict(self.pin(policy.OWNED), arbitrary='preserve-row-field'),
                                      dict(self.pin(policy.SCRIPT), kind='pack-owned-startup-script'),
                                      {'path': 'unrelated.data', 'sha256': 'keep', 'sizeBytes': 23}]}
        self.dump(policy.LOCK, self.lock)

    def write(self, relative, raw):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def dump(self, relative, value):
        self.write(relative, (json.dumps(value, indent=2) + '\n').encode())

    def pin(self, relative):
        return policy.pin((self.root / relative).read_bytes(), relative)

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def test_verify_is_read_only_and_makes_no_runtime_claim(self):
        before = self.snapshot()
        report = policy.verify(self.root)
        self.assertEqual('PASS_SOURCE_ONLY', report['status'])
        self.assertEqual([], report['changedPaths'])
        self.assertFalse(report['runtimeOrWorldAcceptanceClaimed'])
        self.assertEqual(before, self.snapshot())

    def test_missing_runtime_requires_apply_and_restores_idempotently(self):
        (self.root / policy.SCRIPT).unlink()
        with self.assertRaisesRegex(ValueError, 'script is absent or conflicts'):
            policy.verify(self.root)
        self.assertFalse((self.root / policy.SCRIPT).exists())
        self.assertEqual([policy.SCRIPT], policy.prepare(self.root, apply=True)['changedPaths'])
        self.assertEqual(self.script, (self.root / policy.SCRIPT).read_bytes())
        self.assertEqual([], policy.prepare(self.root, apply=True)['changedPaths'])

    def test_missing_receipt_template_or_profile_blocks_repair_without_writes(self):
        for relative in (policy.RECEIPT, policy.TEMPLATE, policy.PROFILE):
            with self.subTest(relative=relative):
                original = (self.root / relative).read_bytes()
                (self.root / relative).unlink()
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    policy.prepare(self.root, apply=True)
                self.assertEqual(before, self.snapshot())
                self.write(relative, original)

    def test_unknown_runtime_collision_is_preserved_even_when_lock_accepts_it(self):
        unknown = b'// possible new author file or personal code\n'
        self.write(policy.SCRIPT, unknown)
        self.lock['supportFiles'][1].update(self.pin(policy.SCRIPT))
        self.dump(policy.LOCK, self.lock)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'unknown bytes are preserved'):
            policy.prepare(self.root, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_recognized_previous_owned_script_can_be_upgraded_only_with_apply(self):
        old = b'// reviewed previous version\n'
        self.receipt['previousOwnedScripts'] = [policy.pin(old, policy.SCRIPT)]
        self.dump(policy.RECEIPT, self.receipt)
        self.write(policy.SCRIPT, old)
        with self.assertRaises(ValueError):
            policy.verify(self.root)
        self.assertIn(policy.SCRIPT, policy.prepare(self.root, apply=True)['changedPaths'])
        self.assertEqual(self.script, (self.root / policy.SCRIPT).read_bytes())

    def test_changed_upstream_module_blocks_before_repair(self):
        (self.root / policy.SCRIPT).unlink()
        self.write('mods/kubejs.jar', b'new upstream version must be reviewed')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'native module kubejs'):
            policy.prepare(self.root, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_changing_receipt_pin_alone_cannot_accept_new_native_bytes(self):
        self.write('mods/rhino.jar', b'replaced upstream library')
        row = next(row for row in self.receipt['nativeModules'] if row['modId'] == 'rhino')
        row.update(self.pin(row['path']))
        self.dump(policy.RECEIPT, self.receipt)
        with self.assertRaisesRegex(ValueError, 'native profile differs'):
            policy.prepare(self.root, apply=True)

    def test_changed_embedded_veil_fails_even_after_parent_pin_update(self):
        archive = BytesIO()
        with zipfile.ZipFile(archive, 'w') as jar:
            jar.writestr('META-INF/jarjar/veil.jar', b'changed embedded native renderer')
        self.write('mods/sable.jar', archive.getvalue())
        row = next(row for row in self.receipt['nativeModules'] if row['modId'] == 'sable')
        row.update(self.pin(row['path']))
        self.dump(policy.RECEIPT, self.receipt)
        with self.assertRaisesRegex(ValueError, 'Embedded Veil bytes changed'):
            policy.prepare(self.root, apply=True)

    def test_duplicate_embedded_entry_is_refused(self):
        archive = BytesIO()
        with zipfile.ZipFile(archive, 'w') as jar:
            jar.writestr('META-INF/jarjar/veil.jar', b'one')
            with self.assertWarns(UserWarning):
                jar.writestr('META-INF/jarjar/veil.jar', b'two')
        self.write('mods/sable.jar', archive.getvalue())
        row = next(row for row in self.receipt['nativeModules'] if row['modId'] == 'sable')
        row.update(self.pin(row['path']))
        self.dump(policy.RECEIPT, self.receipt)
        with self.assertRaisesRegex(ValueError, 'entry missing or duplicated'):
            policy.prepare(self.root, apply=True)

    def test_platform_drift_and_native_identity_duplicates_are_refused(self):
        changed = deepcopy(self.platform)
        changed['loader']['version'] = '21.1.249'
        self.dump('portable-pack.json', changed)
        with self.assertRaisesRegex(ValueError, 'platform changed'):
            policy.verify(self.root)
        self.dump('portable-pack.json', self.platform)
        self.receipt['nativeModules'][0] = self.receipt['nativeModules'][1]
        self.dump(policy.RECEIPT, self.receipt)
        with self.assertRaisesRegex(ValueError, 'identities changed'):
            policy.verify(self.root)

    def test_author_import_missing_script_ownership_and_record_is_repaired_narrowly(self):
        (self.root / policy.SCRIPT).unlink()
        base = b'\xef\xbb\xbf# preserve comment\r\nconfig/fml.toml\r\n'
        self.write(policy.OWNED, base)
        self.lock['supportFiles'] = [row for row in self.lock['supportFiles'] if row.get('path') != policy.SCRIPT]
        self.dump(policy.LOCK, self.lock)
        self.write('options.txt', b'PERSONAL/OR AUTHOR OPTIONS RETAIN')
        self.write('config/unrelated.toml', b'quality=author-value\n')
        report = policy.prepare(self.root, apply=True)
        self.assertEqual({policy.SCRIPT, policy.OWNED, policy.LOCK}, set(report['changedPaths']))
        self.assertEqual(base + policy.SCRIPT.encode() + b'\r\n', (self.root / policy.OWNED).read_bytes())
        after = json.loads((self.root / policy.LOCK).read_text())
        self.assertEqual(self.lock['arbitraryAuthorField'], after['arbitraryAuthorField'])
        self.assertEqual(self.lock['authorVersion'], after['authorVersion'])
        self.assertEqual(self.lock['supportFiles'][-1], after['supportFiles'][-2])
        self.assertEqual('preserve-row-field', after['supportFiles'][0]['arbitrary'])
        self.assertEqual(b'PERSONAL/OR AUTHOR OPTIONS RETAIN', (self.root / 'options.txt').read_bytes())
        self.assertEqual(b'quality=author-value\n', (self.root / 'config/unrelated.toml').read_bytes())

    def test_verify_rejects_broad_only_ownership_and_stale_support_lock(self):
        self.write(policy.OWNED, b'kubejs/**\n')
        with self.assertRaisesRegex(ValueError, 'Broad hand-depth ownership'):
            policy.verify(self.root)
        self.write(policy.OWNED, self.ownership)
        self.lock['supportFiles'][1]['sha256'] = '0' * 64
        self.dump(policy.LOCK, self.lock)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'support lock differs'):
            policy.verify(self.root)
        self.assertEqual(before, self.snapshot())

    def test_duplicate_ownership_and_support_record_are_refused(self):
        self.write(policy.OWNED, self.ownership + policy.SCRIPT.encode() + b'\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate exact'):
            policy.prepare(self.root, apply=True)
        self.write(policy.OWNED, self.ownership)
        self.lock['supportFiles'].append(self.lock['supportFiles'][1])
        self.dump(policy.LOCK, self.lock)
        with self.assertRaisesRegex(ValueError, 'Duplicate hand-depth support'):
            policy.prepare(self.root, apply=True)

    def test_runtime_instances_are_refused_before_any_write(self):
        for relative in ('saves', 'logs/latest.log'):
            with self.subTest(relative=relative):
                path = self.root / relative
                if relative == 'saves':
                    path.mkdir()
                else:
                    self.write(relative, b'personal log')
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'source-only'):
                    policy.prepare(self.root, apply=True)
                self.assertEqual(before, self.snapshot())
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()

    def test_unsafe_paths_and_tampered_template_are_refused(self):
        for relative in ('../outside.jar', '/outside.jar', 'C:/outside.jar', 'mods\\outside.jar'):
            with self.subTest(relative=relative):
                self.receipt['nativeModules'][0]['path'] = relative
                self.dump(policy.RECEIPT, self.receipt)
                with self.assertRaises(ValueError):
                    policy.verify(self.root)

    def test_template_tampering_cannot_change_owned_runtime(self):
        self.write(policy.TEMPLATE, b'unknown changed source template')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'script template'):
            policy.prepare(self.root, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_failed_multi_file_preparation_rolls_back_our_writes(self):
        (self.root / policy.SCRIPT).unlink()
        self.write(policy.OWNED, b'config/fml.toml\n')
        before = self.snapshot()
        original = policy._replace_checked
        def fail_lock(path, expected, desired):
            if path == self.root / policy.LOCK:
                raise OSError('synthetic write failure')
            return original(path, expected, desired)
        with mock.patch.object(policy, '_replace_checked', side_effect=fail_lock):
            with self.assertRaisesRegex(OSError, 'synthetic write failure'):
                policy.prepare(self.root, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_cli_does_not_allow_mutating_verify(self):
        proc = subprocess.run([sys.executable, '-B', str(Path(policy.__file__)), 'verify', '--pack', str(self.root), '--apply'],
                              capture_output=True, text=True)
        self.assertNotEqual(0, proc.returncode)
        self.assertIn('verify is read-only', proc.stderr)

    def test_actual_template_behavior(self):
        proc = subprocess.run(['node', str(Path(__file__).parent / 'hand-depth/test_runtime.js')],
                              capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn('PASS actual template behavior:', proc.stdout)

    def test_publisher_refuses_missing_layer_before_collecting_assets(self):
        import generate_manifest
        (self.root / policy.SCRIPT).unlink()
        before = self.snapshot()
        out = self.root / 'dist'
        with mock.patch('generate_manifest.network_logging_policy.verify'), mock.patch('generate_manifest.waypoint_source_policy.verify'):
            with self.assertRaisesRegex(ValueError, 'script is absent or conflicts'):
                generate_manifest.generate(self.root, out, 'test')
        self.assertFalse(out.exists())
        self.assertEqual(before, self.snapshot())

    def test_verify_all_cli_enforces_real_hand_gate_without_repair(self):
        (self.root / policy.SCRIPT).unlink()
        before = self.snapshot()
        runner = '''
import runpy, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
script = sys.argv[1] + '/generate_manifest.py'
sys.argv = [script, '--pack', sys.argv[2], '--out', sys.argv[2] + '/dist', '--verify-all']
with patch('network_logging_policy.verify'), patch('waypoint_source_policy.verify'):
    runpy.run_path(script, run_name='__main__')
'''
        proc = subprocess.run([sys.executable, '-B', '-c', runner, str(Path(__file__).resolve().parent), str(self.root)],
                              capture_output=True, text=True)
        self.assertNotEqual(0, proc.returncode)
        self.assertIn('script is absent or conflicts', proc.stderr)
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main()
