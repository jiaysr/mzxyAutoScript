# This Python file uses the following encoding: utf-8
"""
答题题库测试

覆盖三处改动：
1. 题目身份 = 题干 + 选项：题干只差一个字的题（国鸟 / 国花）不能串成一道
2. 错误选项排除是硬约束：题库、AI、兜底三条路径都不会再点已排除的选项
3. question_time_limit 生效：预算不够就不问 AI，直接兜底抢答；AI 超时按剩余预算收窄

真实题库用 tasks/Quiz/bank/quiz_zh.json（bank_sync pull 同步过来的那本）
"""
import json
from pathlib import Path
from types import SimpleNamespace

from module.base.timer import Timer
from tasks.Quiz.question_bank import QuestionBank
from tasks.Quiz.script_task import RUSH_RESERVE, ScriptTask

BANK_FILE = Path(__file__).resolve().parents[1] / 'tasks' / 'Quiz' / 'bank' / 'quiz_zh.json'
# 实测被串过答案的一条：题干是国鸟，答案却是国花的「牡丹」
BIRD_ID = 'q5d2206479'
BIRD_QUESTION = '中国的国鸟是什么？'
BIRD_OPTIONS = ['丹顶鹤', '短尾信天翁', '黄嘴白鹭']
FLOWER_QUESTION = '中国的国花是什么？'
FLOWER_OPTIONS = {'A': '牡丹', 'B': '菊花', 'C': '梅花'}


# ---------------------------------------------------------------- 工具
def record(question, options, answer='', verified=False, wrong=None):
    return {
        'id': QuestionBank.make_id(question),
        'q': question,
        'options': list(options),
        'answer': answer,
        'verified': verified,
        'wrong': list(wrong or []),
        'hits': 0,
        'correct': 0,
        'source': 'llm',
        'updated': '2026-10-18',
    }


def make_bank(tmp_path, questions) -> QuestionBank:
    path = tmp_path / 'quiz_zh.json'
    QuestionBank.dump(str(path), questions)
    return QuestionBank(str(path)).load()


def load_real_bank() -> QuestionBank:
    return QuestionBank(str(BANK_FILE)).load()


class FakeTask(ScriptTask):
    """只保留答题决策逻辑的 ScriptTask：截图/OCR/点击/问 AI 全部替换掉"""

    def __init__(self, bank, ai_results=None, feedback='correct'):
        self.bank = bank
        self.question = BIRD_QUESTION
        self.options = {chr(ord('A') + i): text for i, text in enumerate(BIRD_OPTIONS)}
        self.ai_results = list(ai_results or [])
        self.ai_calls = []
        self.clicked = []
        self.feedback = feedback
        self._config = SimpleNamespace(
            ask_again_if_unverified=True,
            unknown_fallback='random',
            api_timeout=8,
            question_time_limit=20,
            learn_from_llm=True,
        )

    @property
    def quiz_config(self):
        return self._config

    def screenshot(self):
        return None

    def ocr_locally(self):
        return self.question, dict(self.options)

    def click_option(self, letter):
        self.clicked.append(letter)

    def read_feedback(self, timeout: int = 10):
        return self.feedback

    def ask_ai(self, question='', options=None, wrong_options=None):
        self.ai_calls.append({'question': question, 'options': dict(options or {}),
                              'wrong': list(wrong_options or [])})
        return self.ai_results.pop(0) if self.ai_results else None


# ---------------------------------------------------------------- 题干 + 选项
def test_one_char_question_is_a_different_question(tmp_path):
    """题干只差一个字：题干相似度确实过阈值，但选项完全不同，不能当成同一道题"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '丹顶鹤', verified=True)])
    similarity = QuestionBank.similarity(bank.normalize(BIRD_QUESTION), bank.normalize(FLOWER_QUESTION))
    assert similarity >= 0.85, '这两道题的题干相似度本来就超过阈值，所以必须靠选项区分'

    found, _ = bank.find(FLOWER_QUESTION, FLOWER_OPTIONS)
    assert found is None, '国花题不能命中到国鸟那条记录上'

    found, _ = bank.find(BIRD_QUESTION, {'A': '丹顶鹤', 'B': '短尾信天翁', 'C': '黄嘴白鹭'})
    assert found is not None and found['id'] == QuestionBank.make_id(BIRD_QUESTION)


def test_same_question_with_ocr_noise_still_matches(tmp_path):
    """同一道题、OCR 有出入时仍然要命中（题干少字、选项错一个字）"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '丹顶鹤', verified=True)])

    found, _ = bank.find('中国的国鸟是什么', {'A': '丹顶鹤', 'B': '短尾信天翁', 'C': '黄嘴白鹭'})
    assert found is not None

    found, _ = bank.find('中国的国乌是什么？', {'A': '丹顶鹤', 'B': '短尾信天翁', 'C': '黄嘴白鹭'})
    assert found is not None, '题干一个字被认错，选项一致也应该命中'

    found, _ = bank.find(BIRD_QUESTION, {'A': '丹顶鹤', 'B': '短尾信天翁', 'C': '黄嘴白莺'})
    assert found is not None, '选项一个字被认错，题干一致也应该命中'


def test_options_unknown_keeps_question_only_matching(tmp_path):
    """选项完全没读出来时退回只按题干判断（交给 AI 兜底，不要因此建重复条目）"""
    bank = make_bank(tmp_path, [record(FLOWER_QUESTION, list(FLOWER_OPTIONS.values()), '牡丹', verified=True)])
    found, _ = bank.find(FLOWER_QUESTION, {})
    assert found is not None


def test_flower_question_gets_its_own_entry(tmp_path):
    """两道题各自成条，答案互不污染"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '丹顶鹤', verified=True)])
    entry = bank.ensure_entry(FLOWER_QUESTION, FLOWER_OPTIONS)
    assert entry['id'] != QuestionBank.make_id(BIRD_QUESTION)
    assert len(bank.questions) == 2

    bank.upsert(FLOWER_QUESTION, FLOWER_OPTIONS, '牡丹', record=entry)
    bank.update_by_feedback(FLOWER_QUESTION, '牡丹', 'correct', record=entry, options=FLOWER_OPTIONS)

    bird = next(q for q in bank.questions if q['q'] == BIRD_QUESTION)
    assert (entry['answer'], entry['verified']) == ('牡丹', True)
    assert (bird['answer'], bird['verified']) == ('丹顶鹤', True)


def test_same_text_different_options_does_not_create_duplicate_id(tmp_path):
    """题干一模一样但选项对不上：按新题处理，但不能建出两条同 id 的记录"""
    bank = make_bank(tmp_path, [record('以下哪个说法正确？', ['甲', '乙', '丙'], '甲')])
    entry = bank.ensure_entry('以下哪个说法正确？', {'A': '丁', 'B': '戊', 'C': '己'})
    assert entry['id'] == bank.questions[0]['id']
    assert len(bank.questions) == 1
    assert entry['options'] == ['丁', '戊', '己']


# ---------------------------------------------------------------- 真实题库
def test_real_bank_bird_record_is_polluted_and_demoted():
    """真实题库里国鸟那条的答案是国花的答案：自检要把它降级并挡住"""
    bank = load_real_bank()
    bird = next((q for q in bank.questions if q['id'] == BIRD_ID), None)
    assert bird is not None, '题库里应该有这条记录'
    assert bird['q'] == BIRD_QUESTION and bird['answer'] == '牡丹', '历史脏数据：答案被国花题串了'
    assert not bird['verified'], '自检应把对不上选项的答案降级为未验证'
    assert '牡丹' in bird['wrong'], '串题的答案要进排除表，防止题库同步把 verified 合并回来'

    # 于是它不能再用题库答案作答，但也不能挡住真正的选项
    options = bank.option_map(bird['options'])
    assert bank.option_letter(options, bird['answer'], bird['wrong']) == ''
    assert set(bank.allowed_letters(options, bird['wrong'])) == {'A', 'B', 'C'}

    # 国花题在真实题库里没有记录，不能被国鸟那条接住
    assert bank.find(FLOWER_QUESTION, FLOWER_OPTIONS)[0] is None


def test_real_bank_wrong_options_are_never_picked():
    """真实题库里每条带排除表的记录：随机/固定兜底都不能点已排除的选项"""
    bank = load_real_bank()
    checked = 0
    for item in bank.questions:
        wrong = item.get('wrong') or []
        options = bank.option_map(item.get('options'))
        if len(wrong) < 1 or len(options) < 3:
            continue
        excluded = {letter for letter, text in options.items() if bank.find_excluded(text, wrong)}
        if not excluded:
            # 排除项跟选项对不上（自检降级时特意保留的串题答案）：这套断言不适用
            continue
        checked += 1
        # record=None：不触发「全被排除就清表」的分支，避免测试改到真实题库文件
        for _ in range(20):
            letter = bank.pick_letter(options, wrong, mode='random', record=None)
            assert letter not in excluded, f'{item["q"]} 兜底又选中了已排除的 {options[letter]}'
        assert bank.pick_letter(options, wrong, mode='first', record=None) not in excluded
    assert checked >= 10, f'真实题库里带排除表的记录太少（{checked}），用例没起到作用'


def test_real_bank_answer_in_exclusion_list_is_refused(tmp_path):
    """题库答案就是上次答错的那个：任何路径都不能再点它"""
    bank = make_bank(tmp_path, [record('炎帝在哪里？', ['牧野', '家里', '火星'], '火星',
                                       verified=False, wrong=['火星'])])
    item = bank.questions[0]
    options = bank.option_map(item['options'])
    wrong = item['wrong']

    assert bank.option_letter(options, '火星', wrong) == '', '被排除的答案不能再点'
    assert bank.option_letter(options, '牧野', wrong) == 'A'
    letter = bank.pick_letter(options, wrong, mode='random', record=item)
    assert letter in ('A', 'B'), '兜底只能从没被排除的选项里挑'


def test_all_options_excluded_clears_stale_wrong(tmp_path):
    """三个选项全在排除表里说明数据自相矛盾：清掉排除表重来，而不是硬点已排除的选项"""
    bank = make_bank(tmp_path, [record('炎帝在哪里？', ['牧野', '家里', '火星'], '牧野',
                                       wrong=['牧野', '家里', '火星'])])
    item = bank.questions[0]
    options = bank.option_map(item['options'])
    letter = bank.pick_letter(options, item['wrong'], mode='random', record=item)
    assert letter in ('A', 'B', 'C')
    assert item['wrong'] == []


def test_wrong_entry_is_dropped_when_similarity_is_loose(tmp_path):
    """答对后要把「跟正确答案是同一个选项」的排除项清掉（OCR 写法差异也算同一个）"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '丹顶鹤',
                                       verified=False, wrong=['短尾信天翁', '丹顶鹤 '])])
    item = bank.questions[0]
    bank.update_by_feedback(BIRD_QUESTION, '丹顶鹤', 'correct', record=item,
                            options=bank.option_map(BIRD_OPTIONS))
    assert item['verified'] is True
    assert item['wrong'] == ['短尾信天翁'], '确认为正确答案的选项不能留在排除表里'


def test_sanitize_drops_orphan_wrong_entry(tmp_path):
    """排除项跟本题选项毫无关系（串题留下的）：自检清掉"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '丹顶鹤',
                                       verified=True, wrong=['梅花'])])
    assert bank.questions[0]['wrong'] == []
    assert bank.questions[0]['verified'] is True


# ---------------------------------------------------------------- 选点路径
def test_ai_pick_is_forced_off_the_excluded_option(tmp_path):
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '', wrong=['短尾信天翁'])])
    options = bank.option_map(BIRD_OPTIONS)
    wrong = ['短尾信天翁']

    # AI 第一次选到了已排除的选项，重问时给出安全答案
    task = FakeTask(bank, ai_results=[{'answer': 'A', 'answer_text': '丹顶鹤'}])
    letter, text = task.pick_ai_option(BIRD_QUESTION, {'answer': 'B', 'answer_text': '短尾信天翁'},
                                       options, wrong)
    assert (letter, text) == ('A', '丹顶鹤')
    assert task.ai_calls[-1]['options'] == {'A': '丹顶鹤', 'C': '黄嘴白鹭'}, '重问时要把排除项摘掉'


def test_ai_pick_falls_back_to_random_safe_option(tmp_path):
    """重问也失败时，在剩余选项里随机，绝不回到已排除的那个"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '', wrong=['短尾信天翁'])])
    options = bank.option_map(BIRD_OPTIONS)
    task = FakeTask(bank, ai_results=[{'answer': 'B', 'answer_text': '短尾信天翁'}])
    for _ in range(20):
        letter, _ = task.pick_ai_option(BIRD_QUESTION, {'answer': 'B', 'answer_text': '短尾信天翁'},
                                        options, ['短尾信天翁'])
        assert letter in ('A', 'C')


def test_ai_letter_without_text_is_distrusted_when_wrong_is_orphan(tmp_path):
    """AI 给的字母没读到选项文字，而排除表里正好有无主条目：不敢点"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '', wrong=['黄嘴白鹭'])])
    options = {'A': '丹顶鹤', 'B': '短尾信天翁'}          # C 没读出来
    task = FakeTask(bank, ai_results=[])
    letter, _ = task.pick_ai_option(BIRD_QUESTION, {'answer': 'C', 'answer_text': ''}, options, ['黄嘴白鹭'])
    assert letter != 'C'
    assert letter in ('A', 'B')


def test_fallback_pick_never_picks_excluded_and_honours_skip(tmp_path):
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '', wrong=['短尾信天翁'])])
    options = bank.option_map(BIRD_OPTIONS)
    item = bank.questions[0]

    task = FakeTask(bank)
    for _ in range(20):
        task.clicked.clear()
        task.fallback_pick(options, item['wrong'], item)
        assert task.clicked and task.clicked[0] in ('A', 'C')

    task.clicked.clear()
    task._config.unknown_fallback = 'skip'
    task.fallback_pick(options, item['wrong'], item)
    assert task.clicked == [], 'skip 策略下不作答'


def test_fallback_pick_blind_guess_only_without_exclusion_list(tmp_path):
    """选项文字完全没读出来：没有排除表才允许闭眼选一个，有排除表就空着"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '')])
    task = FakeTask(bank)

    task.clicked.clear()
    task.fallback_pick({}, [], bank.questions[0])
    assert len(task.clicked) == 1

    task.clicked.clear()
    task.fallback_pick({}, ['短尾信天翁'], bank.questions[0])
    assert task.clicked == []


# ---------------------------------------------------------------- 时间预算
def test_question_budget_drives_ai_timeout():
    task = FakeTask(None)
    assert task.question_budget_left() == float('inf'), '没在计时就不要限制'
    assert task.ai_timeout() == 8 and task.ai_budget_enough()

    task.question_timer = Timer(20).start()
    assert task.question_budget_left() > 19
    assert task.ai_timeout() == 8, '预算充足时用配置的超时'

    task.question_timer = SimpleNamespace(limit=20, current=lambda: 18.0)
    assert task.question_budget_left() == 2.0
    assert not task.ai_budget_enough(), '只剩 2 秒就不该问 AI 了'
    assert task.ai_timeout() == 1.0, '超时要按剩余预算收窄到下限'

    task.question_timer = SimpleNamespace(limit=20, current=lambda: 15.0)
    assert task.ai_budget_enough(), f'剩 5 秒还够问一次（预留 {RUSH_RESERVE}s 抢答）'
    assert task.ai_timeout() == 3.5


def test_answer_one_skips_ai_and_rush_answers_when_budget_is_short(tmp_path):
    """预算见底：不问 AI，直接用题库兜底把题答上，反馈照常回写"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '丹顶鹤', verified=False)])
    task = FakeTask(bank, feedback='correct')
    task.question_timer = SimpleNamespace(limit=20, current=lambda: 19.0)

    task.answer_one()

    assert task.ai_calls == [], '来不及就不该再问 AI'
    assert task.clicked == ['A'], '兜底也要把题答上（用题库里未验证但没被排除的答案）'
    assert bank.questions[0]['verified'] is True, '答对后答案转正'


def test_wrong_answer_is_remembered_and_never_picked_again(tmp_path):
    """答错一次之后：错误选项进排除表，下一轮即使 AI 还想选它也不会再点"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '', verified=False)])

    # 第 1 轮：AI 选了 B（错的），游戏反馈答错
    first = FakeTask(bank, ai_results=[{'answer': 'B', 'answer_text': '短尾信天翁'}], feedback='wrong')
    first.answer_one()
    assert first.clicked == ['B']
    item = bank.questions[0]
    assert item['wrong'] == ['短尾信天翁'] and not item['verified']

    # 第 2 轮：AI 又选 B -> 被排除表挡住，带剩余选项重问，重问给出 C（对）
    second = FakeTask(bank, ai_results=[{'answer': 'B', 'answer_text': '短尾信天翁'},
                                        {'answer': 'C', 'answer_text': '黄嘴白鹭'}],
                      feedback='correct')
    second.answer_one()
    assert second.clicked == ['C'], '上一轮答错的 B 不能再点'
    assert len(second.ai_calls) == 2 and second.ai_calls[1]['options'] == {'A': '丹顶鹤', 'C': '黄嘴白鹭'}
    assert (item['answer'], item['verified']) == ('黄嘴白鹭', True)


def test_answer_one_uses_bank_answer_and_skips_api(tmp_path):
    """已验证的题：直接点，不调 API"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '丹顶鹤', verified=True)])
    task = FakeTask(bank)
    task.answer_one()
    assert task.ai_calls == []
    assert task.clicked == ['A']


def test_answer_one_asks_ai_with_exclusion_list(tmp_path):
    """题库没答案（或答案被排除）：问 AI 时要把排除项带上"""
    bank = make_bank(tmp_path, [record(BIRD_QUESTION, BIRD_OPTIONS, '短尾信天翁',
                                       verified=False, wrong=['短尾信天翁'])])
    task = FakeTask(bank, ai_results=[{'answer': 'A', 'answer_text': '丹顶鹤'}])
    task.answer_one()
    assert task.ai_calls and task.ai_calls[0]['wrong'] == ['短尾信天翁']
    assert task.clicked == ['A']


def test_bank_sync_merge_does_not_resurrect_polluted_answer():
    """题库同步合并时 verified 取并集：合并结果必须再过一遍自检，不能把串题答案合并回来"""
    from tasks.Quiz import bank_sync

    local = [record(BIRD_QUESTION, BIRD_OPTIONS, '牡丹', verified=False, wrong=['牡丹'])]
    remote = [record(BIRD_QUESTION, BIRD_OPTIONS, '牡丹', verified=True, wrong=['梅花'])]
    merged, _ = bank_sync.merge_questions(local, remote)

    item = merged[0]
    assert item['verified'] is False, '别的设备的 verified 不能把降级冲掉'
    assert item['wrong'] == ['牡丹'], '降级时留的排除项要保留，串题的「梅花」要清掉'


def test_sync_keeps_exclusion_of_unverified_answer():
    """未验证记录的答案就是上次试错的那个：同步合并后不能把它从排除表里删掉"""
    from tasks.Quiz import bank_sync

    question = '哪一种水在灭火时比较有用？'
    options = ['热水', '冷水', '温水']
    # 上次答错后的写法：answer 和 wrong 里都是「冷水」
    local = [record(question, options, '冷水', verified=False, wrong=['冷水'])]
    # 交换仓库里的同一题（旧版本同步时已经把排除表清掉了）
    remote = [record(question, options, '冷水', verified=False, wrong=[])]

    merged, _ = bank_sync.merge_questions(local, remote)
    item = merged[0]
    assert item['wrong'] == ['冷水'], '排除表被删掉的话，下次 AI 还会被引导选同一个错项'
    assert item['verified'] is False


def test_sync_demotes_verified_when_answer_is_excluded():
    """远端 verified=True 不能把本地「答错了」的降级冲掉"""
    from tasks.Quiz import bank_sync

    question = '装备升级需要消耗什么？'
    options = ['铜币', '冶炼石', '星灵宝珠']
    local = [record(question, options, '冶炼石', verified=False, wrong=['冶炼石'])]
    remote = [record(question, options, '冶炼石', verified=True, wrong=[])]

    merged, _ = bank_sync.merge_questions(local, remote)
    item = merged[0]
    assert item['verified'] is False, '答案在排除表里，verified 不能取并集'
    assert item['wrong'] == ['冶炼石']


# ---------------------------------------------------------------- 读写分家
def test_ensure_entry_splits_similar_questions(tmp_path):
    """题干相近但文字不同：写入要落成自己的记录，不能复用另一道题"""
    upgrade = '装备升级需要消耗什么？'
    star_up = '装备升星需要消耗什么？'
    options = ['铜币', '冶炼石', '星灵宝珠']
    bank = make_bank(tmp_path, [record(upgrade, options, '冶炼石', verified=True)])

    entry = bank.ensure_entry(star_up, bank.option_map(options))
    assert entry['id'] != bank.questions[0]['id']
    assert entry['q'] == star_up
    assert len(bank.questions) == 2


def test_similar_question_feedback_creates_own_record(tmp_path):
    """
    装备升星 / 装备升级：读可以用模糊命中，写反馈必须落在新记录上，
    不能改另一道题的答案和排除表
    """
    upgrade = '装备升级需要消耗什么？'
    star_up = '装备升星需要消耗什么？'
    options = ['铜币', '冶炼石', '星灵宝珠']
    bank = make_bank(tmp_path, [record(upgrade, options, '冶炼石', verified=True)])
    original = bank.questions[0]

    # 第 1 轮：升星题模糊命中升级题的已验证答案 -> 点了冶炼石 -> 答错
    first = FakeTask(bank, feedback='wrong')
    first.question = star_up
    first.options = bank.option_map(options)
    first.answer_one()
    assert first.clicked == ['B']

    assert (original['answer'], original['verified'], original['wrong']) == ('冶炼石', True, []), \
        '升级题的记录不能被升星题的反馈改掉'
    own = [q for q in bank.questions if q['q'] == star_up]
    assert len(own) == 1 and own[0]['id'] != original['id'], '升星题要落成自己的记录'
    assert own[0]['wrong'] == ['冶炼石'] and own[0]['verified'] is False

    # 第 2 轮：升星题精确命中自己的记录 -> 排除冶炼石 -> 问 AI
    second = FakeTask(bank, ai_results=[{'answer': 'C', 'answer_text': '星灵宝珠'}], feedback='correct')
    second.question = star_up
    second.options = bank.option_map(options)
    second.answer_one()
    assert second.clicked == ['C'], '上一轮答错的冶炼石不能再点'
    assert second.ai_calls and second.ai_calls[0]['wrong'] == ['冶炼石']
    assert (own[0]['answer'], own[0]['verified']) == ('星灵宝珠', True)
    assert original['answer'] == '冶炼石', '升级题的答案仍然是冶炼石'


def test_question_bank_file_is_valid_json():
    with open(BANK_FILE, encoding='utf-8') as f:
        data = json.load(f)
    assert data['questions'], '题库不应为空'
