from types import SimpleNamespace

import pytest

from module.ocr import download as ocr_download
from module.ocr.models import (
    DEFAULT_VERSION,
    MODEL_PROFILES,
    build_local_model,
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


def test_profile_paths_requires_all_files(tmp_path):
    assert profile_paths('PP-OCRv5', str(tmp_path)) is None

    _touch_model_files(tmp_path, 'PP-OCRv5')
    paths = profile_paths('PP-OCRv5', str(tmp_path))

    assert paths['rec_image_shape'] == (3, 48, 320)
    assert paths['rec'].endswith('ch_PP-OCRv5_rec_mobile.onnx')
    assert paths['dict'].endswith('ppocrv5_dict.txt')


def test_unknown_version_falls_back_to_default(deploy_config):
    deploy_config(OcrModelVersion='PP-OCRv9')
    assert get_model_version() == DEFAULT_VERSION


def test_ensure_profile_skips_download_when_disabled(deploy_config, tmp_path):
    deploy_config(OcrModelAutoDownload=False)
    assert ensure_profile('PP-OCRv5', str(tmp_path)) is None


def test_build_local_model_falls_back_to_builtin(deploy_config, tmp_path):
    deploy_config(OcrModelAutoDownload=False, OcrModelDir=str(tmp_path))
    model = build_local_model()

    # 内置模型：PP-OCRv3 检测 + PP-OCRv2 识别
    assert model.text_recognizer.rec_image_shape == [3, 32, 320]
    assert type(model.text_recognizer) is not type(None)


def test_build_local_model_uses_configured_models(deploy_config, tmp_path):
    _touch_model_files(tmp_path, 'PP-OCRv4')
    deploy_config(OcrModelVersion='PP-OCRv4', OcrModelDir=str(tmp_path), OcrModelAutoDownload=False)
    paths = ensure_profile('PP-OCRv4', str(tmp_path))

    assert paths['rec_image_shape'] == (3, 48, 320)
    assert paths['dict'].endswith('ppocr_keys_v1.txt')


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
