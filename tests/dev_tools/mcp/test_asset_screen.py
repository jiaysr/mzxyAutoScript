# This Python file uses the following encoding: utf-8
import shutil
import unittest

import cv2
import numpy as np

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.runtime import PROJECT_ROOT
from dev_tools.mcp.tools import asset

TMP_REL = "log/mcp/_unittest_screen"
TMP_DIR = PROJECT_ROOT / TMP_REL


def _make_image() -> np.ndarray:
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[100:160, 200:360] = (0, 255, 0)
    return image


class RegionParseTest(unittest.TestCase):
    def test_parse_region_ok(self):
        self.assertEqual(asset.parse_region("1,2,30,40", (720, 1280, 3)), (1, 2, 30, 40))

    def test_parse_region_rejects_wrong_parts(self):
        with self.assertRaises(McpToolError):
            asset.parse_region("1,2,3", (720, 1280, 3))

    def test_parse_region_rejects_out_of_bounds(self):
        with self.assertRaises(McpToolError):
            asset.parse_region("1200,100,200,50", (720, 1280, 3))

    def test_parse_region_rejects_zero_size(self):
        with self.assertRaises(McpToolError):
            asset.parse_region("10,10,0,5", (720, 1280, 3))


class ImageIoTest(unittest.TestCase):
    def setUp(self):
        if TMP_DIR.exists():
            shutil.rmtree(TMP_DIR)
        TMP_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if TMP_DIR.exists():
            shutil.rmtree(TMP_DIR)

    def test_save_frame_returns_image_block(self):
        image = _make_image()
        path = TMP_DIR / "frame.png"
        block = asset.save_frame(image, path)
        self.assertEqual(block["type"], "image")
        self.assertTrue(path.exists())
        reloaded = cv2.imread(str(path))
        self.assertEqual(reloaded.shape, (720, 1280, 3))
        # 颜色空间往返正确（RGB→BGR 写盘）
        self.assertEqual(tuple(reloaded[110, 210]), (0, 255, 0))

    def test_load_image_path_converts_to_rgb(self):
        image = _make_image()
        path = TMP_DIR / "frame.png"
        asset.save_frame(image, path)
        loaded = asset.load_image(path)
        self.assertEqual(loaded.shape, (720, 1280, 3))
        self.assertEqual(tuple(loaded[110, 210]), (0, 255, 0))

    def test_ocr_region_missing_image_raises(self):
        with self.assertRaises(McpToolError):
            asset.ocr_region({"region": "0,0,100,50", "image_path": f"{TMP_REL}/nope.png"})

    def test_crop_from_image_file(self):
        image = _make_image()
        path = TMP_DIR / "source.png"
        asset.save_frame(image, path)
        text = asset._crop_and_save(
            source=image,
            region=(200, 100, 160, 60),
            target=TMP_DIR / "res_probe.png",
        )
        self.assertIn("res_probe.png", text)
        saved = cv2.imread(str(TMP_DIR / "res_probe.png"))
        self.assertEqual(saved.shape[:2], (60, 160))


if __name__ == "__main__":
    unittest.main()
