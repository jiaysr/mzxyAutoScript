# This Python file uses the following encoding: utf-8
"""
DeepSeek 答题客户端

官方文档确认 deepseek-flash 支持图片输入（Vision）：
    content 用数组，图片走 image_url 的 data URL，单图最多按 1024 token 计费
这里只依赖项目已有的 requests，不引入 openai SDK
"""
import base64
import json

import cv2
import numpy as np
import requests

from module.logger import logger

DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
CHAT_ENDPOINT = f'{DEEPSEEK_BASE_URL}/chat/completions'

SYSTEM_PROMPT = (
    '你是游戏答题助手。用户会给你一张游戏答题界面的截图，'
    '你需要读出题目和选项，并选出正确答案。只输出 JSON，不要输出其它内容。'
)

USER_PROMPT = """这是游戏答题界面截图（单选题，只有 A / B / C 三个选项）。

要求：
1. 准确读出题干和三个选项的文字
2. 从三个选项中选出你认为正确的答案
3. {exclude_hint}
4. 只输出如下 JSON，不要有多余文字：
{{"question": "题干文字", "options": {{"A": "选项A文字", "B": "选项B文字", "C": "选项C文字"}}, "answer": "A", "answer_text": "选项A文字", "confidence": 0.9}}

如果截图看不清，也要给出最可能的答案，confidence 填低一些。"""


class DeepSeekClient:
    def __init__(self, api_key: str, model: str = 'deepseek-flash',
                 timeout: int = 8, use_thinking: bool = False):
        self.api_key = (api_key or '').strip()
        self.model = model or 'deepseek-flash'
        self.timeout = timeout
        self.use_thinking = use_thinking

    def available(self) -> bool:
        if not self.api_key:
            logger.warning('未配置 DeepSeek API Key，跳过 AI 答题')
            return False
        return True

    @staticmethod
    def encode_image(image: np.ndarray, quality: int = 85) -> str:
        """
        BGR 图 -> base64 data URL（PNG 无损，题目文字要清晰）
        """
        ok, buf = cv2.imencode('.png', image)
        if not ok:
            raise RuntimeError('图片编码失败')
        b64 = base64.b64encode(buf.tobytes()).decode('utf-8')
        return f'data:image/png;base64,{b64}'

    def answer_quiz(self, image: np.ndarray, wrong_options: list = None) -> dict:
        """
        把答题界面截图交给模型，返回结构化结果

        :param image: 答题弹窗区域截图（BGR）
        :param wrong_options: 已验证错误的选项文字，会提示模型排除
        :return: {'question', 'options', 'answer', 'answer_text', 'confidence'}
                 失败返回 None
        """
        if not self.available():
            return None

        if wrong_options:
            exclude_hint = ('以下选项已验证是错误的，不要选择它们：'
                            + '、'.join(wrong_options))
        else:
            exclude_hint = '没有已知的错误选项'

        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': USER_PROMPT.format(exclude_hint=exclude_hint)},
                        {
                            'type': 'image_url',
                            'image_url': {
                                'url': self.encode_image(image),
                                # 题目是大字，512x512 足够，更快更省
                                'detail': 'low',
                            },
                        },
                    ],
                },
            ],
            'response_format': {'type': 'json_object'},
            'stream': False,
        }
        if self.use_thinking:
            payload['extra_body'] = {'thinking': {'type': 'enabled'}}

        try:
            resp = requests.post(
                CHAT_ENDPOINT,
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                timeout=self.timeout,
            )
        except requests.Timeout:
            logger.warning(f'DeepSeek 超时（{self.timeout}s）')
            return None
        except Exception as e:
            logger.error(f'DeepSeek 请求异常: {e}')
            return None

        if resp.status_code != 200:
            logger.error(f'DeepSeek 返回 {resp.status_code}: {resp.text[:300]}')
            return None

        try:
            content = resp.json()['choices'][0]['message']['content']
            result = json.loads(content)
        except Exception as e:
            logger.error(f'DeepSeek 响应解析失败: {e}')
            logger.error(f'原始响应: {resp.text[:300]}')
            return None

        result.setdefault('options', {})
        result.setdefault('answer', '')
        result.setdefault('answer_text', '')
        result.setdefault('question', '')
        result['confidence'] = float(result.get('confidence') or 0)
        logger.info(f"AI 作答: {result['answer']} {result['answer_text']} "
                    f"(confidence={result['confidence']:.2f})")
        return result


if __name__ == '__main__':
    # 本地自测：把图片路径传进来，验证 API 是否正常
    import io
    import os
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else ''
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not path or not key:
        print('用法: DEEPSEEK_API_KEY=xxx python deepseek_client.py <图片路径>')
        raise SystemExit(1)
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    print(DeepSeekClient(key).answer_quiz(img))
