# This Python file uses the following encoding: utf-8
import os
from typing import Any, Dict, Optional, Union

from module.base.decorator import cached_property
from module.exception import ScriptError
from module.logger import logger
from module.ocr.download import MAA_ASSETS_SOURCE
from module.ocr.engine import DEFAULT_CPU_THREADS, TextSystem
from module.ocr.rpc import ModelProxy
from module.server.setting import State

# 可用的 OCR 模型
#   files 为 (仓库内路径, 本地文件名, sha256) 列表，来源见 module/ocr/download.py
#   PP-OCRv6 是 PaddleOCR 2026 年的新模型，small 档默认（准确率/速度平衡）
MODEL_PROFILES: Dict[str, Optional[Dict[str, Any]]] = {
    'PP-OCRv6': {
        'source': MAA_ASSETS_SOURCE,
        'files': [
            ('OCR/ppocr_v6/small/det.onnx', 'PP-OCRv6_det_small.onnx',
             '66c0f34caaf432553710fd9973a7134d8cf924db7109310c3d2562dcc39b209d'),
            ('OCR/ppocr_v6/small/rec.onnx', 'PP-OCRv6_rec_small.onnx',
             '7dcf6298d77d2a6eb44c1ebeed990826ca895a9c68d955a3eace00763d052949'),
            ('OCR/ppocr_v6/small/keys.txt', 'ppocrv6_dict.txt',
             'b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d'),
        ],
        'det': 'PP-OCRv6_det_small.onnx',
        'rec': 'PP-OCRv6_rec_small.onnx',
        'dict': 'ppocrv6_dict.txt',
        'rec_image_shape': (3, 48, 320),
    },
    'PP-OCRv6-tiny': {
        'source': MAA_ASSETS_SOURCE,
        'files': [
            ('OCR/ppocr_v6/tiny/det.onnx', 'PP-OCRv6_det_tiny.onnx',
             '47e5d1038703831349e8edb4d0e9edf3547d484bbe0a170bd8583607049b287e'),
            ('OCR/ppocr_v6/tiny/rec.onnx', 'PP-OCRv6_rec_tiny.onnx',
             'd66bab3e7724fe4cca3aa6392ee0087169d61eb4f3ffa0d6576fff634f6dae28'),
            ('OCR/ppocr_v6/tiny/keys.txt', 'ppocrv6_tiny_dict.txt',
             'c5cbe34ef40c29c4df07ed012bf96569cb69a2d2a01a07027e9f13cb832bd9cd'),
        ],
        'det': 'PP-OCRv6_det_tiny.onnx',
        'rec': 'PP-OCRv6_rec_tiny.onnx',
        'dict': 'ppocrv6_tiny_dict.txt',
        'rec_image_shape': (3, 48, 320),
    },
    'PP-OCRv5': {
        'source': MAA_ASSETS_SOURCE,
        'files': [
            ('OCR/ppocr_v5/zh_cn/det.onnx', 'ch_PP-OCRv5_det_mobile.onnx',
             '8c3b7ee97913a7942b8565669dc9acbe8846fbbaf4b63e1d7fdb339005574a33'),
            ('OCR/ppocr_v5/zh_cn/rec.onnx', 'ch_PP-OCRv5_rec_mobile.onnx',
             '31fb844ce3a4aaf13e4bea62ae35f43bd9a509966061980c30db9b248c542a6b'),
            ('OCR/ppocr_v5/zh_cn/keys.txt', 'ppocrv5_dict.txt',
             'd1979e9f794c464c0d2e0b70a7fe14dd978e9dc644c0e71f14158cdf8342af1b'),
        ],
        'det': 'ch_PP-OCRv5_det_mobile.onnx',
        'rec': 'ch_PP-OCRv5_rec_mobile.onnx',
        'dict': 'ppocrv5_dict.txt',
        'rec_image_shape': (3, 48, 320),
    },
}

DEFAULT_VERSION = 'PP-OCRv6'
FALLBACK_VERSIONS = ('PP-OCRv6', 'PP-OCRv5')

_LOCAL_MODEL_CACHE: Dict[str, TextSystem] = {}
_OCR_PROXY_CACHE: Dict[str, ModelProxy] = {}


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_model_dir() -> str:
    deploy_config = State.deploy_config
    # 引擎包里模型在 env 目录，启动器通过 MZXY_OCR_MODEL_DIR 指过来
    directory = (os.environ.get('MZXY_OCR_MODEL_DIR', '').strip()
                 or getattr(deploy_config, 'OcrModelDir', None)
                 or './bin/ocr_model')
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


def get_cpu_threads() -> int:
    deploy_config = State.deploy_config
    threads = getattr(deploy_config, 'OcrCpuThreadNum', None)
    if not threads:
        return DEFAULT_CPU_THREADS
    return max(1, int(threads))


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
    allow_ir_rewrite = bool(getattr(deploy_config, 'OcrModelAllowIRRewrite', False))
    if not download_model(profile['files'], model_dir, profile['source'], allow_ir_rewrite=allow_ir_rewrite):
        return None
    return profile_paths(version, model_dir)


def build_local_model(version: str = None, model_dir: str = None) -> TextSystem:
    """
    构建本地 OCR 引擎（RapidOCR），模型缺失时按 FALLBACK_VERSIONS 依次尝试
    """
    version = version or get_model_version()
    candidates = [version] + [v for v in FALLBACK_VERSIONS if v != version]
    for candidate in candidates:
        paths = ensure_profile(candidate, model_dir)
        if not paths:
            continue
        if candidate != version:
            logger.warning(f'OCR model {version} is not available, fallback to {candidate}')
        model = TextSystem(
            use_angle_cls=False,
            rec_model_path=paths['rec'],
            det_model_path=paths['det'],
            char_dict_path=paths['dict'],
            rec_image_shape=paths['rec_image_shape'],
            threads=get_cpu_threads(),
        )
        logger.info(f'OCR model: {candidate} ({os.path.dirname(paths["rec"])}) cpu threads: {get_cpu_threads()}')
        return model

    raise ScriptError(
        f'No OCR model available, please check OcrModelVersion / OcrModelDir / OcrModelAutoDownload '
        f'in config/deploy.yaml (tried {candidates})'
    )


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

    image = cv2.imread(r"log/mcp/ancienthunt/a2.png")

    for i in range(2):
        start_time = time.time()
        result = model.detect_and_ocr(image)
        print(result)
        end_time = time.time()
        print(f'耗时：{end_time - start_time}')
