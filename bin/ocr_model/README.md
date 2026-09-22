# OCR 模型

本目录存放本地 OCR 使用的 ONNX 模型，来自 [RapidOCR](https://www.modelscope.cn/models/RapidAI/RapidOCR)
（PaddleOCR 官方发布模型的 ONNX 版本，Apache-2.0），由 `module/ocr/download.py` 下载：

| 文件 | 说明 |
| --- | --- |
| `ch_PP-OCRv5_det_mobile.onnx` | PP-OCRv5 文本检测（mobile） |
| `ch_PP-OCRv5_rec_mobile.onnx` | PP-OCRv5 文本识别（mobile） |
| `ppocrv5_dict.txt` | PP-OCRv5 识别字典 |
| `ch_PP-OCRv4_det_mobile.onnx` / `ch_PP-OCRv4_rec_mobile.onnx` | PP-OCRv4 检测/识别（备选，字典复用 ppocr-onnx 内置的 `ppocr_keys_v1.txt`） |

切换/补下模型：

```bash
python -m module.ocr.download PP-OCRv5      # 下载指定版本到 bin/ocr_model
```

模型版本在 `config/deploy.yaml` 的 `OcrModelVersion` 配置，可选 `PP-OCRv5`（默认）、`PP-OCRv4`、
`default`（ppocr-onnx 内置的 PP-OCRv3 检测 + PP-OCRv2 识别）。模型缺失时会按
`OcrModelAutoDownload` 自动下载，下载失败则回退到 `default`。
