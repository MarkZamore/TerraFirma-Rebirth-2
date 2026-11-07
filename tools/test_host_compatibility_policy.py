"""The publication gate must preserve source pins and reject unreviewed code."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import host_compatibility_policy as policy

SOURCE = Path(__file__).resolve().parents[1]


class HostCompatibilityTests(unittest.TestCase):
    def test_actual_scripts(self):
        for name in ('test_player_markers.js', 'test_lss_config_migration.js'):
            subprocess.run(['node', str(SOURCE / 'tools/qa' / name)], check=True)

    def test_missing_script_restored_but_changed_native_or_unknown_script_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = policy.load(SOURCE, policy.RECEIPT)
            paths = [policy.RECEIPT, policy.OWNED, policy.LOCK, 'portable-pack.json']
            paths += [row['path'] for row in receipt['nativeModules']]
            paths += [field for row in receipt['scripts'] for field in (row['path'], row['template'])]
            for relative in paths:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SOURCE / relative, destination)
            policy.verify(root)
            runtime = root / receipt['scripts'][0]['path']
            raw = runtime.read_bytes()
            runtime.unlink()
            with self.assertRaises(ValueError):
                policy.verify(root)
            policy.prepare(root, apply=True)
            self.assertEqual(raw, runtime.read_bytes())
            runtime.write_bytes(b'unknown local change')
            with self.assertRaises(ValueError):
                policy.prepare(root, apply=True)
            self.assertEqual(b'unknown local change', runtime.read_bytes())
            runtime.write_bytes(raw)
            module = root / receipt['nativeModules'][0]['path']
            module.write_bytes(b'new author version requires review')
            with self.assertRaises(ValueError):
                policy.prepare(root, apply=True)


if __name__ == '__main__':
    unittest.main()
