# This Python file uses the following encoding: utf-8
"""
OCR 模型下载

模型来自 https://www.modelscope.cn/models/RapidAI/RapidOCR（PaddleOCR 官方模型的 ONNX 版本），
下载到 bin/ocr_model 下，文件名与 module/ocr/models.py 中的 profile 对应
"""
import os
import sys
from typing import List

import requests

from module.logger import logger

MODEL_SOURCE = "https://www.modelscope.cn/api/v1/models/RapidAI/RapidOCR/repo?Revision=master&FilePath="
CHUNK_SIZE = 1024 * 256
# onnxruntime 1.16 最高只支持 IR v9，而 PP-OCRv5 的模型是 IR v10
MAX_IR_VERSION = 9


def normalize_onnx_ir(path: str, max_ir: int = MAX_IR_VERSION) -> bool:
    """
    把模型的 IR 版本降到 onnxruntime 能加载的版本

    IR v10 相对 v9 只是协议的补充，paddle2onnx 导出的普通图结构用 v9 完全等价，
    这里直接改写文件头里的 ir_version（ModelProto 的第一个字段，varint 编码）
    """
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    if len(data) < 2 or data[0] != 0x08:
        logger.warning(f"Unexpected onnx file header: {path}")
        return False

    value, shift, index = 0, 0, 1
    while index < len(data) and shift < 64:
        byte = data[index]
        value |= (byte & 0x7F) << shift
        index += 1
        if not byte & 0x80:
            break
        shift += 7
    else:
        logger.warning(f"Unexpected onnx ir_version: {path}")
        return False

    if value <= max_ir:
        return True
    if value > 0x7F:
        logger.warning(f"Onnx ir_version {value} needs more bytes, skip: {path}")
        return False
    data[index - 1] = max_ir
    with open(path, 'wb') as f:
        f.write(data)
    logger.info(f"Downgrade onnx IR version {value} -> {max_ir}: {os.path.basename(path)}")
    return True


def verify_onnx(path: str) -> bool:
    """
    尝试加载模型，确认 onnxruntime 能正常使用
    """
    try:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.log_severity_level = 3
        with open(path, 'rb') as f:
            ort.InferenceSession(f.read(), so, providers=['CPUExecutionProvider'])
        return True
    except Exception as e:
        logger.warning(f"Cannot load onnx model {os.path.basename(path)}: {e}")
        return False


def download_file(file_path: str, target: str, source: str = MODEL_SOURCE) -> bool:
    """
    下载单个文件，先写临时文件再改名，避免下载中断留下坏文件
    :param file_path: 仓库内的路径，例如 onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx
    :param target: 本地保存路径
    :return: 是否成功
    """
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return True
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".download"
    url = source + requests.utils.quote(file_path, safe="")
    logger.info(f"Download OCR model: {url}")
    try:
        with requests.get(url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            written = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    f.write(chunk)
                    written += len(chunk)
                    if total and written % (CHUNK_SIZE * 20) < CHUNK_SIZE:
                        logger.info(f"  {written / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB")
        if os.path.getsize(tmp) == 0:
            raise IOError("empty file")
        os.replace(tmp, target)
        logger.info(f"OCR model saved: {target} ({os.path.getsize(target) / 1024 / 1024:.1f} MB)")
    except Exception as e:
        logger.error(f"Download failed: {file_path}: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False

    if target.endswith(".onnx"):
        normalize_onnx_ir(target)
        verify_onnx(target)
    return True


def download_model(files: List[str], model_dir: str, source: str = MODEL_SOURCE) -> bool:
    """
    下载一组模型文件
    :param files: 仓库内路径列表
    :param model_dir: 本地模型目录
    :return: 是否全部成功
    """
    ok = True
    for file_path in files:
        target = os.path.join(model_dir, os.path.basename(file_path))
        ok = download_file(file_path, target, source) and ok
    return ok


if __name__ == "__main__":
    from module.ocr.models import MODEL_PROFILES, DEFAULT_VERSION, project_root

    version = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VERSION
    directory = sys.argv[2] if len(sys.argv) > 2 else os.path.join(project_root(), "bin", "ocr_model")
    profile = MODEL_PROFILES.get(version)
    if not profile:
        print(f"Unknown version: {version}, available: {list(MODEL_PROFILES)}")
        sys.exit(1)
    print(f"Download {version} -> {directory}")
    sys.exit(0 if download_model(profile["files"], directory) else 1)
