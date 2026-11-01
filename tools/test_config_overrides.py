"""Small independent publication projections; no game or personal files are used."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from config_overrides import (MAX_CONFIG_BYTES, MAX_OVERRIDES, MAX_POLICY_BYTES,
                              project_configs, projection_report, transform_toml_boolean)


class BooleanProjectionTests(unittest.TestCase):
    def test_exact_token_only_preserves_bom_crlf_comments_other_tables_and_values(self):
        raw = (b'\xef\xbb\xbf# author header\r\n[display] # author table\r\n'
               b'\tdoPopUpLogging = true # comment true\r\n\tdoDisplayShaderInGameMessage = true\r\n'
               b'[updates]\r\ndoPopUpLogging = true\r\ndoUpdateChecking = "important"\r\n')
        actual = transform_toml_boolean(raw, ['display'], 'doPopUpLogging', False)
        self.assertEqual(raw.replace(b'doPopUpLogging = true #', b'doPopUpLogging = false #', 1), actual)
        self.assertEqual(actual, transform_toml_boolean(actual, ['display'], 'doPopUpLogging', False))
        self.assertTrue(actual.startswith(b'\xef\xbb\xbf'))

    def test_updated_author_file_retains_new_keys_comments_and_changed_author_values(self):
        raw = (b'# new author release\n[display]\ndoPopUpLogging=true\nnewAuthorOption=27\n'
               b'doDisplayShaderInGameMessage=false\n[updates]\nmode="all"\n')
        projected = transform_toml_boolean(raw, ['display'], 'doPopUpLogging', False)
        self.assertEqual(raw.replace(b'doPopUpLogging=true', b'doPopUpLogging=false'), projected)
        parsed = tomllib.loads(projected.decode())
        self.assertEqual(27, parsed['display']['newAuthorOption'])
        self.assertFalse(parsed['display']['doDisplayShaderInGameMessage'])
        self.assertEqual('all', parsed['updates']['mode'])

    def test_quoted_and_nested_table_and_key_names_are_resolved_natively(self):
        raw = b'["render settings".messages]\n"pop.up" = false # retained\n'
        projected = transform_toml_boolean(raw, ['render settings', 'messages'], 'pop.up', True)
        self.assertEqual(raw.replace(b'= false', b'= true'), projected)

    def test_unrelated_nan_and_datetime_semantics_are_preserved(self):
        raw = b'[display]\ndoPopUpLogging=true\nnumber=nan\ndate=2026-11-01T00:00:00Z\n'
        self.assertEqual(raw.replace(b'Logging=true', b'Logging=false'),
                         transform_toml_boolean(raw, ['display'], 'doPopUpLogging', False))

    def test_missing_duplicate_invalid_and_non_boolean_targets_fail_closed(self):
        for raw in (b'[other]\ndoPopUpLogging=true\n', b'[display]\nother=true\n',
                    b'[display]\ndoPopUpLogging=true\ndoPopUpLogging=false\n',
                    b'[display]\ndoPopUpLogging="true"\n', b'[display]\ndoPopUpLogging=1\n',
                    b'[display]\ndoPopUpLogging=TRUE\n', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                transform_toml_boolean(raw, ['display'], 'doPopUpLogging', False)

    def test_multiline_string_lookalikes_cannot_change_another_value(self):
        raw = (b'[display]\nmessage="""\n[display]\ndoPopUpLogging=true\n"""\n'
               b'doPopUpLogging=true\n')
        with self.assertRaisesRegex(ValueError, 'unambiguous'):
            transform_toml_boolean(raw, ['display'], 'doPopUpLogging', False)

    def test_inline_table_representation_and_excessive_config_fail_closed(self):
        for raw in (b'display={doPopUpLogging=true}\n', b'#' * (MAX_CONFIG_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                transform_toml_boolean(raw, ['display'], 'doPopUpLogging', False)


class ConfigPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.pack = Path(temporary.name)
        self.relative = 'config/euphoria_patcher/settings.toml'
        self.source = self.pack / self.relative
        self.source.parent.mkdir(parents=True)
        self.raw = b'[display]\ndoPopUpLogging=true\nother=true # keep\n'
        self.source.write_bytes(self.raw)
        self.rule = {'path': self.relative, 'table': ['display'], 'key': 'doPopUpLogging',
                     'value': False, 'reason': 'Disable only patcher popups'}
        self.policy = self.pack / 'tools/pack-config-overrides.json'
        self.policy.parent.mkdir(parents=True)

    def write_policy(self, rules=None, **extra):
        document = {'schemaVersion': 1, 'overrides': [self.rule] if rules is None else rules, **extra}
        self.policy.write_text(json.dumps(document), encoding='utf-8')

    def test_no_policy_is_noop_and_empty_policy_is_only_allowed_outside_cli(self):
        self.assertEqual({}, project_configs(self.pack))
        self.assertEqual([], projection_report(self.pack))
        self.write_policy([])
        self.assertEqual({}, project_configs(self.pack))

    def test_repeated_projection_and_report_never_write_source_and_pin_both_byte_streams(self):
        self.write_policy()
        before = self.source.stat().st_mtime_ns
        with patch.object(Path, 'write_bytes', side_effect=AssertionError('Source must never be written')), \
                patch.object(Path, 'write_text', side_effect=AssertionError('Source must never be written')):
            projected = project_configs(self.pack)
            self.assertEqual(projected, project_configs(self.pack))
            report = projection_report(self.pack, projected)
        self.assertEqual(self.raw, self.source.read_bytes())
        self.assertEqual(before, self.source.stat().st_mtime_ns)
        row, = report
        self.assertEqual({'sizeBytes': len(self.raw), 'sha256': hashlib.sha256(self.raw).hexdigest()}, row['source'])
        delivery = projected[self.relative]
        self.assertEqual({'sizeBytes': len(delivery), 'sha256': hashlib.sha256(delivery).hexdigest()}, row['delivery'])
        self.assertEqual(['display'], row['table'])
        self.assertEqual('doPopUpLogging', row['key'])
        self.assertFalse(row['value'])

    def test_multiple_distinct_keys_in_one_file_are_composed_without_source_changes(self):
        self.write_policy([self.rule, {**self.rule, 'key': 'other'}])
        projected = project_configs(self.pack)
        self.assertEqual(b'[display]\ndoPopUpLogging=false\nother=false # keep\n', projected[self.relative])
        self.assertEqual(2, len(projection_report(self.pack, projected)))
        self.assertEqual(self.raw, self.source.read_bytes())

    def test_invalid_schema_duplicate_rule_and_json_fields_are_rejected(self):
        for document in ({'schemaVersion': 2, 'overrides': [self.rule]},
                         {'schemaVersion': True, 'overrides': [self.rule]},
                         {'schemaVersion': 1}, {'schemaVersion': 1, 'overrides': [self.rule], 'extra': 1},
                         {'schemaVersion': 1, 'overrides': [self.rule, self.rule]},
                         {'schemaVersion': 1, 'overrides': [{**self.rule, 'value': 0}]},
                         {'schemaVersion': 1, 'overrides': [{**self.rule, 'reason': ''}]},
                         {'schemaVersion': 1, 'overrides': [{**self.rule, 'unknown': True}]}):
            self.policy.write_text(json.dumps(document), encoding='utf-8')
            with self.subTest(document=document), self.assertRaises(ValueError):
                project_configs(self.pack)
        self.policy.write_text('{"schemaVersion":1,"schemaVersion":1,"overrides":[]}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON'):
            project_configs(self.pack)

    def test_path_escapes_non_config_targets_and_casing_aliases_are_rejected(self):
        for path in ('../settings.toml', '/config/settings.toml', 'config/../settings.toml',
                     'config\\settings.toml', 'C:/config/settings.toml', 'config//settings.toml',
                     'launcher/settings.toml', 'config/settings.json', 'config/settings.toml:stream'):
            self.write_policy([{**self.rule, 'path': path}])
            with self.subTest(path=path), self.assertRaises(ValueError):
                project_configs(self.pack)
        self.write_policy([self.rule, {**self.rule, 'path': self.relative.upper().replace('CONFIG/', 'config/'), 'key': 'other'}])
        with self.assertRaisesRegex(ValueError, 'casing'):
            project_configs(self.pack)

    def test_missing_source_and_bounded_policy_are_rejected(self):
        self.write_policy([{**self.rule, 'path': 'config/missing.toml'}])
        with self.assertRaisesRegex(ValueError, 'missing'):
            project_configs(self.pack)
        self.policy.write_bytes(b' ' * (MAX_POLICY_BYTES + 1))
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            project_configs(self.pack)
        self.write_policy([self.rule] * (MAX_OVERRIDES + 1))
        with self.assertRaisesRegex(ValueError, 'count'):
            project_configs(self.pack)

    def test_report_rejects_delivery_bytes_outside_declared_policy(self):
        self.write_policy()
        with self.assertRaisesRegex(ValueError, 'differ'):
            projection_report(self.pack, {self.relative: self.raw})
        with self.assertRaisesRegex(ValueError, 'differ'):
            projection_report(self.pack, {})

    def test_cli_requires_present_nonempty_policy_and_reports_without_writing(self):
        command = [sys.executable, '-B', str(Path(__file__).with_name('config_overrides.py')),
                   '--pack', str(self.pack), '--check']
        missing = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(0, missing.returncode)
        self.write_policy([])
        empty = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(0, empty.returncode)
        self.write_policy()
        passed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, passed.returncode, passed.stderr)
        report = json.loads(passed.stdout)
        self.assertEqual('PASS', report['status'])
        self.assertTrue(report['nonMutating'])
        self.assertFalse(report['overrides'][0]['value'])
        self.assertEqual(self.raw, self.source.read_bytes())


if __name__ == '__main__':
    unittest.main()
