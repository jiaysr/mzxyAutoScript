# This Python file uses the following encoding: utf-8
"""
种植-页面定义

入口：主页面 -> 角色面板 -> 辅助（幻化页）-> 顶部 tab「种植」
种植页本身是辅助模块的一个 tab，右上角「返回」由页面引擎自动补回主页面。
"""
from tasks.DailySignIn.page import page_fuhua
from tasks.GameUi.pages.page import Page, PageRegistry
from tasks.GameUi.pages.targets import TabTarget
from tasks.ZhongZhi.assets import ZhongZhiAssets as Z

# 辅助/种植页面：一键种植按钮或说明按钮任一命中即认为是种植页，
# 避免种植按钮随作物状态变化后页面识别失败
page_zhongzhi = Page([Z.I_ZHONGZHI_PAGE, Z.I_ZHONGZHI_HELP])
page_zhongzhi.module = '辅助'

# 幻化页 <-> 种植页：种植 tab 在辅助 tab 栏最右侧，导航会自动横向滚动查找
page_fuhua.link(button=TabTarget('种植', module='辅助'), destination=page_zhongzhi)
page_zhongzhi.link(button=TabTarget('幻化', module='辅助'), destination=page_fuhua)

# 种植页与奖励弹窗一样可能盖住主页面特征，放在注册表最前面优先识别
_registry = PageRegistry._registry
if page_zhongzhi in _registry:
    _registry.remove(page_zhongzhi)
_registry.insert(0, page_zhongzhi)
