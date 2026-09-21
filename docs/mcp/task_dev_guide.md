# OAS 任务开发规范

本文档面向使用 MCP 辅助开发 OAS 任务的 AI 与开发者。写代码前先读本文，再参考 `tasks/` 下的相似任务。

## 1. 任务目录结构

```
tasks/<TaskName>/
  config.py        # 任务参数定义（pydantic 模型）
  script_task.py   # 任务逻辑（ScriptTask 类 + __main__ 单跑入口）
  assets.py        # 由 dev_tools/assets_extract.py 自动生成，禁止手改
  res/             # 规则 JSON 与素材 PNG
    image.json     # RuleImage
    click.json     # RuleClick
    long_click.json
    swipe.json
    ocr.json
    list.json      # 单个 dict，不是数组
    res_xxx.png    # 素材图片（与规则 imageName 对应）
```

规则 JSON 的类型靠字段特征识别（`AssetsExtractor`）：`imageName` → image，`keyword` → ocr，
`duration` → long_click，`mode` → swipe，含 `name`+`list` 的 dict → list，其余 → click。

命名约定：任务目录用大驼峰（`WorldBoss`），参数类同名，配置访问用下划线（`self.config.world_boss`）。

## 2. 规则类型与常量

`assets.py` 中的常量前缀由 `itemName` 转大写生成：

| 类型 | 设备端字段（res JSON） | 常量前缀 | 示例 |
| --- | --- | --- | --- |
| image | `itemName` `imageName` `roiFront` `roiBack` `method` `threshold` `description` | `I_` | `I_CHALLENGE_PAGE` |
| click | `itemName` `roiFront` `roiBack` `description` | `C_` | `C_ACTIVITY_MENU` |
| long_click | `itemName` `roiFront` `roiBack` `duration` `description` | `L_` | `L_XXX` |
| swipe | `itemName` `roiFront` `roiBack` `mode` `description` | `S_` | `S_TO_XXX` |
| ocr | `itemName` `roiFront` `roiBack` `mode` `method` `keyword` `description` | `O_` | `O_XXX` |
| list | `name` `direction` `type` `roiBack` `description` `list[{itemName, roiFront}]` | `L_` | `L_XXX` |

要点：

- ROI 统一为 `x,y,w,h`（左上角 + 宽高），坐标基于 1280x720 截图。
- `roiFront` 是模板尺寸与展示位置，`roiBack` 是匹配搜索区域，必须不小于模板。
- `threshold` 默认 0.8；识别不稳先调阈值（0.7~0.9），再考虑换素材。
- `ocr` 的 `mode` 可选 `Single`/`Full`/`Digit`/`DigitCounter`/`Duration`/`Quantity`，
  配合 `keyword` 做匹配。

## 3. 常用能力（`tasks/base_task.py` / `tasks/GameUi/game_ui.py`）

```python
# 截图与等待
self.screenshot()
self.appear(rule, interval=1, threshold=None)          # 是否出现
self.appear_then_click(rule, interval=2)               # 出现则点击（默认点图片中心）
self.wait_until_appear(rule)                            # 等待出现
self.wait_until_disappear(rule)                         # 等待消失

# 点击
self.click(rule, interval=None)                         # 点击规则位置
self.ui_click(click, stop, interval=1, timeout=None)    # 点 click 直到 stop 出现
self.ui_click_until_disappear(click, interval=1)

# OCR
self.ocr_appear(rule)                                   # rule 必须带 keyword
self.ocr_appear_click(rule)

# 列表与滑动
self.list_find(rule, name, max_swipe=10)                # 列表找项（可滑动）
self.list_appear_click(rule, interval=None)             # 列表项出现则点击
self.swipe(rule)

# 页面跳转（GameUi 引擎，页面注册后可用）
self.ui_get_current_page()                              # 识别当前页
self.ui_goto(page_xxx)                                  # 通过页面注册表自动跳转
```

`tasks/GameUi/` 分工：`game_ui.py`（寻路引擎：页面识别/BFS/路径执行/未知页脱困/统一操作入口）、
`panel.py`（角色面板左侧模块栏与顶部 tab 栏导航）、`top_menu.py`（右上角菜单）、
`activity.py`（活动弹窗顶部 tab 与右侧可滚动子 tab 导航）、
`page.py`（页面注册表与页面定义）、`targets.py`（页面连线目标类型）。
目前注册了 `page_main`（主页面）、`page_role_detail`（角色-详情）、`page_item_bag`（物品-背包）、
`page_challenge`（挑战-战场），以及活动弹窗的 `page_activity`（容器）、`page_activity_notice`（公告）、
`page_activity_list`（活动-推荐）、`page_activity_world_boss`（活动-世界首领）、
`page_activity_active`（活动-活跃），其余页面录制素材后
按 `tasks/GameUi/page.py` 中的方式注册（或在自己的 `tasks/<Task>/page.py` 扩展），
`ui_goto` 即可自动寻路。未注册页面的任务直接继承 `BaseTask`，用图像/OCR 规则导航即可
（当前 `Challenge`、`WorldBoss`、`Quiz`、`Restart` 都是这种方式）。
弹窗清理和安全点击由任务类覆盖 `GameUi.ui_close`、`GameUi.ui_safe_click` 配置。

全局弹窗清理：`tasks/GlobalGame/popup/` 下记录弹窗素材——特征点
`itemName=popup_<名字>`（image.json）+ 关闭区域 `itemName=popup_<名字>_close`（click.json）。
命名配对后所有任务的 `screenshot()` 都会自动检测并关闭（`GlobalGame.handle_global_popup`，1 秒限频），
新增弹窗只需录素材、无需改代码。

## 4. 任务骨架模板

`config.py`（参数定义，参考 `tasks/Challenge/config.py`）：

```python
# This Python file uses the following encoding: utf-8
from pydantic import BaseModel, Field

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase


class MyTaskConfig(BaseModel):
    switch: bool = Field(default=True, description="功能开关")


class MyTask(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    my_task_config: MyTaskConfig = Field(default_factory=MyTaskConfig)
```

`script_task.py`（逻辑，参考 `tasks/Challenge/script_task.py`、`tasks/WorldBoss/script_task.py`）：

```python
# This Python file uses the following encoding: utf-8
from module.exception import TaskEnd
from module.logger import logger

from tasks.MyTask.assets import MyTaskAssets
from tasks.base_task import BaseTask


class ScriptTask(BaseTask, MyTaskAssets):
    def run(self):
        self.screenshot()
        if self.appear_then_click(self.I_MY_BUTTON, interval=1):
            logger.info('Clicked my button')

        # 设置下次运行（成功后按 success_interval 排期）
        self.set_next_run(task='MyTask', success=True, finish=False)
        raise TaskEnd('MyTask')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.run()
```

要点：

- 任务类继承 `BaseTask`（截图/点击/OCR/UI 断言等能力）与 `XxxAssets`（规则常量）；
  需要“页面自动寻路”时再混入 `GameUi`（需先在 `tasks/GameUi/page.py` 注册页面）。
- 任务正常结束用 `raise TaskEnd('TaskName')`；`run()` 不要返回 True/False 表示成功（由调度器统一处理）。
- 结束时调用 `self.set_next_run(task='TaskName', success=True, finish=False)` 刷新排期。
- 参数通过 `self.config.<task_snake>.xxx` 获取。

## 5. 页面对象

```python
from tasks.GameUi.page import Page

# 定义新页面（写在 tasks/GameUi/page.py 或自己的 tasks/<Task>/page.py）
page_x = Page(check_button=XxxAssets.I_PAGE_CHECK)
page_x.additional = [XxxAssets.I_AD_CLOSE]              # 可选的进页弹窗清理
page_x.link(button=XxxAssets.I_GOTO_Y, destination=page_y)
```

- `check_button` 用于识别页面；`links` 描述页面间可达关系，`ui_goto` 自动寻路。
- 页面变量名即页面名（由 traceback 反推），保持 `page_xxx` 命名。
- 不确定当前页时用 `self.ui_get_current_page()`。
- 角色面板左侧模块栏是可滚动的，用它连线时写 `SidebarTarget('物品')` 作为 button
  （`GameUi.ui_sidebar_click` 会 OCR 定位、自动滚动并校验切换结果）；任务里也可直接调用
  `self.ui_sidebar_click('物品')`。
- 顶部 tab 栏也可以横向滚动，连线时写 `TabTarget('打造')`（跨模块时带上 `module='物品'`）；
  任务里可直接调用 `self.ui_tab_click('打造')`，会自动校验选中态并重试。
  各模块的 tab 顺序维护在 `GameUi.PANEL_TABS`。
- 右上角菜单（可展开/收起）里的图标连线写 `MenuTarget(G.I_ACTIVITY_ICON)`；
  任务里可调用 `self.ui_open_menu()` / `self.ui_menu_click(icon)`，会自动判断并展开菜单。
- 活动弹窗：顶部 tab 连线写 `ActivityTabTarget('活动')`，右侧子 tab 连线写
  `ActivitySubTabTarget('世界首领')`（子 tab 可上下滚动，会自动查找）；
  任务里可直接调用 `self.ui_activity_tab_click(...)` / `self.ui_activity_subtab_click(...)`。

## 6. 用 MCP 开发新任务的流程

1. `select_config` 选定配置 → `screenshot` 看当前画面（需要时手动把游戏点到目标页）
2. `crop_asset(task=..., item_name=..., region="x,y,w,h")` 裁素材到 `res/res_<item_name>.png`
3. `add_rule(task=..., json_relpath="res/image.json", rule_type="image", rule={...})`
   —— 自动合并写入并重生 `assets.py`，返回常量名（如 `I_XXX`）
4. `test_rule(task=..., json_relpath="res/image.json", rule_type="image", rule={...})`
   —— 对当前帧测试，返回 `matched`/`similarity`/命中 ROI
5. `read_doc` + `search_code` 学习相似任务 → `write_file` 写 `config.py`、`script_task.py`
6. `run_task(task='Xxx', config='oas1')` → `get_run_logs` 看日志，`stop_run` 停止
7. 失败时 `get_error_screenshots` 查看 `log/error/<ts>/` 现场截图与日志

## 7. 调试方法

- 单跑任务：任务文件底部的 `__main__`（`Config('oas1')` + `Device` + `run()`），或用 MCP `run_task`
- 日志：控制台输出 + `log/<date>_<config>.txt`；MCP 运行日志在 `log/mcp/runs/<run_id>.log`
- 错误现场：`log/error/<timestamp>/`（截图 + `log.txt`），由 `save_error_log()` 保存
- 规则调试：MCP `test_rule`，或用 `dev_tools/assets_test.py` 的 `detect_image_detail/detect_ocr_detail`

## 8. 常见坑

- 截图必须是 1280x720，否则素材与规则全部失配
- `roiBack` 太小会报 `roi_back_too_small`；模板旋转/缩放会显著降低相似度
- `RuleOcr` 的 `keyword` 与 `mode` 要配套：`Single` 是全等匹配，`Full` 是包含匹配
- `res/list.json` 是 dict（不是数组），`add_rule` 时传 `rules` + `list_meta`
- `assets.py` 由脚本生成，手改会在下次 `add_rule`/annotator 保存时被覆盖
- `ocr_appear` / `RuleOcr.ocr()` 的 Full 模式在整串不匹配时会退化为**逐字符匹配**，几乎总返回 True；
  需要精确判断文案时用 `rule.detect_and_ocr(image)` + 文本包含判断（参考 `tasks/Arena/script_task.py`）
- `self.click(rule, interval=1)` 首次调用不会点击（等 interval 到时间才点），循环里用没问题，
  只点一次时不要传 interval
- 修改 `config.py` 参数后需重启脚本进程才会生效（配置在启动时载入）
- OCR 会把形近字认错（如「手」→「于」、「入」→「人」），精确文案判断要用**不易误识的片段**：
  跨服竞技匹配弹窗实际识别为「已找到对于，是否进人跨服战场」，所以判断用的是 `已找到对`
  （参考 `tasks/CrossArena/script_task.py` 的 `MATCH_TEXTS`）
- 长时间等待（>1 分钟）不动屏幕会被 `device.stuck_record_check()` 判为卡死（Wait too long），
  循环里要定期 `self.reset_records()`（清卡死 + 连点记录，GameUi 已提供）
- 弹窗文案识别、让路判断（`higher_priority_task_due`）、活跃页任务状态读取（`active_task_completed`）
  都是 `GameUi` 的公共方法，新任务直接复用，不要各自实现一份
- 活动-活跃页的任务列表很长（30 行以上，「同服竞技/跨服竞技」在中下段），读取状态必须
  先自适应滑到顶部再逐屏扫描（`activity_list_to_top` + `ACTIVITY_SCAN_ROWS`），只扫几屏会找不到；
  行名匹配统一用 `GameUi.ocr_name_match`（容忍 OCR 形近字误识，且前两字必须一致以免同服/跨服互相误判）
- minitouch 的滑动速度固定且很快（`swipe` 的 duration 参数对 minitouch 无效），列表扫描要用
  `GameUi.ui_swipe_gentle` 拆成多段小滑动（每屏 150px 左右）+ 滚动后等待 0.8s 再截图识别，
  整段快速滑动会甩过头、滑完立刻截图会识别不准（参考 `tasks/GameUi/activity.py`）
- **页面缓存失效**：`ui_goto` 依赖 `GameUi.ui_current` 算路径，界面在 `ui_goto` 之外发生变化后
  （重启游戏、角色自动寻路离开活动页、任务弹窗关闭回主页面等）必须调用
  `self.ui_reset_current_page()` 作废缓存，让下次导航重新识别，否则：
  - 缓存正好等于目标页时 `ui_goto` 直接返回 True（连截图都不做），后续步骤在错误的界面上操作，
    例如世界首领同一时段打第二个首领时在野外地图上找首领卡片，最后 `Unable to seek` 静默跳过；
  - 缓存是别的页面时会按旧路径点错按钮，例如在主页面点角色面板的返回键而打开了地图，
    随后 `Unknown ui page` + `GamePageUnknownError`（参考 `tasks/CrossArena/script_task.py` 的重启流程）
  重启游戏统一用 `GameUi.ui_restart_game()`（内部已作废缓存），不要自己 new `RestartTask`

## 9. 公共能力（GameUi）

- 背包找物品：`bag_find_item(name)` 返回物品图标中心坐标、`bag_click_item(name)` 查找并点击；
  OCR 物品名 + 慢速滚动逐屏扫描，几何常量（4 列、行距 120px）在 `tasks/GameUi/bag.py`
- 背包用物品：`bag_use_item(name)` = 查找并点击物品 -> 在详情面板 OCR 定位并点击「立即使用」
  （面板文案里有「使用后可参与…」的说明，所以按按钮文字精确匹配，不要匹配「使用」两字）
- 活跃任务状态：`active_task_completed(name)`
- 弹窗文案：`dialog_appear(rule, text)`；长时间等待：`reset_records()`、`ui_swipe_gentle(p1, p2)`
- 重启游戏：`ui_restart_game()`（跑一遍重启+登录流程，并作废页面缓存）
- 作废页面缓存：`ui_reset_current_page()`（界面在 `ui_goto` 之外变化后、下次寻路前调用）

## 10. 已实现任务

- `tasks/WorldBoss`：世界首领（跑图/寻路/锁定/攻击）
- `tasks/Arena` 同服竞技、`tasks/CrossArena` 跨服竞技：报名 -> 匹配 -> 参战 -> 重启计数；
  支持「根据活跃度判断」模式（查活动-活跃页对应任务是否已完成）和等待匹配时给高优先级任务让路
- `tasks/Challenge`、`tasks/Quiz`：挑战、答题
  - Quiz：背包找「趣味答题券」-> `bag_use_item` 立即使用 -> 答题（题库命中优先，否则问 DeepSeek）
    -> 读贴纸校验并回写题库 -> 关弹窗回主页面（进背包/回主页都走 `ui_goto`）
