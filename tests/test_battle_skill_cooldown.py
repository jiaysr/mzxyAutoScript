# -*- coding: utf-8 -*-
"""
技能冷却识别单测（不连设备）

样本（tests/assets/battle_skill/，都取自 2026-10-23 真机截图 1280x720 的区域 991,582,52,54）：
- skill1_ready_1.png：冷却好的样式一（左上角白色高光）
- skill1_ready_2.png：冷却好的样式二（高光转到顶部）
- skill1_cooling_1.png：冷却中，倒计时「1」
- skill1_cooling_2.png：冷却中，倒计时「0」

另外验证：
- GameUiAssets.I_SKILL1_READY 基准素材与样本一致（裁剪/素材管线没走样）
- 7 个技能位的区域、点击区、基准素材一一对应（判定点的是同一个按钮）

直接用 toolkit python 跑（没装 pytest 也能跑）：
    toolkit\\python.exe tests\\test_battle_skill_cooldown.py
装了 pytest 时也可以：python -m pytest tests/test_battle_skill_cooldown.py
"""
import sys
import traceback
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tasks.GameUi.navigation.battle_skill import (SKILL_COOLING, SKILL_READY, SKILL_UNKNOWN,
                                                  SkillCooldownJudge, skill_region_score)

ASSET_DIR = PROJECT_ROOT / 'tests' / 'assets' / 'battle_skill'
GAMEUI_RES = PROJECT_ROOT / 'tasks' / 'GameUi' / 'res' / 'battle'
REGION = (991, 582, 52, 54)  # 用户标定的区域1

READY_1 = ASSET_DIR / 'skill1_ready_1.png'
READY_2 = ASSET_DIR / 'skill1_ready_2.png'
COOLING_1 = ASSET_DIR / 'skill1_cooling_1.png'
COOLING_2 = ASSET_DIR / 'skill1_cooling_2.png'


def load_rgb(path: Path) -> np.ndarray:
    """读成 RGB（device.image / RuleImage.image 都是 RGB；cv2.imread 是 BGR）"""
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, f'图片读取失败: {path}'
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def load_scene(path: Path) -> np.ndarray:
    """把 52x54 的样本贴回 1280x720 黑底截图里的真实位置（验证的是实际运行路径）"""
    crop = load_rgb(path)
    scene = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y, w, h = REGION
    scene[y:y + h, x:x + w] = crop
    return scene


def test_score_separates_ready_and_cooling():
    """冷却好的分数至少是冷却中的 10 倍，且两组本身也拉开距离"""
    ready = [skill_region_score(load_rgb(p)) for p in (READY_1, READY_2)]
    cooling = [skill_region_score(load_rgb(p)) for p in (COOLING_1, COOLING_2)]
    assert min(ready) > 20, f'冷却好分数异常低: {ready}'
    assert max(cooling) < 5, f'冷却中分数异常高: {cooling}'
    assert min(ready) > max(cooling) * 10, f'两组分数没拉开: ready={ready}, cooling={cooling}'
    print(f'   ready={[round(v, 2) for v in ready]} cooling={[round(v, 2) for v in cooling]}')


def test_two_ready_styles_are_close():
    """两种「冷却好」样式的分数应该很接近（高光转动不能影响判定）"""
    score_1 = skill_region_score(load_rgb(READY_1))
    score_2 = skill_region_score(load_rgb(READY_2))
    assert max(score_1, score_2) / min(score_1, score_2) < 1.3, (score_1, score_2)


def test_judge_classifies_four_samples():
    """以「冷却好样式一」为基准：两种冷却好都判 ready，两种冷却中都判 cooling"""
    judge = SkillCooldownJudge({0: REGION},
                               {0: skill_region_score(load_rgb(READY_1))})
    assert judge.state(load_scene(READY_1), 0) == SKILL_READY
    assert judge.state(load_scene(READY_2), 0) == SKILL_READY
    assert judge.state(load_scene(COOLING_1), 0) == SKILL_COOLING
    assert judge.state(load_scene(COOLING_2), 0) == SKILL_COOLING
    assert judge.is_ready(load_scene(READY_2), 0)
    assert judge.is_cooling(load_scene(COOLING_1), 0)


def test_judge_from_images():
    """from_images 建判定器（用冷却好素材当基准）"""
    judge = SkillCooldownJudge.from_images({0: REGION}, {0: load_rgb(READY_1)})
    assert judge.has_reference(0)
    assert judge.state(load_scene(COOLING_2), 0) == SKILL_COOLING
    # 比例：冷却中远小于 1，冷却好接近 1
    assert judge.ratio_of(load_scene(COOLING_2), 0) < 0.2
    assert 0.8 < judge.ratio_of(load_scene(READY_2), 0) < 1.3


def test_unknown_without_reference():
    """没配基准素材的技能位不瞎判：unknown，既不 ready 也不 cooling"""
    judge = SkillCooldownJudge({0: REGION}, {})
    assert judge.state(load_scene(READY_1), 0) == SKILL_UNKNOWN
    assert not judge.is_ready(load_scene(READY_1), 0)
    assert not judge.is_cooling(load_scene(COOLING_1), 0)
    # 未登记的技能位同样 unknown
    assert judge.state(load_scene(READY_1), 5) == SKILL_UNKNOWN


def test_states_dict():
    """一次算完所有技能位"""
    judge = SkillCooldownJudge({0: REGION, 1: REGION},
                               {0: skill_region_score(load_rgb(READY_1))})
    states = judge.states(load_scene(READY_1))
    assert states == {0: SKILL_READY, 1: SKILL_UNKNOWN}, states


def test_reference_asset_matches_fixture():
    """GameUi 的基准素材就是从「冷却好样式一」裁的，分数必须一致"""
    asset = GAMEUI_RES / 'res_skill1_ready.png'
    assert asset.exists(), f'基准素材不存在: {asset}'
    asset_score = skill_region_score(load_rgb(asset))
    fixture_score = skill_region_score(load_rgb(READY_1))
    assert abs(asset_score - fixture_score) < 0.5, (asset_score, fixture_score)


def test_all_skill_assets_have_reference():
    """7 个技能位的基准素材都在，且尺寸与判定区域一致"""
    from tasks.GameUi.navigation.battle import SimpleBattle

    assert len(SimpleBattle.BATTLE_SKILL_READY_ASSET) == 7, '目前是 7 个技能位'
    assert len(SimpleBattle.BATTLE_SKILL_CHECK_REGIONS) == 7
    for name in ('res_skill1_ready.png', 'res_skill2_ready.png',
                 'res_skill3_ready.png', 'res_skill4_ready.png',
                 'res_skill5_ready.png', 'res_skill6_ready.png',
                 'res_skill7_ready.png'):
        path = GAMEUI_RES / name
        assert path.exists(), f'缺少基准素材: {path}'
        image = load_rgb(path)
        assert image.shape[:2] == (REGION[3], REGION[2]), f'{name} 尺寸 {image.shape} 与区域不符'
        # 技能6 图标中心是大块黑底，分数天然偏低（约 4.4），不能卡到 5
        assert skill_region_score(image) > 2, f'{name} 分数过低，可能不是「冷却好」的截图'


def test_seven_skill_slots_aligned():
    """7 个技能位都能对上：区域、点击区、基准素材三者一一对应"""
    from tasks.GameUi.navigation.battle import SimpleBattle

    assert len(SimpleBattle.BATTLE_SKILL_REGIONS) == 7
    assert tuple(SimpleBattle.BATTLE_SKILL_REGIONS) == tuple(SimpleBattle.BATTLE_SKILL_REGIONS_ALL)
    assert set(SimpleBattle.BATTLE_SKILL_CHECK_REGIONS) == set(range(7))
    assert set(SimpleBattle.BATTLE_SKILL_READY_ASSET) == set(range(7))
    for index, region in SimpleBattle.BATTLE_SKILL_CHECK_REGIONS.items():
        x, y, w, h = region
        assert w == REGION[2] and h == REGION[3], f'技能{index + 1} 判定区域尺寸异常: {region}'
        assert x >= 0 and y >= 0 and x + w <= 1280 and y + h <= 720, f'技能{index + 1} 区域越界'


def test_check_regions_match_click_regions():
    """冷却判定区域要和点击区域是同一个按钮（中心偏差不超过 2px）"""
    from tasks.GameUi.navigation.battle import SimpleBattle

    for index, check_region in SimpleBattle.BATTLE_SKILL_CHECK_REGIONS.items():
        click_region = SimpleBattle.BATTLE_SKILL_REGIONS[index]
        cx1, cy1 = check_region[0] + check_region[2] / 2, check_region[1] + check_region[3] / 2
        cx2, cy2 = click_region[0] + click_region[2] / 2, click_region[1] + click_region[3] / 2
        assert abs(cx1 - cx2) <= 2 and abs(cy1 - cy2) <= 2, \
            f'技能{index + 1} 判定区域 {check_region} 与点击区域 {click_region} 不同心'
    # 区域1 与用户标定的坐标一致
    assert SimpleBattle.BATTLE_SKILL_CHECK_REGIONS[0] == REGION


def test_invalid_region_is_safe():
    """越界/无效区域不能抛异常（截图边缘或 UI 不在时返回 0 = 按冷却处理）"""
    image = load_rgb(READY_1)
    assert skill_region_score(image, (10_000, 10_000, 52, 54)) == 0.0
    assert skill_region_score(image, None) > 0  # 整图
    assert skill_region_score(None) == 0.0


if __name__ == '__main__':
    tests = [(name, func) for name, func in sorted(globals().items())
             if name.startswith('test_') and callable(func)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f'PASS {name}')
        except Exception:  # noqa: BLE001
            failed += 1
            print(f'FAIL {name}')
            traceback.print_exc()
    print(f'---- {len(tests) - failed}/{len(tests)} passed ----')
    if failed:
        sys.exit(1)
