# This Python file uses the following encoding: utf-8
"""
OCR 模型下载

默认模型来自 MaaCommonAssets（PaddleOCR 官方模型的 ONNX 版本，Apache-2.0），
文件按 sha256 校验后落到 bin/ocr_model 下，文件名与 module/ocr/models.py 中的 profile 对应。

MaaCommonAssets 提供的 ONNX 是 IR v8，onnxruntime 1.16 可以直接加载，
不需要改写模型文件的 IR 版本；其它来源（如 IR v10 的模型）需要把
OcrModelAllowIRRewrite 配成 true 才能使用。
"""
import hashlib
import os
import sys
from typing import List, Optional, Sequence, Tuple, Union

import requests

from module.logger import logger

CHUNK_SIZE = 1024 * 256
# onnxruntime 1.16 最高只支持 IR v9
MAX_IR_VERSION = 9

# 模型源模板：{path} 会被替换成仓库内路径
MAA_ASSETS_SOURCE = "https://raw.githubusercontent.com/MaaXYZ/MaaCommonAssets/main/{path}"
MODELSCOPE_SOURCE = ("https://www.modelscope.cn/api/v1/models/RapidAI/RapidOCR/repo"
                     "?Revision=master&FilePath={path}")

# 兼容旧代码
MODEL_SOURCE = MODELSCOPE_SOURCE

# 文件描述：仓库内路径，或者 (仓库内路径, 本地文件名[, sha256])
FileSpec = Union[str, Tuple[str, str], Tuple[str, str, str]]


def normalize_file_spec(spec: FileSpec) -> Tuple[str, str, Optional[str]]:
    if isinstance(spec, str):
        return spec, os.path.basename(spec), None
    if len(spec) == 2:
        return spec[0], spec[1], None
    return spec[0], spec[1], spec[2]


def file_sha256(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            sha.update(chunk)
    return sha.hexdigest()


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


def onnx_ir_version(path: str) -> Optional[int]:
    """读取 onnx 文件头里的 IR 版本"""
    with open(path, 'rb') as f:
        data = f.read(16)
    if len(data) < 2 or data[0] != 0x08:
        return None
    value, shift, index = 0, 0, 1
    while index < len(data):
        byte = data[index]
        value |= (byte & 0x7F) << shift
        index += 1
        if not byte & 0x80:
            break
        shift += 7
    return value


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


def download_file(file_path: str, target: str, source: str = MODEL_SOURCE,
                  sha256: str = None, allow_ir_rewrite: bool = False) -> bool:
    """
    下载单个文件，先写临时文件再改名，避免下载中断留下坏文件
    :param file_path: 仓库内的路径，例如 OCR/ppocr_v6/small/det.onnx
    :param target: 本地保存路径
    :param source: 下载源模板，包含 {path} 占位符
    :param sha256: 期望的 sha256，为空则跳过校验
    :param allow_ir_rewrite: 是否允许改写 IR 版本（IR > 9 时）
    :return: 是否成功
    """
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return True
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".download"
    url = source.format(path=file_path)
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
        if sha256:
            actual = file_sha256(tmp)
            if actual.lower() != sha256.lower():
                raise IOError(f"sha256 mismatch: {actual} != {sha256}")
        os.replace(tmp, target)
        logger.info(f"OCR model saved: {target} ({os.path.getsize(target) / 1024 / 1024:.1f} MB)")
    except Exception as e:
        logger.error(f"Download failed: {file_path}: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False

    if target.endswith(".onnx"):
        ir_version = onnx_ir_version(target)
        if ir_version is not None and ir_version > MAX_IR_VERSION:
            if not allow_ir_rewrite:
                logger.error(
                    f"{os.path.basename(target)} is IR v{ir_version}, "
                    f"onnxruntime supports up to v{MAX_IR_VERSION}. "
                    f"Use an IR<=v{MAX_IR_VERSION} model, or set OcrModelAllowIRRewrite = true"
                )
                os.remove(target)
                return False
            normalize_onnx_ir(target)
        if not verify_onnx(target):
            os.remove(target)
            return False
    return True


def download_model(files: Sequence[FileSpec], model_dir: str, source: str = MODEL_SOURCE,
                   allow_ir_rewrite: bool = False) -> bool:
    """
    下载一组模型文件
    :param files: 仓库内路径列表（可带本地文件名与 sha256）
    :param model_dir: 本地模型目录
    :return: 是否全部成功
    """
    ok = True
    for spec in files:
        file_path, local_name, sha256 = normalize_file_spec(spec)
        target = os.path.join(model_dir, local_name)
        ok = download_file(file_path, target, source, sha256=sha256,
                           allow_ir_rewrite=allow_ir_rewrite) and ok
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
    sys.exit(0 if download_model(profile["files"], directory, profile["source"]) else 1)
