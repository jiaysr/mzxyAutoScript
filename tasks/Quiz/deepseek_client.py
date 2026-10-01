# This Python file uses the following encoding: utf-8
"""
DeepSeek 答题客户端

官方文档确认 deepseek-flash 支持图片输入（Vision）：
    content 用数组，图片走 image_url 的 data URL，单图最多按 1024 token 计费
这里只依赖项目已有的 requests，不引入 openai SDK
"""
import base64
import json
import time

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

{question_block}
要求：
1. 优先依据上面给出的文字作答；如果文字有明显缺漏，再结合图片修正
2. 只能从 A / B / C 三个选项里选一个，不要选其它内容
3. {exclude_hint}
4. 只输出如下 JSON，不要有多余文字：
{{"answer": "A", "answer_text": "选项A的文字", "reason": "简短理由", "confidence": 0.9}}"""

OCR_QUESTION_BLOCK = """题目文字（已由 OCR 识别）：
题干：{question}
选项：{options}

"""


class DeepSeekClient:
    def __init__(self, api_key: str, model: str = 'deepseek-flash',
                 timeout: int = 8, use_thinking: bool = False, retries: int = 1):
        self.api_key = (api_key or '').strip()
        self.model = model or 'deepseek-flash'
        self.timeout = timeout
        self.use_thinking = use_thinking
        self.retries = max(0, int(retries))

    def _post(self, payload: dict):
        """
        POST 到 chat/completions，超时/网络异常自动重试 retries 次
        :return: requests.Response，全部失败返回 None
        """
        for attempt in range(self.retries + 1):
            try:
                return requests.post(
                    CHAT_ENDPOINT,
                    headers={
                        'Authorization': f'Bearer {self.api_key}',
                        'Content-Type': 'application/json',
                    },
                    data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                    timeout=self.timeout,
                )
            except Exception as e:
                if attempt < self.retries:
                    logger.warning(f'DeepSeek 请求失败，重试 {attempt + 1}/{self.retries}: {e}')
                    time.sleep(0.5)
                    continue
                if isinstance(e, requests.Timeout):
                    logger.warning(f'DeepSeek 超时（{self.timeout}s，已重试 {self.retries} 次）')
                else:
                    logger.error(f'DeepSeek 请求异常: {e}')
                return None

    def available(self) -> bool:
        if not self.api_key:
            logger.warning('未配置 DeepSeek API Key，跳过 AI 答题')
            return False
        return True

    def test_connection(self) -> tuple:
        """
        测试 API 连通性：发一条最小的文本请求，验证 Key / 模型 / 网络
        :return: (ok, message)
        """
        if not self.api_key:
            return False, '未配置 API Key'

        started = time.time()
        payload = {
            'model': self.model,
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': 1,
            'stream': False,
        }
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
            return False, f'连接超时（{self.timeout}s），检查网络或代理'
        except Exception as e:
            return False, f'请求异常: {e}'

        cost = time.time() - started
        if resp.status_code != 200:
            return False, f'HTTP {resp.status_code}: {resp.text[:200]}'
        try:
            content = resp.json()['choices'][0]['message']['content']
        except Exception:
            return False, f'响应格式异常: {resp.text[:200]}'
        return True, f'连接正常，模型 {self.model} 用时 {cost:.2f}s，回复: {content!r}'

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

    def answer_quiz(self, image: np.ndarray, question: str = '', options: dict = None,
                    wrong_options: list = None) -> dict:
        """
        把答题界面截图交给模型，返回结构化结果

        本地 OCR 更稳，所以题干/选项以文字形式一并给出，模型只负责"选哪个"；
        图片作为补充（文字有缺漏时模型可以看图修正）

        :param image: 答题弹窗区域截图（BGR）
        :param question: 本地 OCR 的题干
        :param options: 本地 OCR 的选项 {'A': ..., 'B': ..., 'C': ...}
        :param wrong_options: 已验证错误的选项文字，会提示模型排除
        :return: {'answer', 'answer_text', 'reason', 'confidence'}，失败返回 None
        """
        if not self.available():
            return None

        if wrong_options:
            exclude_hint = ('以下选项已验证是错误的，不要选择它们：'
                            + '、'.join(wrong_options))
        else:
            exclude_hint = '没有已知的错误选项'

        options = options or {}
        if question or options:
            question_block = OCR_QUESTION_BLOCK.format(
                question=question or '（未识别）',
                options='，'.join(f'{k}：{v}' for k, v in sorted(options.items())
                                if v) or '（未识别）',
            )
        else:
            question_block = ''

        text = USER_PROMPT.format(question_block=question_block,
                                  exclude_hint=exclude_hint)
        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': text},
                        {
                            'type': 'image_url',
                            'image_url': {
                                'url': self.encode_image(image),
                                # 单图 token 上限本来就是 1024，降采样不加分反而糊掉小字
                                'detail': 'original',
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

        resp = self._post(payload)
        if resp is None:
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

        result.setdefault('answer', '')
        result.setdefault('answer_text', '')
        result.setdefault('reason', '')
        result['confidence'] = float(result.get('confidence') or 0)
        logger.info(f"AI 作答: {result['answer']} {result['answer_text']} "
                    f"(confidence={result['confidence']:.2f})")
        return result


if __name__ == '__main__':
    # 自测：
    #   python tasks/Quiz/deepseek_client.py --test --config oas1   # 只测连通性（读项目配置里的 Key）
    #   python tasks/Quiz/deepseek_client.py 截图.png --config oas1 # 走完整答题测试
    import argparse
    import os
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description='DeepSeek 客户端自测')
    parser.add_argument('image', nargs='?', default='', help='答题界面截图路径，给出则走完整答题测试')
    parser.add_argument('--test', action='store_true', help='只测试 API 连通性')
    parser.add_argument('--config', default='', help='从 config/<name>.json 读取 api_key / model')
    parser.add_argument('--api-key', default='', help='直接指定 API Key')
    parser.add_argument('--model', default='', help='直接指定模型')
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    model = args.model
    timeout = 8
    if args.config:
        cfg = {}
        cfg_path = Path.cwd() / 'config' / f'{args.config}.json'
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
        cfg = cfg.get('quiz', {}).get('quiz_config', {})
        api_key = api_key or cfg.get('api_key', '')
        model = model or cfg.get('model', '')
        timeout = int(cfg.get('api_timeout', timeout))

    client = DeepSeekClient(api_key, model=model or 'deepseek-flash', timeout=timeout)

    if args.test or not args.image:
        ok, message = client.test_connection()
        print(f"{'OK' if ok else 'FAIL'}  {message}")
        sys.exit(0 if ok else 1)

    img = cv2.imdecode(np.fromfile(args.image, dtype=np.uint8), cv2.IMREAD_COLOR)
    print(client.answer_quiz(img))

