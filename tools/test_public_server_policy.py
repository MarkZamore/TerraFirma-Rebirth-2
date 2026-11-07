import json
from pathlib import Path
import tempfile
import unittest

import public_server_policy as policy


class PublicServerPolicyTests(unittest.TestCase):
    def test_changed_menu_new_mod_or_delivered_seed_requires_new_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tools').mkdir()
            (root / 'config').mkdir()
            menu = root / 'config/menu.txt'
            menu.write_bytes(b'vanilla multiplayer')
            # The original source snapshot can retain its own server file.
            (root / 'servers.dat').write_bytes(b'author original, excluded from runtime')
            receipt = {'schemaVersion': 1, 'reviewedInputSha256': policy.fingerprint(root)}
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


if __name__ == '__main__':
    unittest.main()
