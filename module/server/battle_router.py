# This Python file uses the following encoding: utf-8
"""
战斗菜单的后台接口（给 mzxyOASX 的「脚本 -> 战斗」页面用）

接口一览
--------
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/battle/catalog?config=oas1` | 技能目录 + 预设列表 + 通用设置（页面初始化一次拿全） |
| GET | `/battle/presets?config=oas1` | 只取预设 id/名称（任务设置里的下拉框用） |
| GET | `/battle/icon/{skill_id}` | 技能图标 PNG |
| POST | `/battle/preset?config=oas1` | 新建预设 `{name?, base?}`（基于某个预设复制） |
| PUT | `/battle/preset?config=oas1` | 改预设 `{id, name?, order?, disabled?}`（默认预设拒绝） |
| DELETE | `/battle/preset/{preset_id}?config=oas1` | 删除预设（默认预设拒绝） |
| PUT | `/battle/common?config=oas1` | 改通用设置 `{identify_threshold?, respect_cooldown?, cast_interval?}` |
| PUT | `/battle/reset?config=oas1&preset=preset_1` | 预设顺序恢复默认 |
| GET | `/battle/identify?config=oas1&preset=preset_1` | 连设备识别 7 个技能位并给出该预设的释放计划（预览） |

写操作统一返回「保存后的完整 catalog」，前端拿到就直接覆盖本地状态，
不用自己拼一遍数据、也不会出现前后端不一致。

注意：识别会短暂占用设备，脚本正在运行时直接拒绝 —— 两个进程抢 adb 会导致截图失败。
"""
from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from module.logger import logger
from module.server.main_manager import mm
from module.server.script_process import ScriptState
from tasks.Battle import slots as battle_slots
from tasks.Battle.catalog import load_catalog
from tasks.Battle.config import DEFAULT_PRESET_ID
from tasks.Battle.planner import build_plan, plan_summary
from tasks.Battle.presets import (PresetError, create_preset, delete_preset, list_presets,
                                  reset_preset, resolve_preset, update_preset)
from tasks.Battle.recognizer import (DEFAULT_MARGIN, DEFAULT_THRESHOLD, SkillRecognizer,
                                     format_matches)

battle_app = APIRouter(prefix='/battle', tags=['battle'])


# --------------------------------------------------------------------- 工具
def _load_config(config_name: str):
    """读某个脚本实例的配置；不存在时 404"""
    try:
        config = mm.config_cache(config_name)
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[battle] 读取配置 {config_name} 失败：{e}')
        raise HTTPException(status_code=404, detail=f'配置 {config_name} 不存在')
    battle = getattr(config, 'battle', None)
    if battle is None:
        raise HTTPException(status_code=404,
                            detail=f'配置 {config_name} 里没有 battle 段（重启服务会自动补）')
    return config, battle


def _catalog_payload(config_name: str) -> dict:
    """页面需要的全部数据：技能目录 + 预设 + 通用设置"""
    catalog = load_catalog()
    _, battle = _load_config(config_name)

    skills = []
    for skill in catalog.skills:
        item = skill.to_dict(class_name=catalog.class_name(skill.id))
        item['iconUrl'] = f'/battle/icon/{skill.id}'
        skills.append(item)

    return {
        'config': config_name,
        'classes': [c.to_dict() for c in catalog.classes],
        'skills': skills,
        'presets': list_presets(battle, catalog),
        'slots': battle_slots.slots_info(),
        'common': battle.common.model_dump(),
        'defaults': {
            'order': catalog.default_order(),
            'threshold': DEFAULT_THRESHOLD,
            'margin': DEFAULT_MARGIN,
        },
    }


def _presets_payload(config_name: str) -> dict:
    """轻量接口：只给预设 id / 名称 / 是否内建（任务配置的下拉框用）"""
    catalog = load_catalog()
    _, battle = _load_config(config_name)
    return {
        'presets': [{'id': p['id'], 'name': p['name'], 'builtin': p['builtin']}
                    for p in list_presets(battle, catalog)],
    }


def _mutate(config_name: str, action) -> dict:
    """改配置 -> 保存 -> 返回最新的 catalog"""
    catalog = load_catalog()
    config, battle = _load_config(config_name)
    try:
        action(battle, catalog)
    except PresetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 改了嵌套模型（presets / common）不会触发自动保存，这里显式存盘
    config.save()
    logger.info(f'[battle] 保存战斗配置 {config_name}')
    return _catalog_payload(config_name)


# --------------------------------------------------------------------- 读
@battle_app.get('/catalog')
async def battle_catalog(config: str = 'template'):
    return await run_in_threadpool(_catalog_payload, config)


@battle_app.get('/presets')
async def battle_presets(config: str = 'template'):
    return await run_in_threadpool(_presets_payload, config)


@battle_app.get('/icon/{skill_id}')
async def battle_icon(skill_id: str):
    path = load_catalog().icon_path(skill_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f'技能 {skill_id} 没有图标')
    return FileResponse(str(path), media_type='image/png')


# --------------------------------------------------------------------- 预设
@battle_app.post('/preset')
async def battle_create_preset(payload: dict = Body(default={}),
                               config: str = 'template'):
    """新建预设：body 里 `name` 是名称、`base` 是基于哪个预设（默认 default）"""
    return await run_in_threadpool(
        _mutate, config,
        lambda battle, catalog: create_preset(
            battle, catalog,
            name=payload.get('name', ''),
            base=payload.get('base') or DEFAULT_PRESET_ID))


@battle_app.put('/preset')
async def battle_update_preset(payload: dict = Body(...),
                               config: str = 'template'):
    """修改预设；默认预设（id=default）会被拒绝"""
    preset_id = payload.get('id')
    if not preset_id:
        raise HTTPException(status_code=400, detail='缺少预设 id')
    return await run_in_threadpool(
        _mutate, config,
        lambda battle, catalog: update_preset(
            battle, preset_id, catalog,
            name=payload.get('name'),
            order=payload.get('order'),
            disabled=payload.get('disabled')))


@battle_app.delete('/preset/{preset_id}')
async def battle_delete_preset(preset_id: str, config: str = 'template'):
    return await run_in_threadpool(
        _mutate, config, lambda battle, catalog: delete_preset(battle, preset_id))


@battle_app.put('/reset')
async def battle_reset(config: str = 'template', preset: str = ''):
    """
    把预设的顺序恢复成默认、清空禁用

    :param preset: 预设 id；id=default 时无需处理（默认预设本来就是默认顺序）
    """
    if not preset or preset == DEFAULT_PRESET_ID:
        return await run_in_threadpool(_catalog_payload, config)
    return await run_in_threadpool(
        _mutate, config, lambda battle, catalog: reset_preset(battle, preset, catalog))


# --------------------------------------------------------------------- 通用设置
@battle_app.put('/common')
async def battle_set_common(payload: dict = Body(...), config: str = 'template'):
    """改通用设置（识别阈值 / 是否只点冷却好的 / 点击间隔）"""

    def _apply(battle, catalog):
        common = battle.common
        if 'identify_threshold' in payload:
            common.identify_threshold = float(payload['identify_threshold'])
        if 'respect_cooldown' in payload:
            common.respect_cooldown = bool(payload['respect_cooldown'])
        if 'cast_interval' in payload:
            common.cast_interval = float(payload['cast_interval'])

    return await run_in_threadpool(_mutate, config, _apply)


# --------------------------------------------------------------------- 识别预览
@battle_app.get('/identify')
async def battle_identify(config: str = 'template', preset: str = ''):
    """
    连设备截图，识别 7 个技能位并给出释放计划（脚本运行中不允许，避免抢设备）

    :param preset: 用哪个预设算计划；留空 = 系统预设（页面会把正在看的预设传进来）
    """
    process = mm.script_process.get(config)
    if process is not None and process.state != ScriptState.INACTIVE:
        return {'ok': False, 'message': '脚本正在运行，先停止再识别（两个进程抢设备会导致截图失败）'}
    try:
        return await run_in_threadpool(_identify_once, config, preset)
    except Exception as e:  # noqa: BLE001
        logger.exception(f'[battle] 技能位识别失败：{e}')
        detail = str(e).strip() or type(e).__name__
        return {'ok': False, 'message': f'识别失败：{detail}'}


def _identify_once(config_name: str, preset_id: str = '') -> dict:
    """同步实现：截图 -> 识别 -> 按预设算计划"""
    from module.device.screenshot import Screenshot

    catalog = load_catalog()
    _, battle = _load_config(config_name)
    threshold = float(battle.common.identify_threshold)
    preset = resolve_preset(battle, preset_id or DEFAULT_PRESET_ID, catalog)
    order = catalog.normalize_order(list(preset['order']), list(preset['disabled']))
    disabled = list(preset['disabled'])

    device = Screenshot(config=config_name)
    image = device.screenshot()
    recognizer = SkillRecognizer.from_catalog(catalog, threshold=threshold)
    matches = recognizer.identify(image, battle_slots.BATTLE_SKILL_CHECK_REGIONS)
    plan = build_plan(matches, order, catalog, disabled=disabled)

    logger.info(f'[battle] 技能位识别：{format_matches(matches, catalog)}')
    logger.info(f'[battle] 预设 {preset["name"]} 的释放计划：{plan_summary(plan)}')
    return {
        'ok': True,
        'preset': {'id': preset['id'], 'name': preset['name']},
        'threshold': threshold,
        'slots': [matches[i].to_dict(catalog) for i in sorted(matches)],
        'plan': [entry.to_dict() for entry in plan],
        'summary': plan_summary(plan),
    }
