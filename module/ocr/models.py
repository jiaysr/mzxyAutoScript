# This Python file uses the following encoding: utf-8
import os
from typing import Any, Dict, Optional

from module.base.decorator import cached_property
from module.logger import logger
from module.ocr.ppocr import TextSystem
from module.ocr.rpc import ModelProxy
from module.server.setting import State

# 可用的 OCR 模型，file 列表为 ModelScope 仓库 https://www.modelscope.cn/models/RapidAI/RapidOCR 内的路径
# 'default' 表示使用 ppocr-onnx 包内置的模型（PP-OCRv3 检测 + PP-OCRv2 识别）
MODEL_PROFILES: Dict[str, Optional[Dict[str, Any]]] = {
    'PP-OCRv5': {
        'files': [
            'onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx',
            'onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx',
            'paddle/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile/ppocrv5_dict.txt',
        ],
        'det': 'ch_PP-OCRv5_det_mobile.onnx',
        'rec': 'ch_PP-OCRv5_rec_mobile.onnx',
        'dict': 'ppocrv5_dict.txt',
        'rec_image_shape': (3, 48, 320),
    },
    'PP-OCRv4': {
        'files': [
            'onnx/PP-OCRv4/det/ch_PP-OCRv4_det_mobile.onnx',
            'onnx/PP-OCRv4/rec/ch_PP-OCRv4_rec_mobile.onnx',
            'paddle/PP-OCRv4/rec/ch_PP-OCRv4_rec_mobile/ppocr_keys_v1.txt',
        ],
        'det': 'ch_PP-OCRv4_det_mobile.onnx',
        'rec': 'ch_PP-OCRv4_rec_mobile.onnx',
        'dict': 'ppocr_keys_v1.txt',
        'rec_image_shape': (3, 48, 320),
    },
    'default': None,
}

DEFAULT_VERSION = 'PP-OCRv5'

_LOCAL_MODEL_CACHE: Dict[str, TextSystem] = {}
_OCR_PROXY_CACHE: Dict[str, ModelProxy] = {}


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_model_dir() -> str:
    deploy_config = State.deploy_config
    directory = getattr(deploy_config, 'OcrModelDir', None) or './bin/ocr_model'
    if not os.path.isabs(directory):
        directory = os.path.join(project_root(), directory)
    return os.path.abspath(directory)


def get_model_version() -> str:
    deploy_config = State.deploy_config
    version = getattr(deploy_config, 'OcrModelVersion', None) or DEFAULT_VERSION
    version = str(version).strip()
    if version not in MODEL_PROFILES:
        logger.warning(f'Unknown OcrModelVersion {version!r}, fallback to {DEFAULT_VERSION}')
        version = DEFAULT_VERSION
    return version


def profile_paths(version: str, model_dir: str = None) -> Optional[Dict[str, Any]]:
    """
    获取某个版本的本地模型路径，模型不齐全时返回 None
    :return: {'det', 'rec', 'dict', 'rec_image_shape'}
    """
    profile = MODEL_PROFILES.get(version)
    if not profile:
        return None
    model_dir = model_dir or get_model_dir()
    paths = {
        'det': os.path.join(model_dir, profile['det']),
        'rec': os.path.join(model_dir, profile['rec']),
        'dict': os.path.join(model_dir, profile['dict']) if profile['dict'] else None,
        'rec_image_shape': profile['rec_image_shape'],
    }
    for key in ('det', 'rec', 'dict'):
        path = paths[key]
        if path and not os.path.exists(path):
            return None
    return paths


def ensure_profile(version: str, model_dir: str = None) -> Optional[Dict[str, Any]]:
    """
    获取模型路径，缺失时按配置尝试下载
    """
    model_dir = model_dir or get_model_dir()
    paths = profile_paths(version, model_dir)
    if paths:
        return paths

    deploy_config = State.deploy_config
    if not getattr(deploy_config, 'OcrModelAutoDownload', True):
        return None

    from module.ocr.download import download_model

    profile = MODEL_PROFILES.get(version)
    if not profile:
        return None
    logger.info(f'OCR model {version} not found in {model_dir}, try to download')
    if not download_model(profile['files'], model_dir):
        return None
    return profile_paths(version, model_dir)


def build_local_model(version: str = None, model_dir: str = None) -> TextSystem:
    """
    构建本地 OCR 引擎，模型缺失时回退到 ppocr-onnx 内置模型
    """
    version = version or get_model_version()
    paths = ensure_profile(version, model_dir)
    if paths:
        model = TextSystem(
            use_angle_cls=False,
            rec_model_path=paths['rec'],
            det_model_path=paths['det'],
            char_dict_path=paths['dict'],
            rec_image_shape=paths['rec_image_shape'],
        )
        logger.info(f'OCR model: {version} ({os.path.dirname(paths["rec"])})')
        return model

    if version != 'default':
        logger.warning(f'OCR model {version} is not available, fallback to the builtin PP-OCRv2/v3 model')
    return TextSystem(use_angle_cls=False)


def get_local_ocr_model(lang: str = "ch") -> TextSystem:
    """
    本地 OCR 引擎，同一个进程内按照 版本 复用
    """
    version = get_model_version()
    cache_key = f'{lang}:{version}'
    if cache_key not in _LOCAL_MODEL_CACHE:
        _LOCAL_MODEL_CACHE[cache_key] = build_local_model(version)
    return _LOCAL_MODEL_CACHE[cache_key]


class OcrModel:
    @cached_property
    def ch(self) -> TextSystem:
        return get_local_ocr_model('ch')


OCR_MODEL = OcrModel()


def get_ocr_model(lang: str = "ch"):
    deploy_config = State.deploy_config
    if deploy_config.UseOcrServer:
        address = deploy_config.OcrClientAddress or "127.0.0.1:22268"
        if address not in _OCR_PROXY_CACHE:
            _OCR_PROXY_CACHE[address] = ModelProxy(address)
        return _OCR_PROXY_CACHE[address]
    return get_local_ocr_model(lang)


if __name__ == "__main__":
    model = get_local_ocr_model()
    import cv2
    import time

    image = cv2.imread(r"E:\Project\OnmyojiAutoScript-assets\jade.png")

    # 引入ocr 会导致非常巨大的内存开销
    for i in range(2):
        start_time = time.time()
        result = model.detect_and_ocr(image)
        print(result)
        end_time = time.time()
        print(f'耗时：{end_time-start_time}')
