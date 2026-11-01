import ast
import logging
import os
import runpy
import sys
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.exception import GameStuckError, RequestHumanTakeover
from module.handler.sensitive_info import PasswordAccessFilter
from tasks.GameUi.assets import GameUiAssets as GA
from tasks.Restart.config import LoginOfficialConfig
from tasks.Restart.login_official import RestartOfficial
from tasks.Restart.login_x7 import RestartX7, ServerEntry


def task_for_test(**config):
    task = RestartOfficial.__new__(RestartOfficial)
    task.config = SimpleNamespace(restart=SimpleNamespace(login_official_config=LoginOfficialConfig(**config)))
    task.device = Mock(package='com.pip.android.xuanyuan')
    task.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
    task.screenshot = Mock(side_effect=lambda: task.device.image)
    task.appear = Mock(return_value=False)
    task.interval_timer = {}
    return task


class OfficialLoginTest(unittest.TestCase):
    def test_entrypoint_selects_handler_after_device_initialization(self):
        for standalone in (True, False):
            for target in ('official', 'x7'):
                with self.subTest(standalone=standalone, target=target):
                    state = {'client': 'x7' if standalone else target}
                    config = Mock()
                    device = Mock()

                    def init_device(cfg):
                        self.assertIs(cfg, config)
                        state['client'] = target
                        return device

                    class FakeX7:
                        app_restart = Mock()

                        def __init__(self, cfg, dev):
                            self.config, self.device = cfg, dev

                    class FakeOfficial(FakeX7):
                        app_restart = Mock()

                    modules = {
                        'module.config.client': SimpleNamespace(CLIENT_OFFICIAL='official',
                                                               get_client=lambda: state['client']),
                        'module.config.config': SimpleNamespace(Config=Mock(return_value=config)),
                        'module.device.device': SimpleNamespace(Device=Mock(side_effect=init_device)),
                        'module.logger': SimpleNamespace(logger=Mock()),
                        'tasks.Restart.login_x7': SimpleNamespace(RestartX7=FakeX7),
                        'tasks.Restart.login_official': SimpleNamespace(RestartOfficial=FakeOfficial),
                    }
                    with patch.dict(sys.modules, modules):
                        namespace = runpy.run_path(str(ROOT / 'tasks/Restart/script_task.py'),
                                                  run_name='__main__' if standalone else 'restart_test_module')
                    expected = FakeOfficial if target == 'official' else FakeX7
                    self.assertEqual(namespace['ScriptTask'].__bases__, (expected,))
                    if standalone:
                        modules['module.device.device'].Device.assert_called_once_with(config)
                        expected.app_restart.assert_called_once()
                        self.assertIs(namespace['task'].device, device)
                    else:
                        modules['module.device.device'].Device.assert_not_called()
                        expected.app_restart.assert_not_called()

    def test_load_target_is_official_not_x7(self):
        task = task_for_test(server='26区', account='测试账号(上次登录)', character='右侧角色')
        task.load_login_target()
        self.assertEqual(task.server_zone, 26)
        self.assertEqual(task.account_name, '测试账号(上次登录)')
        self.assertEqual(task.character_name, '右侧角色')

    def test_account_submits_unicode_and_exact_password(self):
        task = task_for_test(account='测试账号', password='  fake;&%密码  ')
        fields = Mock(count=2)
        account, password, confirm = Mock(), Mock(), Mock()
        fields.__getitem__ = Mock(side_effect=[account, password])
        task.device.u2.side_effect = [fields, confirm]
        task.handle_account_page()
        account.set_text.assert_called_once_with('测试账号', timeout=1)
        password.set_text.assert_called_once_with('  fake;&%密码  ', timeout=1)
        confirm.click.assert_called_once_with(timeout=1)
        task.device.u2.assert_any_call(text='确定', className='android.widget.Button',
                                       packageName='com.pip.android.xuanyuan')

    def test_empty_credentials_preserve_remembered_input(self):
        task = task_for_test()
        fields, confirm = Mock(count=2), Mock()
        task.device.u2.side_effect = [fields, confirm]
        task.handle_account_page()
        self.assertEqual(fields.mock_calls, [])
        confirm.click.assert_called_once()

    def test_error_stops_before_input(self):
        task = task_for_test()
        task.appear.side_effect = lambda rule: rule == task.I_OFFICIAL_ACCOUNT_ERROR
        with self.assertRaisesRegex(RequestHumanTakeover, '^账号密码错误$'):
            task.handle_account_page()
        task.device.u2.assert_not_called()

    def test_error_after_submit_does_not_retry(self):
        task = task_for_test()
        task.device.u2.side_effect = [Mock(count=2), Mock()]
        task.appear.side_effect = [False, True]
        with self.assertRaisesRegex(RequestHumanTakeover, '^账号密码错误$'):
            task.handle_account_page()
        self.assertEqual(task.device.u2.call_count, 2)
        task.device.app_start.assert_not_called()

    def test_input_rpc_failure_does_not_expose_secret(self):
        task = task_for_test(password='synthetic-secret')
        task.device.u2.side_effect = RuntimeError('setText synthetic-secret')
        with self.assertRaises(RequestHumanTakeover) as raised:
            task.handle_account_page()
        self.assertNotIn('synthetic-secret', str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_bad_native_layout_does_not_submit(self):
        task = task_for_test()
        task.device.u2.return_value.count = 1
        with self.assertRaises(RequestHumanTakeover):
            task.handle_account_page()
        self.assertEqual(task.device.u2.call_count, 1)

    def test_account_wait_is_bounded(self):
        task = task_for_test()
        task.device.u2.side_effect = [Mock(count=2), Mock()]
        with patch('tasks.Restart.login_official.Timer') as timer:
            timer.return_value.start.return_value.reached.return_value = True
            with self.assertRaises(GameStuckError):
                task.handle_account_page()

    def test_named_server_clicks_ocr_center(self):
        task = task_for_test()
        task.server_zone = None
        task.appear.return_value = True
        entry = ServerEntry(6, 618.5, 199.5, '6区-测试')
        task.parse_server_entries = Mock(return_value=[entry])
        task.click_server_entry = Mock(return_value=True)
        self.assertTrue(task.select_from_mine())
        task.click_server_entry.assert_called_once_with(entry)

    def test_named_server_uses_character_list(self):
        task = task_for_test()
        task.server_zone = 26
        task.appear.return_value = True
        task.parse_server_entries = Mock(return_value=[ServerEntry(6, 618, 199, '6区')])
        entries = [ServerEntry(26, 910, 403, '26区')]
        task.mine_entries = Mock(return_value=entries)
        task.click_target_server = Mock(return_value=True)
        self.assertTrue(task.select_from_mine())
        task.click_target_server.assert_called_once_with(entries)

    def test_no_name_layout_reuses_existing_logic(self):
        task = task_for_test()
        with patch.object(RestartX7, 'select_from_mine', return_value=True) as original:
            self.assertTrue(task.select_from_mine())
            original.assert_called_once()
        task.parse_server_entries = Mock(return_value=[])
        task.all_entries()
        task.parse_server_entries.assert_called_with(task.O_X7_SERVER_ALL_LIST)
        task.mine_entries()
        task.parse_server_entries.assert_called_with(task.O_X7_SERVER_MINE_LIST)

    def test_named_list_does_not_mutate_x7_rules(self):
        task = task_for_test()
        roi = RestartX7.O_X7_SERVER_ALL_LIST.roi
        task.appear.return_value = True
        task.parse_server_entries = Mock(return_value=[])
        task.all_entries()
        task.parse_server_entries.assert_called_once_with(task.O_OFFICIAL_SERVER_LIST)
        self.assertEqual(RestartX7.O_X7_SERVER_ALL_LIST.roi, roi)

    def test_official_login_click_is_on_left(self):
        task = task_for_test()
        task.appear.return_value = True
        task.page_click = Mock()
        task.handle_login_page()
        task.page_click.assert_called_once_with(task.I_OFFICIAL_LOGIN_ENTER)
        self.assertLess(task.I_OFFICIAL_LOGIN_ENTER.roi_front[0], 300)

    def test_full_state_machine(self):
        task = task_for_test()
        pages = iter([task.I_OFFICIAL_LOGIN_ENTER, task.I_X7_SERVER_PAGE,
                      task.I_OFFICIAL_ACCOUNT_PAGE, task.I_X7_CHARACTER_PAGE, GA.I_PAGE_MAIN])
        current = [None]
        task.screenshot.side_effect = lambda: current.__setitem__(0, next(pages))
        task.appear.side_effect = lambda rule: rule == current[0]
        for method in ('handle_login_page', 'handle_server_page', 'handle_account_page',
                       'handle_character_page', 'handle_popup'):
            setattr(task, method, Mock())
        self.assertTrue(task._app_handle_login())
        for method in ('handle_login_page', 'handle_server_page', 'handle_account_page',
                       'handle_character_page', 'handle_popup'):
            getattr(task, method).assert_called_once()
        self.assertIs(RestartOfficial.handle_character_page, RestartX7.handle_character_page)

    def test_account_frame_is_not_saved_with_credentials(self):
        task = task_for_test()
        del task.screenshot
        frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
        task.device.image = frame
        task.device.screenshot_deque = deque([{'image': frame.copy()}])
        task.handle_death = Mock()
        task.handle_captcha = Mock()
        task.handle_global_popup = Mock()
        with patch.object(task.I_OFFICIAL_ACCOUNT_PAGE, 'match', return_value=True):
            task.screenshot()
        self.assertFalse(frame[80:262].any())
        self.assertTrue(frame[:80].all())
        self.assertEqual(len(task.device.screenshot_deque), 0)
        task.handle_death.assert_called_once()
        task.handle_captcha.assert_called_once()
        task.handle_global_popup.assert_called_once()

    def test_global_handler_exception_cannot_retain_credentials(self):
        task = task_for_test()
        del task.screenshot
        task.device.image[:] = 255
        task.device.screenshot_deque = deque([{'image': task.device.image}])
        task.handle_death = Mock(side_effect=RuntimeError('global handler failure'))
        with patch.object(task.I_OFFICIAL_ACCOUNT_PAGE, 'match', return_value=True):
            with self.assertRaises(RuntimeError):
                task.screenshot()
        self.assertFalse(task.device.image[80:262].any())
        self.assertFalse(task.device.screenshot_deque)

    def test_capture_or_template_failure_clears_unsafe_cache(self):
        for stage in ('capture', 'match'):
            task = task_for_test()
            del task.screenshot
            task.device.screenshot_deque = deque([{'image': task.device.image}])
            if stage == 'capture':
                task.device.screenshot.side_effect = RuntimeError('capture failure')
            with patch.object(task.I_OFFICIAL_ACCOUNT_PAGE, 'match', side_effect=RuntimeError('template failure')):
                with self.assertRaises(RuntimeError):
                    task.screenshot()
            self.assertFalse(task.device.screenshot_deque)

    def test_password_access_log_hides_entire_query(self):
        for path in ('/oas/restart/login_official_config/password/value',
                     '/oas/restart/login_official_config/Password/value/',
                     '/oas/restart/login_official_config/%70assword/value'):
            record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0,
                                       '%s - "%s %s HTTP/%s" %d',
                                       ('local', 'PUT', path + '?types=string&value=fake-secret', '1.1', 200), None)
            self.assertTrue(PasswordAccessFilter().filter(record))
            self.assertNotIn('fake-secret', record.getMessage())
            self.assertIn('[REDACTED]', record.getMessage())
        record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0,
                                   '%s - "%s %s HTTP/%s" %d',
                                   ('local', 'GET', '/oas/state', '1.1', 200), None)
        original = record.getMessage()
        PasswordAccessFilter().filter(record)
        self.assertEqual(original, record.getMessage())

    def test_scheduler_sends_reason_once_and_stops_even_when_handle_error_enabled(self):
        tree = ast.parse((ROOT / 'script.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Script')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        from module import exception
        namespace = {name: getattr(exception, name) for name in dir(exception) if not name.startswith('_')}
        namespace.update(Path=Path, logger=Mock(), I18n=Mock(), exit=sys.exit,
                         load_module=Mock(side_effect=RequestHumanTakeover('账号密码错误')))
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'script.py', 'exec'), namespace)
        script = SimpleNamespace(_try_acquire_queue_token=lambda: True, instance_guard=None,
                                 device=Mock(), config=Mock(), config_name='test-only', save_error_log=Mock())
        with self.assertRaises(SystemExit) as raised:
            namespace['run'](script, 'Restart')
        self.assertEqual(raised.exception.code, 1)
        script.config.notifier.push.assert_called_once()
        self.assertEqual(script.config.notifier.push.call_args.kwargs['content'], '<test-only> 账号密码错误')
        script.save_error_log.assert_not_called()
        script.config.task_call.assert_not_called()


@unittest.skipUnless(os.environ.get('RESTART_SCREENSHOTS'), 'Set RESTART_SCREENSHOTS to the screenshot folder')
class OfficialScreenshotTest(unittest.TestCase):
    def image(self, stamp):
        with Image.open(Path(os.environ['RESTART_SCREENSHOTS']) / f'Screenshot_20261020-{stamp}.png') as image:
            return np.array(image.convert('RGB'))

    def test_all_pages_and_no_false_error(self):
        task = task_for_test()
        for stamp, expected in [('141332', task.I_OFFICIAL_LOGIN_ENTER),
                                ('141337', task.I_X7_SERVER_PAGE), ('141638', task.I_X7_SERVER_PAGE),
                                ('141346', task.I_OFFICIAL_ACCOUNT_PAGE), ('141401', task.I_OFFICIAL_ACCOUNT_PAGE)]:
            image = self.image(stamp)
            self.assertTrue(expected.match(image), stamp)
            self.assertEqual(task.I_OFFICIAL_ACCOUNT_ERROR.match(image), stamp == '141401')
        self.assertTrue(task.I_X7_LOGIN_AGREE.match(self.image('141332')))

    def test_server_ocr_and_click_coordinates(self):
        task = task_for_test()
        for stamp, zones in [('141337', [62, 48, 34, 26, 6]),
                             ('141638', [93, 92, 91, 90, 89, 88, 87, 86])]:
            task.device.image = self.image(stamp)
            task.appear.side_effect = lambda rule: rule.match(task.device.image)
            self.assertTrue(task.appear(task.I_OFFICIAL_SERVER_ACCOUNT_LABEL))
            entries = task.mine_entries() if stamp == '141337' else task.all_entries()
            self.assertEqual([e.zone for e in entries], zones)
            for e in entries:
                self.assertTrue(356 < e.x < 1188 and 286 < e.y < 645)
        task.device.image = self.image('141337')
        task.server_zone = None
        task.wait_page_leave = Mock(return_value=True)
        self.assertTrue(task.select_from_mine())
        click = task.device.click.call_args.kwargs
        self.assertTrue(509 < click['x'] < 809 and 173 < click['y'] < 221)


if __name__ == '__main__':
    unittest.main()
