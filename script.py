# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

from functools import wraps
import zerorpc
import zmq
import msgpack
import random
import re
import cv2
import time
import os
import inflection
import asyncio
import json

from datetime import date
import threading
from typing import Callable
from datetime import datetime, timedelta
from pathlib import Path
from cached_property import cached_property
from pydantic import BaseModel, ValidationError
from threading import Thread
from multiprocessing.queues import Queue


from module.config.utils import convert_to_underscore
from module.config.config import Config
from module.config.config_model import ConfigModel
from module.config.instance_guard import InstanceGuard
from module.config.anti_ban import AntiBanGuard
from module.device.device import Device
from module.device.env import IS_WINDOWS
from module.base.utils import load_module
from module.base.decorator import del_cached_property
from module.logger import logger
from module.exception import *
from module.server.i18n import I18n
from module.ocr.rpc import ensure_ocr_server_started



_log_switch_lock = threading.Lock()#线程锁


class Script:
    # 任务因「游戏没运行」被中断时，Restart 拉起后重跑的次数上限（防游戏反复闪退时死循环）
    GAME_NOT_RUNNING_RETRY = 2

    def __init__(self, config_name: str ='oas') -> None:
        logger.hr('Start', level=0)
        self.server = None
        self.state_queue: Queue = None
        self._emulator_down = False
        self.gui_update_task: Callable = None  # 回调函数, gui进程注册当每次config更新任务的时候更新gui的信息
        self.config_name = config_name
        # Skip first restart
        self.is_first_task = True
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.failure_record = {}
        # 运行loop的线程
        self.loop_thread: Thread = None
        # 跨进程排队管理器（仅在 queue_mode=True 时初始化）
        self.instance_guard: InstanceGuard = None
        self.anti_ban_guard: AntiBanGuard = AntiBanGuard()
        # 因「游戏没运行」被中断的任务 -> 连续中断次数（见 _handle_game_not_running）
        self.game_not_running_retry = {}
        # 本次 run() 是否被「游戏没运行」中断（用于区分正常结束）
        self.game_not_running_aborted = False

    @cached_property
    def config(self) -> "Config":
        try:
            from module.config.config import Config
            config = Config(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self) -> "Device":
        try:
            from module.device.device import Device
            device = Device(config=self.config)
            return device
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def checker(self):
        """
        占位函数，在alas中是检查服务器是否正常的
        :return:
        """
        return None

    def save_error_log(self):
        """
        Save last 60 screenshots in ./log/error/<timestamp>
        Save logs to ./log/error/<timestamp>/log.txt
        """
        from module.base.utils import save_image
        from module.handler.sensitive_info import (handle_sensitive_image,
                                                   handle_sensitive_logs)
        if self.config.script.error.save_error:
            if not os.path.exists('./log/error'):
                os.mkdir('./log/error')
            folder_name = str(int(time.time() * 1000))
            folder = f'./log/error/{folder_name}'
            logger.warning(f'Saving error: {folder}')
            logger.info('保存详细错误的日志和截图到路径:')
            logger.info(f'{str( Path.cwd() / "log" / "error" / folder_name)}')
            os.mkdir(folder)
            for data in self.device.screenshot_deque:
                image_time = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
                image = handle_sensitive_image(data['image'])
                save_image(image, f'{folder}/{image_time}.png')
            with open(logger.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                start = 0
                for index, line in enumerate(lines):
                    line = line.strip(' \r\t\n')
                    if re.match('^═{15,}$', line):
                        start = index
                lines = lines[start - 2:]
                lines = handle_sensitive_logs(lines)
            with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
                f.writelines(lines)

    def init_server(self, port: int) -> int:
        """
        初始化zerorpc服务，返回端口号
        :return:
        """
        self.server = zerorpc.Server(self)
        try:
            self.server.bind(f'tcp://127.0.0.1:{port}')
            return port
        except zmq.error.ZMQError:
            logger.error(f"Ocr server cannot bind on port {port}")
            return None

    def run_server(self) -> None:
        """
        启动zerorpc服务
        :return:
        """
        self.server.run()

    def gui_args(self, task: str) -> str:
        """
        获取给gui显示的参数
        :return:
        """
        return self.config.gui_args(task=task)

    def gui_menu(self) -> str:
        """
        获取给gui显示的菜单
        :return:
        """
        return self.config.gui_menu

    def gui_task(self, task: str) -> str:
        """
        获取给gui显示的任务 的参数的具体值
        :return:
        """
        return self.config.model.gui_task(task=task)

    def gui_set_task(self, task: str, group: str, argument: str, value) -> bool:
        """
        设置给gui显示的任务 的参数的具体值
        :return:
        """
        # 验证参数
        task = convert_to_underscore(task)
        group = convert_to_underscore(group)
        argument = convert_to_underscore(argument)
        is_credential = (task == 'restart' and group == 'login_official_config'
                         and argument in ('account', 'password'))
        is_password = argument == 'password'
        # pandtic验证
        if not is_credential and isinstance(value, str):
            if len(value) == 8:
                try:
                    value = datetime.strptime(value, '%H:%M:%S').time()
                except ValueError:
                    pass

        log_value = '[REDACTED]' if is_password else value
        path = f'{task}.{group}.{argument}'
        task_object = getattr(self.config.model, task, None)
        group_object = getattr(task_object, group, None)
        argument_object = getattr(group_object, argument, None)

        if argument_object is None:
            logger.error(f'Set arg {task}.{group}.{argument}.{log_value} failed')
            return False

        try:
            setattr(group_object, argument, value)
            argument_object = getattr(group_object, argument, None)
            log_value = '[REDACTED]' if is_password else argument_object
            logger.info(f'Set arg {task}.{group}.{argument}.{log_value}')
            self.config.save()  # 我是没有想到什么方法可以使得属性改变自动保存的
            return True
        except ValidationError as e:
            logger.error(f'Set arg {task}.{group}.{argument} validation failed [REDACTED]' if is_password else e)
            return False

    @zerorpc.stream
    def gui_mirror_image(self):
        """
        获取给gui显示的镜像
        :return: cv2的对象将 numpy 数组转换为字节串。接下来MsgPack 进行序列化发送方将图像数据转换为字节串
        """
        # return msgpack.packb(cv2.imencode('.jpg', self.device.screenshot())[1].tobytes())
        img = cv2.cvtColor(self.device.screenshot(), cv2.COLOR_RGB2BGR)
        self.device.stuck_record_clear()
        ret, buffer = cv2.imencode('.jpg', img)
        yield buffer.tobytes()

    def _gui_update_tasks(self) -> None:
        """
        获取更新任务后 pending waiting 的任务 和 当前的任务的数据。打包给gui显示
        :return:
        """
        data = {}
        pending = []
        waiting = []
        task = {}
        if self.config.task is not None and self.config.task.next_run < datetime.now():
            task["name"] = self.config.task.command
            task["next_run"] = str(self.config.task.next_run)
        data["task"] = task

        for p in self.config.pending_task[1:]:
            item = {"name": p.command, "next_run": str(p.next_run)}
            pending.append(item)

        for w in self.config.waiting_task:
            item = {"name": w.command, "next_run": str(w.next_run)}
            waiting.append(item)


        data["pending"] = pending
        data["waiting"] = waiting

        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def _gui_set_status(self, status: str) -> None:
        """
        设置给gui显示的状态
        :param status: 可以在gui中显示的状态 有 "Init", "Empty"(不显示), "Run"(运行中), "Error", "Free"(空闲)
        :return:
        """
        data = {"status": status}
        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def gui_task_list(self) -> str:
        """
        获取给gui显示的任务列表
        :return:
        """
        result = {}
        for key, value in self.config.model.dict().items():
            if isinstance(value, str):
                continue
            if key == "restart":
                continue
            if "scheduler" not in value:
                continue

            scheduler = value["scheduler"]
            item = {"enable": scheduler["enable"],
                    "next_run": str(scheduler["next_run"])}
            key = self.config.model.type(key)
            result[key] = item
        return json.dumps(result)

    def _release_token_before_wait(func):
        @wraps(func)
        def wrapper(self, future):
            if self.instance_guard and self.instance_guard.should_release(
                pending_task=self.config.pending_task,
                waiting_task=self.config.waiting_task,
                idle_threshold_minutes=self.config.script.optimization.queue_idle_threshold
            ):
                self.instance_guard.release()
            return func(self, future)
        return wrapper

    @_release_token_before_wait
    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed.
        """
        future = future + timedelta(seconds=1)
        self.config.start_watching()
        while 1:
            if datetime.now() > future:
                return True
            # if self.stop_event is not None:
            #     if self.stop_event.is_set():
            #         logger.info("Update event detected")
            #         logger.info(f"[{self.config_name}] exited. Reason: Update")
            #         exit(0)

            time.sleep(5)

            if self.config.should_reload():
                return False

    def get_next_task(self) -> str:
        """
        获取下一个任务的名字, 大驼峰。
        :return:
        """
        while True:
            task = self.config.get_next()
            self.config.task = task
            if self.state_queue:
                self.state_queue.put({"schedule": self.config.get_schedule_data()})
            now = datetime.now()
            antiban_wake = self.anti_ban_guard.wake_time(now, self.config.script.anti_ban)
            if antiban_wake is not None:
                task.next_run = max(task.next_run, antiban_wake)
            if not self._try_acquire_queue_token():
                del_cached_property(self, "config")
                continue
            # 任务时间到了返回任务名称
            if task.next_run <= now:
                return task.command
            # 根据策略执行等待逻辑
            if not self._handle_wait_during_idle(task.next_run):
                # 若等待被打断, 则刷新配置
                del_cached_property(self, "config")

    def _try_acquire_queue_token(self) -> bool:
        """
        尝试获取排队执行权。
        如果排队模式未启用，返回 True。
        如果排队模式启用但未能获取到执行权，进入等待循环直到获取成功或配置变更。

        Returns:
            True: 获取得执行权，可以执行任务
            False: 等待被配置变更打断，调用方应重新加载配置后重试
        """
        # 是否开启排队模式
        if not self.config.script.optimization.queue_mode:
            if self.instance_guard:
                self.instance_guard.remove_from_queue()
                self.instance_guard = None
            return True

        # 懒加载instance_guard
        if self.instance_guard is None:
            try:
                self.instance_guard = InstanceGuard(self.config_name)
                logger.info(f"[Queue] Queue mode enabled for '{self.config_name}'")
            except Exception:
                self.instance_guard = None
                return True

        # 尝试获取执行权
        if self.instance_guard.try_acquire():
            return True

        # 执行权获取失败，关闭模拟器并进入等待循环
        logger.info(f"[Queue] '{self.config_name}' waiting for execution token...")
        if (self.config.script.optimization.when_task_queue_empty == 'close_game'
                and not self._emulator_down
                and 'device' in self.__dict__):
            try:
                self.device.emulator_stop()
                self._emulator_down = True
                logger.info(f"[Queue] Emulator closed during queue wait")
            except Exception:
                pass
        self.config.start_watching()
        while True:
            time.sleep(30)

            if self.config.should_reload():
                logger.info(f"[Queue] Config changed, re-evaluating")
                return False

            if self.instance_guard.try_acquire():
                return True

    def _handle_wait_during_idle(self, next_run: datetime) -> bool:
        """
        处理任务空闲期间的行为策略
        :param next_run: 下一个任务的时间
        :return: True 表示等待成功完成, False 表示等待被中断
        """
        method = self.config.script.optimization.when_task_queue_empty
        strategy_map = {
            "close_game": self._wait_close_game,
            # 挂机：不做任何游戏内操作（含老配置的 stay_there）
            "goto_main": self._wait_idle,
            "stay_there": self._wait_idle,
        }
        func = strategy_map.get(method)
        if func is None:
            logger.warning(f"Invalid Optimization_WhenTaskQueueEmpty: {method}, fallback to idle")
            func = self._wait_idle
        return func(next_run)

    @staticmethod
    def _time_to_timedelta(value) -> timedelta:
        if value is None:
            return timedelta(0)
        return timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)

    def _wait_until_with_emulator_preheat(self, next_run: datetime) -> bool:
        """Wait until next_run; if emulator is down, preheat startup before next task."""
        if not self._emulator_down:
            return self.wait_until(next_run)

        startup_lead = self._time_to_timedelta(self.config.script.optimization.emulator_startup_lead_time)
        now = datetime.now()
        wake_time = next_run - startup_lead if startup_lead > timedelta(0) else next_run
        if wake_time < now:
            wake_time = now

        now = datetime.now()
        if wake_time > now:
            logger.info(f"Wait before wake emulator: {wake_time.strftime('%Y-%m-%d %H:%M:%S')}")
            if not self.wait_until(wake_time):
                return False

        if self._emulator_down:
            logger.info("Wake emulator before next task")
            if not self._try_acquire_queue_token():
                return False
            self.device = Device(self.config)
            self._emulator_down = False

        if wake_time < next_run:
            return self.wait_until(next_run)
        return True

    def _wait_close_game(self, next_run: datetime) -> bool:
        if self._emulator_down:
            logger.info("Emulator is down, skip close_game/goto_main action and wait with preheat")
            return self._wait_until_with_emulator_preheat(next_run)

        close_game_wait_duration = self.config.script.optimization.close_game_wait_duration
        close_game_wait = self._time_to_timedelta(close_game_wait_duration)
        close_emulator_wait_duration = self.config.script.optimization.close_emulator_wait_duration
        close_emulator_wait = self._time_to_timedelta(close_emulator_wait_duration)

        if close_emulator_wait > timedelta(0) and next_run > datetime.now() + close_emulator_wait:
            logger.info("Close emulator during wait")
            self.device.emulator_stop()
            self._emulator_down = True

            if not self._wait_until_with_emulator_preheat(next_run):
                return False

            self.run("Restart")
            return True

        if close_game_wait <= timedelta(0):
            logger.info("Close game during wait")
            self.device.app_stop()
            self.device.release_during_wait()
            if not self.wait_until(next_run):
                return False
            self.run("Restart")
            return True

        if next_run > datetime.now() + close_game_wait:
            logger.info("Close game during wait")
            self.device.app_stop()
            self.device.release_during_wait()
            if not self.wait_until(next_run):
                return False
            self.run("Restart")
            return True

        logger.info("Wait without closing game (close_game wait duration not reached)")
        self.device.release_during_wait()
        if not self.wait_until(next_run):
            return False
        return True

    def _wait_idle(self, next_run: datetime) -> bool:
        """
        挂机：不做任何游戏内操作，只保持当前画面等待
        等待时间较长时可按配置关闭模拟器省资源
        """
        if self._emulator_down:
            logger.info("Idle during wait (emulator is down, with preheat)")
            return self._wait_until_with_emulator_preheat(next_run)

        close_emulator_wait_duration = self.config.script.optimization.close_emulator_wait_duration
        close_emulator_wait = self._time_to_timedelta(close_emulator_wait_duration)
        if close_emulator_wait > timedelta(0) and next_run > datetime.now() + close_emulator_wait:
            logger.info("Close emulator during wait")
            self.device.emulator_stop()
            self._emulator_down = True

            if not self._wait_until_with_emulator_preheat(next_run):
                return False

            self.run("Restart")
            return True

        logger.info("Idle (no action) during wait")
        self.device.release_during_wait()
        return self.wait_until(next_run)

    def run(self, command: str) -> bool:
        """
        :param command:  大写驼峰命名的任务名字
        :return:
        """
        if command == 'start' or command == 'goto_main':
            logger.error(f'Invalid command `{command}`')

        if not self._try_acquire_queue_token():
            return False

        if self.instance_guard and self.instance_guard.token_lost:
            logger.warning(f'Token lost, stopping emulator and rejoining queue')
            try:
                self.device.emulator_stop()
            except Exception:
                pass
            self._emulator_down = True
            self.instance_guard.release()
            return False

        try:
            self.device.screenshot()
            module_name = 'script_task'
            module_path = str(Path.cwd() / 'tasks' / command / (module_name+'.py'))
            logger.info(f'module_path: {module_path}, module_name: {module_name}')
            task_module = load_module(module_name, module_path)
            task_module.ScriptTask(config=self.config, device=self.device).run()
        except TaskEnd:
            return True
        except TaskFailed as e:
            logger.error(e)
            return False
        except GameNotRunningError as e:
            logger.warning(e)
            self._handle_game_not_running(command)
            return True
        except (GameStuckError, GameTooManyClickError) as e:
            logger.error(e)
            self.save_error_log()
            logger.warning(f'Game stuck, {self.device.package} will be restarted in 10 seconds')
            logger.warning('If you are playing by hand, please stop Alas')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> GameStuckError or GameTooManyClickError")
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GameBugError as e:
            logger.warning(e)
            self.save_error_log()
            logger.warning('An error has occurred in game client, unable to handle')
            logger.warning(f'Restarting {self.device.package} to fix it')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GamePageUnknownError as e:
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            # 这个还不重要 留着坑填
            logger.critical('Game page unknown')
            self.save_error_log()
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> GamePageUnknownError")
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except ScriptError as e:
            logger.critical(e)
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> ScriptError")
            exit(1)
        except RequestHumanTakeover as e:
            logger.critical(e)
            logger.critical('Request human takeover')
            reason = str(e) or 'RequestHumanTakeover'
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> {reason}")
            exit(1)
        except Exception as e:
            logger.exception(e)
            self.save_error_log()
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> Exception occured")
            exit(1)

    def _handle_game_not_running(self, command: str) -> None:
        """
        任务因为游戏没运行被中断：先排 Restart 拉起游戏，再把本任务重新排上

        这不是任务本身的失败——它一步都还没执行。但很多任务的 finally 会无条件
        set_next_run（AutoTask 就把自己排到了第二天的同一时刻），只排 Restart
        不重排本任务的话，这个任务当天就丢了（实测 2026-10-23 00:20 自动任务）。

        连续中断超过 GAME_NOT_RUNNING_RETRY 次就放弃本次，避免游戏反复闪退时死循环。
        """
        self.config.task_call('Restart')
        retry = self.game_not_running_retry.get(command, 0) + 1
        self.game_not_running_retry[command] = retry
        self.game_not_running_aborted = True
        if retry > self.GAME_NOT_RUNNING_RETRY:
            logger.error(f'Task `{command}` gave up: game not running {retry} times')
            return
        logger.info(f'Task `{command}` will run again after Restart '
                    f'({retry}/{self.GAME_NOT_RUNNING_RETRY})')
        self.config.task_call(command)

    def _ensure_game_running_on_startup(self) -> None:
        """
        启动预检：游戏没运行就先启动并登录，再交给调度跑任务。

        原先只有任务第一次调用 `ui_get_current_page()` 时才会发现游戏没开
        （`GameUi` 的 app_check 是惰性的），第一个任务会在模拟器桌面上空跑一轮
        才报错。这里在第一次真正执行任务前主动查一次。

        检查本身失败（设备层/adb 异常等）只告警不阻塞启动：交给任务流程按原样
        报错，避免把整个脚本进程拦在启动阶段。
        """
        try:
            running = self.device.app_is_running()
        except Exception as e:  # noqa: BLE001
            logger.warning(f'启动预检：无法确认游戏运行状态（{e}），跳过预检')
            return
        if running:
            logger.info('启动预检：游戏已在运行')
            return

        logger.warning('启动预检：游戏未运行，先启动并登录')
        try:
            from tasks.Restart.script_task import ScriptTask as RestartTask
            RestartTask(self.config, self.device).app_restart()
            logger.info('启动预检：游戏已启动并登录')
        except Exception as e:  # noqa: BLE001
            # 启动/登录失败不在这里退出：交给任务流程记录失败并按调度重试
            logger.error(f'启动预检：启动游戏失败（{e}）')

    def loop(self):
        """
        Main loop of scheduler.
        :return:
        """
        with _log_switch_lock:
            logger.set_file_logger(self.config_name, do_cleanup=True)
        start_day = date.today()
        logger.info(f'Start scheduler loop: {self.config_name}')
        self.config.model.running_task = ''
        self.anti_ban_guard.reset()

        # Update GUI 防呆, 读取设置并立刻显示后台模拟器到前台
        if not self.config.script.device.run_background_only and IS_WINDOWS:
            from module.device.platform2.platform_windows import minimize_by_name, show_window_by_name
            target_window_name = self.config.script.device.handle  # 在这里输入你的具体窗口名称
            if self.config.script.device.emulator_window_minimize:
                minimize_by_name(target_window_name)
                logger.info(f'重新显示: {target_window_name}')
            else:
                show_window_by_name(target_window_name)
                
        while 1:
            if date.today() > start_day:
                with _log_switch_lock:
                    logger.set_file_logger(self.config_name, do_cleanup=True)
                start_day = date.today()
            # Check update event from GUI
            # if self.stop_event is not None:
            #     if self.stop_event.is_set():
            #         logger.info("Update event detected")
            #         logger.info(f"Alas [{self.config_name}] exited.")
            #         break

            # Check game server maintenance
            # self.checker.wait_until_available()
            # if self.checker.is_recovered():
            #     # There is an accidental bug hard to reproduce
            #     # Sometimes, config won't be updated due to blocking
            #     # even though it has been changed
            #     # So update it once recovered
            #     del_cached_property(self, 'config')
            #     logger.info('Server or network is recovered. Restart game client')
            #     self.config.task_call('Restart')

            # Get task
            task = self.get_next_task()
            # Skip first restart
            if self.is_first_task and task == 'Restart':
                logger.info('Skip task `Restart` at scheduler start')
                self.config.task_delay(task='Restart', success=True, server=True)
                del_cached_property(self, 'config')
                continue

            if self._emulator_down:
                self.device = Device(self.config)
                self._emulator_down = False
            else:
                _ = self.device

            # 启动预检：第一次跑任务前确认游戏在运行（没运行就先启动并登录）
            if self.is_first_task:
                self._ensure_game_running_on_startup()

            # Run
            logger.info(f'Scheduler: Start task `{task}`')
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            logger.hr(task, level=0)
            self.config.model.running_task = task
            _task_start = datetime.now()
            command = inflection.camelize(task)
            self.game_not_running_aborted = False
            success = self.run(command)
            if success and not self.game_not_running_aborted:
                # 正常跑完（不是被「游戏没运行」中断）：清掉该任务的重试计数
                self.game_not_running_retry.pop(command, None)
            self.config.model.running_task = ''
            logger.info(f'Scheduler: End task `{task}`')
            self.is_first_task = False
            self.anti_ban_guard.record_active((datetime.now() - _task_start).total_seconds())

            # Check failures
            # failed = deep_get(self.failure_record, keys=task, default=0)
            failed = self.failure_record[task] if task in self.failure_record else 0
            failed = 0 if success else failed + 1
            # deep_set(self.failure_record, keys=task, value=failed)
            self.failure_record[task] = failed
            if failed >= 3:
                logger.critical(f"Task `{task}` failed 3 or more times.")
                logger.critical("Possible reason #1: You haven't used it correctly. "
                                "Please read the help text of the options.")
                logger.critical("Possible reason #2: There is a problem with this task. "
                                "Please contact developers or try to fix it yourself.")
                logger.critical('Request human takeover')
                # 添加失败三次的推送通知
                self.config.notifier.push(
                    title=f'{I18n.trans_zh_cn(task)}{task}',
                    content=f"<{self.config_name}> 任务连续失败三次，请上线查看"
                )
                # 关闭模拟器
                if self.config.script.error.error_repeated:
                    self.device.emulator_stop()
                exit(1)

            if success:
                del_cached_property(self, 'config')
                continue
            elif self.config.script.error.handle_error:
                # self.config.task_delay(success=False)
                del_cached_property(self, 'config')
                # self.checker.check_now()
                continue
            else:
                break

    def start_loop(self) -> None:
        """
        创建一个线程，运行loop
        :return:
        """
        if self.loop_thread is None:
            self.loop_thread = Thread(target=self.loop, name='Script_loop')
            self.loop_thread.start()


if __name__ == "__main__":
    ensure_ocr_server_started()
    script = Script("oas1")
    script.loop()
