"""Meaningful source-delivery failures; these fixtures make no native/game claim."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

import waypoint_source_policy as policy


class WaypointSourcePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('portable-pack.json', json.dumps({'minecraftVersion': '1.21.1',
            'loader': {'type': 'neoforge', 'version': '21.1.248'}, 'clientJar': 'fixture.jar'}).encode())
        self.write('launcher/pack-owned.txt', (policy.SCRIPT + '\n').encode())
        self.script = b'// synthetic owned fixture, not a runtime or native test\n'
        self.write(policy.TEMPLATE, self.script)
        self.write(policy.SCRIPT, self.script)
        modules = []
        for mod in sorted(policy.NATIVE_IDS):
            path = 'mods/' + mod + '.jar'
            self.write(path, ('synthetic source bytes: ' + mod).encode())
            modules.append(dict(self.pin(path), modId=mod))
        profile = {'minecraftVersion': '1.21.1', 'loader': {'type': 'neoforge', 'version': '21.1.248'},
            'modules': {row['modId']: {'fileName': row['path'].split('/')[-1],
                'sizeBytes': row['sizeBytes'], 'sha256': row['sha256']} for row in modules}}
        self.proof = {'status': 'PASS_EXACT_SOURCE_JAR_AND_NATIVE_PUBLIC_API_ONLY', 'profiles': [profile]}
        self.dump(policy.PROOF, self.proof)
        self.receipt = {'schemaVersion': 1, 'kind': 'pack-owned-waypoint-generation-association',
            'platform': {'minecraftVersion': '1.21.1', 'loader': profile['loader']},
            'script': dict(self.pin(policy.SCRIPT), template=policy.TEMPLATE), 'nativeModules': modules,
            'nativePublicApiProof': policy.PROOF, 'nativePublicApiProofSha256': self.pin(policy.PROOF)['sha256']}
        self.dump(policy.RECEIPT, self.receipt)

    def write(self, relative, raw):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def dump(self, relative, value):
        self.write(relative, json.dumps(value).encode())

    def pin(self, relative):
        raw = (self.root / relative).read_bytes()
        return {'path': relative, 'sizeBytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}

    def test_read_only_verify_claims_only_source(self):
        report = policy.verify(self.root)
        self.assertEqual('PASS_SOURCE_ONLY', report['status'])
        self.assertEqual(0, report['restoredOwnedScripts'])
        self.assertFalse(report['runtimeOrWorldAcceptanceClaimed'])

    def test_missing_receipt_blocks_prepare_without_writing(self):
        (self.root / policy.RECEIPT).unlink()
        (self.root / policy.SCRIPT).unlink()
        with self.assertRaisesRegex(ValueError, 'Required waypoint source file is absent'):
            policy.prepare(self.root, apply=True)
        self.assertFalse((self.root / policy.SCRIPT).exists())

    def test_missing_owned_script_requires_explicit_apply_and_restores_once(self):
        (self.root / policy.SCRIPT).unlink()
        with self.assertRaisesRegex(ValueError, 'script is absent'):
            policy.verify(self.root)
        self.assertFalse((self.root / policy.SCRIPT).exists())
        self.assertEqual(1, policy.prepare(self.root, apply=True)['restoredOwnedScripts'])
        self.assertEqual(self.script, (self.root / policy.SCRIPT).read_bytes())
        self.assertEqual(0, policy.prepare(self.root, apply=True)['restoredOwnedScripts'])

    def test_missing_template_cannot_restore_script(self):
        (self.root / policy.SCRIPT).unlink()
        (self.root / policy.TEMPLATE).unlink()
        with self.assertRaisesRegex(ValueError, 'template changed or is missing'):
            policy.prepare(self.root, apply=True)
        self.assertFalse((self.root / policy.SCRIPT).exists())

    def test_changed_template_cannot_replace_runtime(self):
        self.write(policy.TEMPLATE, b'unknown template')
        with self.assertRaisesRegex(ValueError, 'template changed or is missing'):
            policy.prepare(self.root, apply=True)
        self.assertEqual(self.script, (self.root / policy.SCRIPT).read_bytes())

    def test_unknown_active_bytes_are_preserved_even_with_apply(self):
        unknown = b'// author or personal code must not be overwritten\n'
        self.write(policy.SCRIPT, unknown)
        with self.assertRaisesRegex(ValueError, 'Conflicting owned'):
            policy.prepare(self.root, apply=True)
        self.assertEqual(unknown, (self.root / policy.SCRIPT).read_bytes())

    def test_author_native_drift_blocks_apply_before_any_write(self):
        (self.root / policy.SCRIPT).unlink()
        self.write('mods/journeymap.jar', b'new upstream bytes')
        with self.assertRaisesRegex(ValueError, 'native module journeymap'):
            policy.prepare(self.root, apply=True)
        self.assertFalse((self.root / policy.SCRIPT).exists())

    def test_platform_update_requires_reviewed_profile(self):
        self.dump('portable-pack.json', {'minecraftVersion': '1.21.1',
            'loader': {'type': 'neoforge', 'version': '21.1.249'}})
        with self.assertRaisesRegex(ValueError, 'platform changed'):
            policy.verify(self.root)

    def test_other_minecraft_or_loader_cannot_use_profile(self):
        self.receipt['platform']['minecraftVersion'] = '1.20.1'
        self.dump(policy.RECEIPT, self.receipt)
        with self.assertRaisesRegex(ValueError, 'Unreviewed waypoint platform'):
            policy.verify(self.root)

    def test_exact_ownership_is_required_instead_of_broad_glob(self):
        self.write('launcher/pack-owned.txt', b'kubejs/**\n')
        with self.assertRaisesRegex(ValueError, 'Exact waypoint script ownership'):
            policy.verify(self.root)

    def test_traversal_or_absolute_native_path_is_refused(self):
        for bad in ['mods/../../outside.jar', '/outside.jar', 'C:/outside.jar', 'mods\\outside.jar']:
            with self.subTest(path=bad):
                self.receipt['nativeModules'][0]['path'] = bad
                self.dump(policy.RECEIPT, self.receipt)
                with self.assertRaises(ValueError):
                    policy.verify(self.root)

    def test_native_proof_bytes_cannot_drift_silently(self):
        self.dump(policy.PROOF, {'status': 'unknown'})
        with self.assertRaisesRegex(ValueError, 'API proof changed'):
            policy.verify(self.root)

    def test_updated_native_pin_still_requires_matching_native_api_proof(self):
        row = self.receipt['nativeModules'][0]
        self.write(row['path'], b'new reviewed-or-unreviewed native bytes')
        row.update(self.pin(row['path']))
        self.dump(policy.RECEIPT, self.receipt)
        with self.assertRaisesRegex(ValueError, 'API profile module changed'):
            policy.verify(self.root)

    def test_duplicate_native_identity_cannot_compensate_missing_module(self):
        self.receipt['nativeModules'][0] = self.receipt['nativeModules'][1]
        self.dump(policy.RECEIPT, self.receipt)
        with self.assertRaisesRegex(ValueError, 'module identities changed'):
            policy.verify(self.root)

    def test_apply_preserves_personal_options_and_waypoint_files(self):
        self.write('options.txt', b'PERSONAL')
        self.write('journeymap/data/sp/Old/waypoints/death.json', b'USER WAYPOINT')
        (self.root / policy.SCRIPT).unlink()
        policy.prepare(self.root, apply=True)
        self.assertEqual(b'PERSONAL', (self.root / 'options.txt').read_bytes())
        self.assertEqual(b'USER WAYPOINT', (self.root / 'journeymap/data/sp/Old/waypoints/death.json').read_bytes())

    def test_linked_template_directory_is_refused(self):
        original = self.root / 'tools/client-policy/scripts'
        retained = self.root / 'tools/retained'
        original.rename(retained)
        if os.name == 'nt':
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                "New-Item -ItemType Junction -Path '" + str(original).replace("'", "''")
                + "' -Target '" + str(retained).replace("'", "''") + "' | Out-Null"],
                capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
        else:
            original.symlink_to(retained, target_is_directory=True)
        self.addCleanup(lambda: original.unlink() if original.is_symlink() else original.rmdir())
        with self.assertRaisesRegex(ValueError, 'Linked waypoint source path'):
            policy.verify(self.root)

    def test_ci_prepare_pack_cli_restores_only_missing_script(self):
        (self.root / policy.SCRIPT).unlink()
        result = subprocess.run([sys.executable, str(Path(policy.__file__).resolve()),
            'prepare', '--pack', str(self.root), '--apply'], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, json.loads(result.stdout)['restoredOwnedScripts'])
        self.assertEqual(self.script, (self.root / policy.SCRIPT).read_bytes())

    def test_verify_cli_refuses_apply_without_writing(self):
        (self.root / policy.SCRIPT).unlink()
        result = subprocess.run([sys.executable, str(Path(policy.__file__).resolve()),
            'verify', '--pack', str(self.root), '--apply'], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('verify is read-only', result.stderr)
        self.assertFalse((self.root / policy.SCRIPT).exists())


if __name__ == '__main__':
    unittest.main()
