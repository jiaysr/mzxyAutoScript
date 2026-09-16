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

命名约定：任务目录用大驼峰（`AbyssShadows`），参数类同名，配置访问用下划线（`self.config.abyss_shadows`）。

## 2. 规则类型与常量

`assets.py` 中的常量前缀由 `itemName` 转大写生成：

| 类型 | 设备端字段（res JSON） | 常量前缀 | 示例 |
| --- | --- | --- | --- |
| image | `itemName` `imageName` `roiFront` `roiBack` `method` `threshold` `description` | `I_` | `I_RYOU_SHENSHE` |
| click | `itemName` `roiFront` `roiBack` `description` | `C_` | `C_BOSS_CLICK_AREA` |
| long_click | `itemName` `roiFront` `roiBack` `duration` `description` | `L_` | `L_XXX` |
| swipe | `itemName` `roiFront` `roiBack` `mode` `description` | `S_` | `S_TO_ABBSY_SHADOWS` |
| ocr | `itemName` `roiFront` `roiBack` `mode` `method` `keyword` `description` | `O_` | `O_ST_OVERFLOW` |
| list | `name` `direction` `type` `roiBack` `description` `list[{itemName, roiFront}]` | `L_` | `L_RYOU_ACTIVITY_LIST` |

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

# 页面跳转
self.ui_get_current_page()                              # 识别当前页
self.ui_goto(page_xxx)                                  # 通过页面注册表自动跳转
```

页面对象定义在 `tasks/GameUi/page.py`（`Page(check_button, links)`），常用页面常量：
`page_main`、`page_shikigami_records`、`page_guild`、`page_kekkai_toppa` 等；
`ui_goto` 会基于各页面的 `links` 自动规划路径。

## 4. 任务骨架模板

`config.py`（参数定义，参考 `tasks/SoulsTidy/config.py`）：

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

`script_task.py`（逻辑，参考 `tasks/SoulsTidy/script_task.py`）：

```python
# This Python file uses the following encoding: utf-8
from module.exception import TaskEnd
from module.logger import logger

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.MyTask.assets import MyTaskAssets


class ScriptTask(GameUi, MyTaskAssets):
    def run(self):
        self.ui_get_current_page()
        self.ui_goto(page_main)

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

- 任务类继承 `GameUi`（页面跳转/断言能力）与 `XxxAssets`（规则常量）；需要战斗时再混入 `GeneralBattle`、`SwitchSoul` 等公共组件。
- 任务正常结束用 `raise TaskEnd('TaskName')`；`run()` 不要返回 True/False 表示成功（由调度器统一处理）。
- 结束时调用 `self.set_next_run(task='TaskName', success=True, finish=False)` 刷新排期。
- 参数通过 `self.config.<task_snake>.xxx` 获取。

## 5. 页面对象

```python
from tasks.GameUi.page import page_main, page_shikigami_records

# 定义新页面（tasks/GameUi/page.py 风格）
page_x = Page(check_button=XxxAssets.I_PAGE_CHECK, links={XxxAssets.I_ENTRY: page_main})
page_x.link(button=XxxAssets.I_BACK, destination=page_main)
```

- `check_button` 用于识别页面；`links` 描述页面间可达关系，`ui_goto` 自动寻路。
- 不确定当前页时用 `self.ui_get_current_page()`。

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
- 修改 `config.py` 参数后需重启脚本进程才会生效（配置在启动时载入）
