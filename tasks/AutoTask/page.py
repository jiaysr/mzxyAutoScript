# This Python file uses the following encoding: utf-8
"""
自动任务子页面注册：任务模块下的「任务特权」「任务加量」两个顶部 tab。

任务模块（任务） -> 任务特权；任务特权 -> 任务（顶部 tab 返回）；
任务模块（任务） -> 任务加量；任务加量 -> 任务（顶部 tab 返回）。
任务特权 <-> 任务加量 顶部 tab 直达（tab 栏常驻，不必绕回任务首 tab）。
tab 切换走 TabTarget（OCR 定位 + 选中态校验，模块=任务）。
"""
from tasks.AutoTask.assets import AutoTaskAssets as X
from tasks.GameUi.pages.page import Page, page_task
from tasks.GameUi.pages.targets import TabTarget

page_privilege = Page(X.I_PRIVILEGE_PAGE)
page_bonus = Page(X.I_BONUS_PAGE)
# 子页也标记所属模块：引擎据此自动补出「任务特权/任务加量 -> 各模块首 tab」的侧栏直连
page_privilege.module = '任务'
page_bonus.module = '任务'
page_task.link(button=TabTarget('任务特权'), destination=page_privilege)
page_privilege.link(button=TabTarget('任务'), destination=page_task)
page_task.link(button=TabTarget('任务加量'), destination=page_bonus)
page_bonus.link(button=TabTarget('任务'), destination=page_task)
# 同模块 tab 直连：顶部 tab 常驻，特权/加量之间一次点击直达
page_privilege.link(button=TabTarget('任务加量'), destination=page_bonus)
page_bonus.link(button=TabTarget('任务特权'), destination=page_privilege)
