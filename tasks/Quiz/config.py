# This Python file uses the following encoding: utf-8
from enum import Enum

from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class UnknownFallback(str, Enum):
    """
    未命中题库、API 也不可用时的兜底策略
    """

    SKIP = 'skip'  # 不点，等倒计时结束
    FIRST = 'first'  # 固定选 A
    RANDOM = 'random'  # 随机选一个


class QuizConfig(BaseModel):
    # ---------------------------------------------------------------- DeepSeek
    api_key: str = Field(default='', description='quiz_api_key_help')
    model: str = Field(default='deepseek-flash', description='quiz_model_help')
    # 单次 API 调用的超时秒数，题目 20 秒一题，留给 API 的时间
    api_timeout: int = Field(default=8, description='quiz_api_timeout_help')
    # 开启 thinking 会更准但更慢更贵
    use_thinking: bool = Field(default=False, description='quiz_use_thinking_help')

    # ---------------------------------------------------------------- 答题
    # 一局多少题
    total_questions: int = Field(default=20, description='quiz_total_questions_help')
    # 每题倒计时秒数
    question_time_limit: int = Field(default=20, description='quiz_time_limit_help')
    # 题库里标记为未验证的题，是否仍然重新问 API（会把已排除的选项告诉模型）
    ask_again_if_unverified: bool = Field(default=True, description='quiz_ask_again_help')
    unknown_fallback: UnknownFallback = Field(default=UnknownFallback.RANDOM,
                                              description='quiz_unknown_fallback_help')

    # ---------------------------------------------------------------- 题库
    # 未命中时保存截图，方便事后人工补库
    save_unknown_screenshot: bool = Field(default=True, description='quiz_save_screenshot_help')
    # 是否把 AI 的答案也写入题库（会标记为未验证）
    learn_from_llm: bool = Field(default=True, description='quiz_learn_from_llm_help')


class Quiz(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    quiz_config: QuizConfig = Field(default_factory=QuizConfig)
