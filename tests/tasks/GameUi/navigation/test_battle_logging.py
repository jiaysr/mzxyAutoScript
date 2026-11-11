# -*- coding: utf-8 -*-
"""公共战斗层日志降噪测试（不连设备）"""
from types import SimpleNamespace

from tasks.Battle import attack as attack_module
from tasks.Battle.attack import BattleAttack
from tasks.GameUi.navigation import battle as battle_module
from tasks.GameUi.navigation.battle import SimpleBattle


def test_target_name_logs_only_when_changed(monkeypatch):
    info = []
    debug = []
    monkeypatch.setattr(battle_module.logger, 'attr',
                        lambda name, text: info.append((name, text)))
    monkeypatch.setattr(battle_module.logger, 'debug',
                        lambda msg, *args: debug.append(msg % args if args else msg))

    class FakeOcr:
        text = '陵鱼'

        def detect_and_ocr(self, image, logDisplay=False):
            return [SimpleNamespace(ocr_text=self.text)]

    task = SimpleNamespace(
        device=SimpleNamespace(image=object()),
        exist_image=lambda: True,
        O_TARGET_NAME=FakeOcr(),
    )

    assert SimpleBattle.ui_target_name_direct(task) == '陵鱼'
    assert SimpleBattle.ui_target_name_direct(task) == '陵鱼'
    assert info == [('Target name', '陵鱼')]
    assert len(debug) == 1

    FakeOcr.text = '可爱雪人'
    assert SimpleBattle.ui_target_name_direct(task) == '可爱雪人'
    assert info == [('Target name', '陵鱼'), ('Target name', '可爱雪人')]


def test_relock_suppresses_click_log_and_keeps_whitelist_mismatch(monkeypatch):
    info = []
    debug = []
    monkeypatch.setattr(battle_module.logger, 'info',
                        lambda msg, *args: info.append(msg % args if args else msg))
    monkeypatch.setattr(battle_module.logger, 'debug',
                        lambda msg, *args: debug.append(msg % args if args else msg))

    class FakeDevice:
        def __init__(self):
            self.clicks = []

        def click(self, **kwargs):
            self.clicks.append(kwargs)

        def click_record_clear(self):
            pass

        def sleep(self, seconds):
            pass

    device = FakeDevice()
    task = SimpleNamespace(
        RELOCK_MAX_SWITCH=1,
        RELOCK_SETTLE=0.15,
        C_TARGET_BUTTON_CLICK=SimpleNamespace(coord=lambda: (1, 2)),
        device=device,
        screenshot=lambda: None,
        ui_target_attackable=lambda image=None: True,
        ui_target_name=lambda: '路人甲',
        ocr_name_match=lambda text, expected: text == expected,
    )

    assert SimpleBattle.battle_relock_target(task, ['可爱雪人']) is False
    assert device.clicks[0]['control_name'] == 'BATTLE_TARGET'
    assert device.clicks[0]['log'] is False
    assert any('不在白名单' in message for message in info)
    assert any('快速补锁没锁到目标' in message for message in debug)


def test_battle_skill_loop_config_logs_only_once(monkeypatch):
    attrs = []
    infos = []
    rules = []
    monkeypatch.setattr(attack_module.logger, 'attr',
                        lambda name, text: attrs.append((name, text)))
    monkeypatch.setattr(attack_module.logger, 'info',
                        lambda msg, *args: infos.append(msg % args if args else msg))
    monkeypatch.setattr(attack_module.logger, 'hr',
                        lambda title, level=3: rules.append(title))

    task = SimpleNamespace(
        battle_release_config=lambda: {
            'clicks': 2, 'click_interval': 0.1,
            'attempts': 3, 'cooldown_timeout': 1.2,
        },
        battle_use_normal_attack=lambda: False,
        screenshot=lambda: 'image',
    )

    for _ in range(2):
        assert BattleAttack.battle_skill_loop(
            task, [0], check_stop=lambda image: 'stop',
            include_attack=False, recheck_pending=False,
        ) == 'stop'

    assert [name for name, _ in attrs].count('释放节奏') == 1
    assert [name for name, _ in attrs].count('普通攻击') == 1
    assert rules.count('队列式释放技能') == 1
    assert sum('技能顺序' in message for message in infos) == 1
