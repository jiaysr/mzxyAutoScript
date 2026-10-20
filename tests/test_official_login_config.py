"""Official credentials use synthetic data only; never read/write user configs."""
import ast
import json
import sys
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ConfigDict, ValidationError

ROOT = Path(__file__).resolve().parents[1]

# Import configuration code without starting the application's log file cleanup.
# Restore only this entry: patch.dict(sys.modules) also unloads lazy Pydantic imports.
_previous_logger = sys.modules.get('module.logger')
sys.modules['module.logger'] = SimpleNamespace(logger=Mock())
try:
    from module.config import config_model, config_modify
    from module.config.config import Config
    from module.config.utils import convert_to_underscore
    from tasks.Restart.config import LoginOfficialConfig, LoginX7Config, Restart
finally:
    if _previous_logger is None:
        sys.modules.pop('module.logger', None)
    else:
        sys.modules['module.logger'] = _previous_logger


def load_script_setter(logger):
    """Execute the actual method without importing device/OCR/RPC startup code."""
    path = ROOT / 'script.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    script_class = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == 'Script')
    method = next(node for node in script_class.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'gui_set_task')
    namespace = dict(datetime=datetime, ValidationError=ValidationError,
                     convert_to_underscore=convert_to_underscore, logger=logger)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['gui_set_task']


class ValidatedOfficialConfig(LoginOfficialConfig):
    # Exercise the setters' validation-failure paths without changing model policy.
    model_config = ConfigDict(validate_assignment=True)


class OfficialLoginConfigTest(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()
        for module in (config_model, config_modify):
            patcher = patch.object(module, 'logger', self.logger)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.model = config_model.ConfigModel()
        writer = patch.object(config_model.ConfigModel, 'write_json')
        self.write_json = writer.start()
        self.addCleanup(writer.stop)
        self.gui = object.__new__(config_modify.ConfigModify)
        self.gui.model = self.model
        self.gui.config_name = 'unit_test_only'
        self.config = object.__new__(Config)
        self.config.model = self.model
        self.config.config_name = 'unit_test_only'
        script = SimpleNamespace(config=self.config)
        script_setter = load_script_setter(self.logger)
        self.setters = {
            'script_set_arg': self.model.script_set_arg,
            'config_modify.gui_set_task': self.gui.gui_set_task,
            'script.gui_set_task': lambda *args: script_setter(script, *args),
        }

    def log_text(self):
        return '\n'.join(str(call) for call in self.logger.mock_calls)

    def test_defaults_schema_repr_and_legacy_config(self):
        self.assertEqual(LoginOfficialConfig().password, '')
        legacy = Restart.model_validate({'login_official_config': {'account': 'dummy-account'}})
        self.assertEqual(legacy.login_official_config.password, '')
        model = LoginOfficialConfig(password='synthetic-password-not-real')
        self.assertNotIn(model.password, repr(model))
        self.assertNotIn(model.password, str(model))
        self.assertIn(model.password, model.model_dump_json())
        fields = model.model_json_schema()['properties']
        self.assertEqual(fields['password']['type'], 'string')
        self.assertEqual(fields['password']['default'], '')
        self.assertEqual(fields['password']['description'], 'restart_login_password_help')
        self.assertEqual(fields['account']['description'], 'restart_login_official_account_help')
        self.assertEqual(LoginX7Config.model_fields['account'].description, 'restart_login_account_help')
        self.assertNotIn('password', LoginX7Config.model_fields)

    def test_template_and_translations(self):
        template = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))
        official = template['restart']['login_official_config']
        self.assertEqual(official['password'], '')
        self.assertEqual(official['account'], '')
        for language in ('zh-CN', 'en-US'):
            translations = json.loads((ROOT / f'assets/i18n/{language}.json').read_text(encoding='utf-8'))
            for key in ('password', 'restart_login_official_account_help', 'restart_login_password_help'):
                self.assertTrue(translations[key])

    def test_all_setters_preserve_credentials_and_saved_json(self):
        values = ('', '12:34:56', '01 02:03:04', '2026-01-02 03:04:05',
                  'true', 'false', '00123456', '00012345678',
                  '0001234567890123456', '  synthetic-only  ', '测试专用值')
        for name, setter in self.setters.items():
            for field in ('account', 'password'):
                for value in values:
                    with self.subTest(setter=name, field=field, value=value):
                        self.write_json.reset_mock()
                        self.assertTrue(setter('restart', 'login_official_config', field, value))
                        actual = getattr(self.model.restart.login_official_config, field)
                        self.assertIs(type(actual), str)
                        self.assertEqual(actual, value)
                        self.write_json.assert_called_once()
                        payload = self.write_json.call_args.args[1]
                        saved = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
                        restored = LoginOfficialConfig.model_validate(saved['restart']['login_official_config'])
                        self.assertEqual(getattr(restored, field), value)

    def test_normalized_names_preserve_credentials(self):
        for name, setter in self.setters.items():
            for field in ('Account', 'Password'):
                with self.subTest(setter=name, field=field):
                    self.assertTrue(setter('Restart', 'LoginOfficialConfig', field, '12:34:56'))
                    self.assertEqual(getattr(self.model.restart.login_official_config, field.lower()), '12:34:56')

    def test_password_success_logs_are_redacted(self):
        for name, setter in self.setters.items():
            with self.subTest(setter=name):
                self.logger.reset_mock()
                secret = 'synthetic-secret-success-only'
                self.assertTrue(setter('Restart', 'LoginOfficialConfig', 'Password', secret))
                self.assertNotIn(secret, self.log_text())
                self.assertIn('[REDACTED]', self.log_text())
                self.assertIn('restart.login_official_config.password', self.log_text())

    def test_missing_password_logs_are_redacted(self):
        self.model.restart.login_official_config = SimpleNamespace(account='')
        for name, setter in self.setters.items():
            with self.subTest(setter=name):
                self.logger.reset_mock()
                self.write_json.reset_mock()
                secret = 'synthetic-secret-missing-only'
                self.assertFalse(setter('restart', 'login_official_config', 'password', secret))
                self.assertNotIn(secret, self.log_text())
                self.assertIn('[REDACTED]', self.log_text())
                self.write_json.assert_not_called()

    def test_password_validation_errors_do_not_log_input(self):
        self.model.restart.login_official_config = ValidatedOfficialConfig()
        for name, setter in self.setters.items():
            with self.subTest(setter=name):
                self.logger.reset_mock()
                self.write_json.reset_mock()
                secret = 'synthetic-secret-invalid-only'
                self.assertFalse(setter('restart', 'login_official_config', 'password', [secret]))
                self.assertNotIn(secret, self.log_text())
                self.assertIn('validation failed [REDACTED]', self.log_text())
                self.write_json.assert_not_called()
                self.assertEqual(self.model.restart.login_official_config.password, '')

    def test_save_validation_errors_do_not_log_password(self):
        secret = 'synthetic-secret-save-only'
        try:
            LoginOfficialConfig(password=[secret])
        except ValidationError as error:
            self.write_json.side_effect = error
        for name, setter in self.setters.items():
            with self.subTest(setter=name):
                self.logger.reset_mock()
                self.assertFalse(setter('restart', 'login_official_config', 'password', secret))
                self.assertNotIn(secret, self.log_text())
                self.assertIn('validation failed [REDACTED]', self.log_text())

    def test_other_arguments_keep_existing_conversions(self):
        # Use a neutral group to observe conversions, without serializing invalid types.
        group = SimpleNamespace(account='')
        self.model.restart.login_x7_config = group
        with patch.object(config_model.ConfigModel, 'save'), patch.object(Config, 'save'):
            for value, expected in (
                ('true', True), ('false', False), ('12:34:56', time(12, 34, 56)),
                ('01 02:03:04', timedelta(days=1, hours=2, minutes=3, seconds=4)),
                ('2026-01-02 03:04:05', datetime(2026, 1, 2, 3, 4, 5)),
            ):
                with self.subTest(value=value):
                    self.assertTrue(self.model.script_set_arg('restart', 'login_x7_config', 'account', value))
                    self.assertEqual(group.account, expected)
                    self.assertIs(type(group.account), type(expected))
            self.assertTrue(self.setters['script.gui_set_task']('restart', 'login_x7_config', 'account', '12:34:56'))
            self.assertEqual(group.account, time(12, 34, 56))
            self.assertTrue(self.gui.gui_set_task('restart', 'login_x7_config', 'account', '12:34:56'))
            self.assertEqual(group.account, '12:34:56')


if __name__ == '__main__':
    unittest.main()
