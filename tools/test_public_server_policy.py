import json
from pathlib import Path
import tempfile
import unittest

import public_server_policy as policy


class PublicServerPolicyTests(unittest.TestCase):
    def test_every_generator_root_is_reviewed(self):
        from generate_manifest import SCAN_ROOTS
        self.assertEqual(set(policy.ROOTS), set(SCAN_ROOTS))

    def test_changed_menu_new_mod_or_delivered_seed_requires_new_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tools').mkdir()
            (root / 'config').mkdir()
            menu = root / 'config/menu.txt'
            menu.write_bytes(b'vanilla multiplayer')
            # The original source snapshot can retain its own server file.
            (root / 'servers.dat').write_bytes(b'author original, excluded from runtime')
            receipt = {'schemaVersion': 1, 'coveredRoots': list(policy.ROOTS),
                       'reviewedInputSha256': policy.fingerprint(root)}
            (root / policy.RECEIPT).write_text(json.dumps(receipt), encoding='utf-8')
            policy.verify(root)
            menu.write_bytes(b'new server recommendation')
            with self.assertRaises(ValueError):
                policy.verify(root)
            menu.write_bytes(b'vanilla multiplayer')
            (root / 'mods').mkdir()
            mod = root / 'mods/new.jar'
            mod.write_bytes(b'new native hooks need review')
            with self.assertRaises(ValueError):
                policy.verify(root)
            mod.unlink()
            (root / 'config/servers.dat').write_bytes(b'seed')
            with self.assertRaises(ValueError):
                policy.verify(root)

    def test_missing_receipt_and_new_author_catalog_block_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tools').mkdir()
            receipt = root / policy.RECEIPT
            with self.assertRaises(FileNotFoundError):
                policy.prepare(root)
            receipt.write_text(json.dumps({'schemaVersion': 1, 'coveredRoots': list(policy.ROOTS),
                'reviewedInputSha256': policy.fingerprint(root)}), encoding='utf-8')
            policy.prepare(root)
            (root / 'global_packs').mkdir()
            (root / 'global_packs/catalog.json').write_text('{"servers": ["example.invalid"]}', encoding='utf-8')
            with self.assertRaises(ValueError):
                policy.prepare(root)


if __name__ == '__main__':
    unittest.main()
