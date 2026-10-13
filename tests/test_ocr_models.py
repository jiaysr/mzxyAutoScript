# -*- coding: utf-8 -*-
"""
OCR 模型管理（module/ocr/models.py / download.py）测试

    toolkit\\python.exe -m pytest tests/test_ocr_models.py
"""
from types import SimpleNamespace

import pytest

from module.ocr import download as ocr_download
from module.ocr.models import (
    DEFAULT_VERSION,
    FALLBACK_VERSIONS,
    MODEL_PROFILES,
    ensure_profile,
    get_model_version,
    profile_paths,
)
from module.server.setting import State


@pytest.fixture
def deploy_config(monkeypatch):
    def _apply(**kwargs):
        values = dict(
            OcrModelVersion=DEFAULT_VERSION,
            OcrModelDir='./bin/ocr_model',
            OcrModelAutoDownload=True,
            OcrModelAllowIRRewrite=False,
            OcrCpuThreadNum=4,
        )
        values.update(kwargs)
        config = SimpleNamespace(**values)
        monkeypatch.setattr(State, '_deploy_config_', config, raising=False)
        return config

    return _apply


def _touch_model_files(directory, version):
    profile = MODEL_PROFILES[version]
    for name in (profile['det'], profile['rec'], profile['dict']):
        path = directory / name
        path.write_bytes(b'0')


def test_default_version_is_v6():
    assert DEFAULT_VERSION == 'PP-OCRv6'
    assert 'PP-OCRv6' in FALLBACK_VERSIONS
    for version in ('PP-OCRv6', 'PP-OCRv6-tiny', 'PP-OCRv5'):
        assert version in MODEL_PROFILES


def test_profile_paths_requires_all_files(tmp_path):
    assert profile_paths('PP-OCRv6', str(tmp_path)) is None

    _touch_model_files(tmp_path, 'PP-OCRv6')
    paths = profile_paths('PP-OCRv6', str(tmp_path))

    assert paths['rec_image_shape'] == (3, 48, 320)
    assert paths['rec'].endswith('PP-OCRv6_rec_small.onnx')
    assert paths['dict'].endswith('ppocrv6_dict.txt')


def test_v6_tiny_uses_its_own_dict(tmp_path):
    _touch_model_files(tmp_path, 'PP-OCRv6-tiny')
    paths = profile_paths('PP-OCRv6-tiny', str(tmp_path))

    assert paths['det'].endswith('PP-OCRv6_det_tiny.onnx')
    assert paths['dict'].endswith('ppocrv6_tiny_dict.txt')


def test_unknown_version_falls_back_to_default(deploy_config):
    deploy_config(OcrModelVersion='PP-OCRv9')
    assert get_model_version() == DEFAULT_VERSION


def test_ensure_profile_skips_download_when_disabled(deploy_config, tmp_path):
    deploy_config(OcrModelAutoDownload=False)
    assert ensure_profile('PP-OCRv6', str(tmp_path)) is None


def test_ensure_profile_returns_local_files(deploy_config, tmp_path):
    deploy_config(OcrModelAutoDownload=False)
    _touch_model_files(tmp_path, 'PP-OCRv6')

    paths = ensure_profile('PP-OCRv6', str(tmp_path))

    assert paths is not None
    assert paths['det'].endswith('PP-OCRv6_det_small.onnx')


# --------------------------------------------------------------------- #
# 下载相关
# --------------------------------------------------------------------- #
def test_normalize_file_spec():
    assert ocr_download.normalize_file_spec('a/b/det.onnx') == ('a/b/det.onnx', 'det.onnx', None)
    assert ocr_download.normalize_file_spec(('a/b/det.onnx', 'x.onnx')) == ('a/b/det.onnx', 'x.onnx', None)
    assert ocr_download.normalize_file_spec(('a/b/det.onnx', 'x.onnx', 'abc')) == ('a/b/det.onnx', 'x.onnx', 'abc')


def test_onnx_ir_version(tmp_path):
    model = tmp_path / 'model.onnx'
    model.write_bytes(bytes([0x08, 0x08, 0x12, 0x04]) + b'test')

    assert ocr_download.onnx_ir_version(str(model)) == 8


def test_normalize_onnx_ir_downgrades_v10(tmp_path):
    model = tmp_path / 'model.onnx'
    model.write_bytes(bytes([0x08, 0x0A, 0x12, 0x04]) + b'test')

    assert ocr_download.normalize_onnx_ir(str(model))
    assert model.read_bytes()[:4] == bytes([0x08, 0x09, 0x12, 0x04])


def test_normalize_onnx_ir_keeps_supported_version(tmp_path):
    model = tmp_path / 'model.onnx'
    content = bytes([0x08, 0x08, 0x12, 0x04]) + b'test'
    model.write_bytes(content)

    assert ocr_download.normalize_onnx_ir(str(model))
    assert model.read_bytes() == content


def test_normalize_onnx_ir_rejects_unexpected_header(tmp_path):
    model = tmp_path / 'model.onnx'
    content = bytes([0x12, 0x04]) + b'test'
    model.write_bytes(content)

    assert not ocr_download.normalize_onnx_ir(str(model))
    assert model.read_bytes() == content


def test_model_profiles_sources():
    """默认模型源必须是 IR v8（onnxruntime 1.16 直接可加载），避免改写模型文件"""
    from module.ocr.download import MAA_ASSETS_SOURCE

    for version in ('PP-OCRv6', 'PP-OCRv6-tiny', 'PP-OCRv5'):
        profile = MODEL_PROFILES[version]
        assert profile['source'] == MAA_ASSETS_SOURCE
        for spec in profile['files']:
            assert len(spec) == 3, '模型文件需要带 sha256 校验'
            assert len(spec[2]) == 64
