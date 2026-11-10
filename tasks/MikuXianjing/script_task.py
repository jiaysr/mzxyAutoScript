# This Python file uses the following encoding: utf-8
"""
迷窟仙境（MikuXianjing）

与域外迷窟基本一致（同一个 NPC「迷窟守卫」进入），复用其进图 / NPC 对话 / 离场 /
锁目标 / 补锁 / 名字校验 / 回位 / 修理 / 原地复活逻辑，只覆盖：
- 地图「迷窟仙境」，初始落点 311,49，打怪基准 308,81，三个出口；
- 只打「修仙小妖 / 修仙刀手」；
- 没有时间时段：怪物一直刷新，打怪进度（562,572,69,46）到 100% 收工，
  按任务成功间隔（success_interval，默认 1 天）后再跑；
- 启用后一直打，检测到更高优先级任务到期时离场让路，稍后重排自己。
"""
import re
from datetime import datetime, timedelta

from module.base.timer import Timer
from module.exception import GameStuckError, TaskEnd
from module.logger import logger
from tasks.MikuXianjing.assets import MikuXianjingAssets
from tasks.YuwaiMimang.script_task import ScriptTask as MimangBase
from tasks.YuwaiMimang.script_task import suppress_control_input_log


class ScriptTask(MimangBase, MikuXianjingAssets):
    SCHEDULER_NAME = 'MikuXianjing'
    name = '迷窟仙境'

    MAP_NAME = '迷窟仙境'
    MAP_ENTER_POS = (311, 49)

    # 迷窟仙境小地图上的三个出口（实测 1280x720）：前两个在右上视图，第三个在底部视图
    LEAVE_EXITS = (
        (715, 151, 31, 32, 'top_right'),
        (377, 168, 31, 32, 'top_right'),
        (270, 540, 31, 32, 'bottom_right'),
    )

    # 进度到这么高就收工（百分比）
    PROGRESS_DONE = 100
    # 打怪中间隔这么久 OCR 一次进度（秒）
    PROGRESS_CHECK_INTERVAL = 5

    # 让路 / 配置检查 / 让路后重排（与野外雪人一致）
    YIELD_CHECK_INTERVAL = 5
    CONFIG_CHECK_INTERVAL = 30
    YIELD_RESUME_SECONDS = 5

    def run(self) -> None:
        logger.hr('迷窟仙境')
        cfg = self.current_config()
        self._yielded = False
        self._stopped_by_user = False
        self._yield_timer = Timer(self.YIELD_CHECK_INTERVAL).start()
        self._config_timer = Timer(self.CONFIG_CHECK_INTERVAL).start()
        self._progress_timer = Timer(self.PROGRESS_CHECK_INTERVAL).start()
        self.config.start_watching()

        success = False
        try:
            self.prepare_game()
            if not self.enter_mimang(cfg):
                raise GameStuckError('Cannot enter 迷窟仙境')
            reason = self.progress_battle_loop(cfg)
            if reason == 'yield':
                self._yielded = True
            elif reason == 'disabled':
                self._stopped_by_user = True
            elif reason == 'complete':
                success = True
        finally:
            # 不管收工、让路还是报错，都要离场，别把角色留在迷窟仙境里
            try:
                self.leave_mimang(cfg)
            except Exception as e:  # noqa: BLE001
                logger.warning(f'Leave 迷窟仙境 failed: {e}')
            # 一次性跑完（进度100%）：回沼泽后找 NPC 交付仙心一体，别等下次进图
            if success:
                try:
                    self.deliver_quest_after_battle(cfg)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f'交付仙心一体异常: {e}')
            if self._yielded or self._stopped_by_user:
                # 让路 / 被 UI 关闭：稍后重排自己，等其它任务跑完再继续
                resume_at = datetime.now() + timedelta(seconds=self.YIELD_RESUME_SECONDS)
                logger.attr('恢复时间', resume_at.strftime('%H:%M:%S'))
                self.set_next_run(task='MikuXianjing', target=resume_at,
                                  success=None, finish=True, server=False)
            else:
                self.set_next_run(task='MikuXianjing', success=success, finish=True)
        raise TaskEnd('MikuXianjing')

    # ---------------------------------------------------------------- 配置
    def current_config(self):
        return self.config.miku_xianjing.miku_xianjing_config

    def yuwai_battle_config(self):
        """继承的 battle_target_names / battle_task_preset 都从这里读，改指向本任务配置"""
        return self.config.miku_xianjing.battle_config

    # ---------------------------------------------------------------- 仙心一体任务
    # NPC 处除入口外还能接的每日任务（打 100 只修仙小妖；地图进度 100% 时必然已完成）
    QUEST_NAME = '仙心一体'

    def click_enter_mimang(self, enter_text: str, enter_wait: int = 2) -> bool:
        """进图前先幂等处理仙心一体（让路重进不会重复接取），再点入口"""
        try:
            self.handle_xianxin_quest()
        except Exception as e:  # noqa: BLE001
            logger.warning(f'仙心一体处理异常，继续进图: {e}')
        return super().click_enter_mimang(enter_text, enter_wait)

    def handle_xianxin_quest(self) -> None:
        """
        幂等处理「仙心一体」：每次现读 NPC 弹窗里该选项的标记——
        黄! 可接取 -> 点选项点「接受」；灰? 进行中 -> 跳过；黄? 完成 -> 点选项点「完成」交付；
        选项不存在 -> 视为已完成，跳过。让路→重进→再谈 NPC 不会重复接取。
        """
        box = self.locate_quest_option(self.QUEST_NAME)
        if box is None:
            logger.info('NPC 处没有仙心一体选项，视为已完成，跳过')
            return
        state = self.classify_quest_marker(box)
        if state == 'in_progress':
            logger.info('仙心一体进行中，不重复接取')
            return
        if state is None:
            logger.warning('仙心一体标记读不出，跳过接取/交付')
            return

        cx, cy = int((box[0] + box[2]) / 2), int((box[1] + box[3]) / 2)
        expected = ['接受'] if state == 'available' else ['完成', '领取']
        # 非选中项第一下只选中、第二下才弹详情：点选项后试动作按钮，没弹就再点一下
        ok = False
        for _ in range(2):
            self.device.click(x=cx, y=cy, control_name='miku_quest_option')
            self.device.click_record_clear()
            self.device.sleep(0.5)
            ok = self.click_quest_button(self.O_QUEST_ACTION, expected)
            if ok:
                break
        action = '接取' if state == 'available' else '交付'
        logger.info(f'仙心一体{action}' + ('成功' if ok else '失败'))
        self.back_to_npc_options()

    def locate_quest_option(self, name: str):
        """OCR 定位 NPC 弹窗里 name 选项的文字框（全屏坐标）；翻遍上下都没有返回 None"""
        rule = self.O_NPC_DIALOG_OPTIONS
        for direction in (None, True, False):
            if direction is not None and not self.scroll_dialog_options(down=direction):
                continue
            self.screenshot()
            for item in rule.detect_and_ocr(self.device.image, logDisplay=False):
                text = item.ocr_text.strip()
                if not text or not self.ocr_name_match(text, name):
                    continue
                xs = [p[0] for p in item.box]
                ys = [p[1] for p in item.box]
                x0, y0 = rule.roi[0], rule.roi[1]
                return (min(xs) + x0, min(ys) + y0, max(xs) + x0, max(ys) + y0)
        return None

    def classify_quest_marker(self, box):
        """判定选项左侧标记：available(黄!) / in_progress(灰?) / complete(黄?)；读不出 None"""
        x0, y0, x1, y1 = box
        roi = (int(x0) - 40, int(y0) - 6, 36, int(y1 - y0) + 12)
        self.screenshot()
        image = self.device.image
        for state, template in (('available', self.I_QUEST_AVAILABLE),
                                ('in_progress', self.I_QUEST_PROGRESS),
                                ('complete', self.I_QUEST_DONE)):
            template.roi_back = roi
            if template.match(image):
                return state
        return None

    def click_quest_button(self, rule, expected: list) -> bool:
        """OCR 详情弹窗按钮区，命中 expected 文案就点它（接受 / 完成 / 返回）"""
        self.screenshot()
        for item in rule.detect_and_ocr(self.device.image, logDisplay=False):
            text = item.ocr_text.strip()
            if not any(self.ocr_name_match(text, e) for e in expected):
                continue
            xs = [p[0] for p in item.box]
            ys = [p[1] for p in item.box]
            cx = int((min(xs) + max(xs)) / 2) + rule.roi[0]
            cy = int((min(ys) + max(ys)) / 2) + rule.roi[1]
            self.device.click(x=cx, y=cy, control_name='miku_quest_button')
            self.device.click_record_clear()
            self.device.sleep(0.5)
            return True
        return False

    def back_to_npc_options(self) -> None:
        """接取/交付后回到 NPC 选项弹窗（正常会自动回；仍停详情就点返回）"""
        for _ in range(2):
            self.screenshot()
            if self.appear(self.I_NPC_DIALOG_X):
                return
            if not self.click_quest_button(self.O_QUEST_RETURN, ['返回']):
                return

    def deliver_quest_after_battle(self, cfg) -> None:
        """
        进度 100% 一次性跑完时，进图接取的任务此刻已完成；离场回沼泽后
        再找 NPC 交付（handle_xianxin_quest 幂等：黄? 交付 / 已交付或未完成则跳过）。
        """
        self.ui_goto_main_page()
        if not self.open_minimap_and_click_npc(cfg.npc_name, cfg.dialog_timeout):
            logger.warning('交付仙心一体：NPC 对话没出现，跳过')
            return
        self.handle_xianxin_quest()
        self.close_leftover_dialog()

    # ---------------------------------------------------------------- 打怪
    def progress_battle_loop(self, cfg) -> str:
        """
        无时段打怪：锁白名单目标 -> 按预设释放技能 -> 目标丢失换下一只，
        直到进度 100%（complete）/ 让路（yield）/ 被关闭（disabled）。

        复用域外迷窟的锁目标 / 补锁 / 名字校验 / 回位 / 修理；每帧现查目标名，
        避免怪死后游戏自动转锁到路过玩家时打偏。
        """
        coord = self.parse_coord(cfg.battle_coord)
        tolerance = int(cfg.battle_tolerance)
        names = self.battle_target_names()
        include_attack = self.battle_use_normal_attack()
        preset = self.battle_preset()
        logger.hr(f'迷窟仙境打怪（{preset["name"]}）')
        logger.attr('打怪配置', f'目标={names or "不限"} 基准={coord} 容差={tolerance}')

        if not self.recover_position(coord, tolerance, timeout=60):
            logger.warning(f'Cannot reach battle position {coord}, continue anyway')
        self.ui_close_menu()
        self.device.sleep(0.5)

        slots = []
        progress = {'casts': 0, 'last_repair': datetime.now()}
        # 无时段截止：修理随时可做（need_repair 的剩余时间检查恒成立）
        repair_deadline = datetime.now() + timedelta(days=1)

        def on_cast(_casts: int) -> None:
            progress['casts'] += 1
            if progress['casts'] % self.RECOVER_EVERY_CASTS:
                return
            self.recover_in_battle(coord, tolerance)
            if self.need_repair(cfg, progress['last_repair'], repair_deadline):
                self.repair_in_battle(coord, tolerance)
                progress['last_repair'] = datetime.now()
            logger.attr('打怪进度', f'casts={progress["casts"]}')

        with suppress_control_input_log():
            while True:
                self.screenshot()
                self.reset_records()
                stop = self.battle_stop_reason(self.device.image)
                if stop:
                    return stop
                if not self.acquire_target(names):
                    self.device.sleep(1)
                    continue

                if not slots:
                    slots = self.recognize_skill_slots()
                    if not slots:
                        if self.battle_has_pending_slots():
                            logger.info('技能位都在冷却中未识别，等冷却结束后重认')
                        else:
                            logger.warning('技能位一直认不出来（技能栏被挡 / 素材不匹配），迷窟仙境收工')
                            return 'no_slots'
                slots = self.battle_current_slots() or slots

                reason = self.battle_skill_loop(
                    slots,
                    check_stop=self.battle_stop_reason,
                    target_check=lambda image: self.battle_target_ok(names, image),
                    include_attack=include_attack,
                    attack_interval=self.NORMAL_ATTACK_INTERVAL,
                    poll_interval=self.SKILL_COOLDOWN_POLL,
                    on_cast=on_cast,
                    on_target_lost=self.battle_on_target_lost(names))
                if reason in ('complete', 'yield', 'disabled'):
                    return reason
                self.device.sleep(0.2)

    def battle_stop_reason(self, image=None):
        """
        停止条件（battle_skill_loop 每轮都会调）：
        进度 100% -> complete / 更高优先级到期 -> yield / 被 UI 关闭 -> disabled。
        进度按 PROGRESS_CHECK_INTERVAL 节流 OCR。
        """
        self.reset_records()
        if self._progress_timer.reached_and_reset():
            percent = self.read_progress(image)
            if percent is not None:
                logger.attr('迷窟仙境进度', f'{percent}%')
                if percent >= self.PROGRESS_DONE:
                    logger.info('进度 100%，迷窟仙境完成')
                    return 'complete'
        if self.stop_for_others():
            return 'yield'
        if self.stop_because_disabled():
            return 'disabled'
        return None

    def read_progress(self, image=None):
        """OCR 进度区域（562,572,69,46）里的百分比数字；读不到返回 None"""
        image = image if image is not None else self.device.image
        text = self.O_PROGRESS.ocr(image)
        if not text:
            return None
        match = re.search(r'\d+', str(text))
        return int(match.group()) if match else None

    # ---------------------------------------------------------------- 让路 / 关闭
    def stop_for_others(self) -> bool:
        """检测到更高优先级的任务到期时让路"""
        if not self._yield_timer.reached_and_reset():
            return False
        if self.higher_priority_task_due():
            logger.info('检测到更高优先级任务到期，迷窟仙境让路')
            return True
        return False

    def stop_because_disabled(self) -> bool:
        """运行中检测 UI 是否关闭了本任务"""
        if not self._config_timer.reached_and_reset():
            return False
        if not self.config.should_reload():
            return False
        self.config.reload()
        self.config.start_watching()
        self.battle_config_cache_clear()
        if not self.config.miku_xianjing.scheduler.enable:
            logger.info('迷窟仙境已在配置中关闭，停止任务')
            return True
        return False


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    try:
        ScriptTask(config, device).run()
    except TaskEnd:
        pass