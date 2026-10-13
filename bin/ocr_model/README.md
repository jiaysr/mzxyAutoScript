# OCR 模型

本目录存放本地 OCR 使用的 ONNX 模型，引擎为 [RapidOCR](https://github.com/RapidAI/RapidOCR)，
模型文件来自 [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（PaddleOCR 官方模型的 ONNX 版本，Apache-2.0），
由 `module/ocr/download.py` 下载并校验 sha256：

| 文件 | 说明 |
| --- | --- |
| `PP-OCRv6_det_small.onnx` / `PP-OCRv6_rec_small.onnx` | **PP-OCRv6 small 检测/识别（默认）** |
| `ppocrv6_dict.txt` | PP-OCRv6 small/medium 识别字典 |
| `PP-OCRv6_det_tiny.onnx` / `PP-OCRv6_rec_tiny.onnx` / `ppocrv6_tiny_dict.txt` | PP-OCRv6 tiny（更快，可选） |
| `ch_PP-OCRv5_det_mobile.onnx` / `ch_PP-OCRv5_rec_mobile.onnx` / `ppocrv5_dict.txt` | PP-OCRv5（备选） |

说明：

- 默认模型源提供的 ONNX 是 **IR v8**，onnxruntime 1.16 可以直接加载，不需要改写模型文件；
  其它来源（如 IR v10 的模型）需要把 `config/deploy.yaml` 里的 `OcrModelAllowIRRewrite` 配成 `true`。
- tiny 档使用**独立的字典** `ppocrv6_tiny_dict.txt`，不能和 small/medium 的字典混用。
- 模型版本在 `config/deploy.yaml` 的 `OcrModelVersion` 配置，可选 `PP-OCRv6`（默认）、`PP-OCRv6-tiny`、`PP-OCRv5`。
- CPU 线程数由 `OcrCpuThreadNum` 控制（默认 4，与 MaaFramework 一致）。
- 模型缺失时会按 `OcrModelAutoDownload` 自动下载；下载失败会依次回退 `PP-OCRv6` -> `PP-OCRv5`。

手动下载：

```bash
python -m module.ocr.download PP-OCRv6      # 下载指定版本到 bin/ocr_model
```
