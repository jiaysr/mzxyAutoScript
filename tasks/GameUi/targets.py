# This Python file uses the following encoding: utf-8
"""
页面连线（Page.links）使用的特殊目标类型

GameUi.appear_then_operate 根据类型分派到对应的导航方法：
- SidebarTarget -> GameUi.ui_sidebar_click(name)
- TabTarget     -> GameUi.ui_tab_click(name, module)
- MenuTarget    -> GameUi.ui_menu_click(icon)
"""


class SidebarTarget:
    """
    在角色面板左侧可滚动模块栏中 OCR 查找并点击模块
    """

    def __init__(self, name: str):
        self.name = name

    def __str__(self):
        return f'sidebar[{self.name}]'


class TabTarget:
    """
    在当前模块顶部可横向滚动的 tab 栏中 OCR 查找并点击 tab
    """

    def __init__(self, name: str, module: str = None):
        self.name = name
        self.module = module

    def __str__(self):
        return f'tab[{self.module or ""}{self.name}]'


class MenuTarget:
    """
    先确保右上角菜单展开，再查找并点击菜单内图标
    """

    def __init__(self, icon):
        self.icon = icon

    def __str__(self):
        return f'menu[{self.icon}]'


class ActivityTabTarget:
    """
    活动弹窗顶部 tab（公告/活动/活跃/排行，可横向滚动）
    """

    def __init__(self, name: str):
        self.name = name

    def __str__(self):
        return f'activity_tab[{self.name}]'


class ActivitySubTabTarget:
    """
    活动弹窗右侧子 tab（推荐/强化/.../世界首领/副本，可上下滚动）
    """

    def __init__(self, name: str):
        self.name = name

    def __str__(self):
        return f'activity_subtab[{self.name}]'
