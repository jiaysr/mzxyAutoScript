# This Python file uses the following encoding: utf-8
import json
import shutil
import unittest

import cv2
import numpy as np

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.runtime import TASKS_ROOT
from dev_tools.mcp.tools import asset

TASK = "__mcp_test__"
TASK_DIR = TASKS_ROOT / TASK
RES_DIR = TASK_DIR / "res"


def make_source_image() -> np.ndarray:
    rng = np.random.default_rng(20260916)
    image = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    image[300:340, 500:560] = (10, 200, 30)
    return image


class RuleToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TASK_DIR.exists():
            shutil.rmtree(TASK_DIR)
        RES_DIR.mkdir(parents=True, exist_ok=True)
        (RES_DIR / "image.json").write_text("[]", encoding="utf-8")
        (RES_DIR / "ocr.json").write_text("[]", encoding="utf-8")
        cls.source = make_source_image()
        cls.source_path = RES_DIR / "source_for_test.png"
        cv2.imwrite(str(cls.source_path), cv2.cvtColor(cls.source, cv2.COLOR_RGB2BGR))

    @classmethod
    def tearDownClass(cls):
        if TASK_DIR.exists():
            shutil.rmtree(TASK_DIR)

    def _crop_probe(self, name: str = "probe"):
        return asset.crop_asset(
            {
                "task": TASK,
                "item_name": name,
                "region": "500,300,60,40",
                "description": "测试素材",
                "image_path": str(self.source_path),
                "overwrite": True,
            }
        )

    def _add_probe_rule(self):
        return asset.add_rule(
            {
                "task": TASK,
                "json_relpath": "res/image.json",
                "rule_type": "image",
                "rule": {
                    "itemName": "probe",
                    "imageName": "res_probe.png",
                    "roiFront": "500,300,60,40",
                    "roiBack": "500,300,60,40",
                    "threshold": 0.8,
                    "description": "测试素材",
                },
            }
        )

    def test_crop_asset_from_image_path(self):
        text = self._crop_probe()
        self.assertIn("res_probe.png", text)
        self.assertTrue((RES_DIR / "res_probe.png").exists())

    def test_add_rule_merges_and_regenerates_assets(self):
        self._crop_probe()
        text = self._add_probe_rule()
        self.assertIn("I_PROBE", text)
        rules = json.loads((RES_DIR / "image.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["itemName"], "probe")
        assets_py = (TASK_DIR / "assets.py").read_text(encoding="utf-8")
        self.assertIn("I_PROBE = RuleImage", assets_py)

        # 再次写入不同 itemName，验证合并而非覆盖
        self._crop_probe("probe2")
        asset.add_rule(
            {
                "task": TASK,
                "json_relpath": "res/image.json",
                "rule_type": "image",
                "rule": {
                    "itemName": "probe2",
                    "imageName": "res_probe2.png",
                    "roiFront": "500,300,60,40",
                    "roiBack": "500,300,60,40",
                },
            }
        )
        rules = json.loads((RES_DIR / "image.json").read_text(encoding="utf-8"))
        self.assertEqual({r["itemName"] for r in rules}, {"probe", "probe2"})

    def test_read_rule_file_lists_constants(self):
        self._crop_probe()
        self._add_probe_rule()
        text = asset.read_rule_file({"task": TASK, "json_relpath": "res/image.json"})
        self.assertIn("probe", text)
        self.assertIn("I_PROBE", text)

    def test_add_rule_rejects_bad_roi(self):
        with self.assertRaises(McpToolError):
            asset.add_rule(
                {
                    "task": TASK,
                    "json_relpath": "res/image.json",
                    "rule_type": "image",
                    "rule": {"itemName": "bad", "imageName": "x.png", "roiFront": "1,2,3"},
                }
            )

    def test_test_rule_matches_synthetic_template(self):
        self._crop_probe()
        self._add_probe_rule()
        text = asset.test_rule(
            {
                "task": TASK,
                "json_relpath": "res/image.json",
                "rule_type": "image",
                "rule": {
                    "itemName": "probe",
                    "imageName": "res_probe.png",
                    "roiFront": "500,300,60,40",
                    "roiBack": "500,300,60,40",
                    "threshold": 0.8,
                },
                "image_path": str(self.source_path),
            }
        )
        self.assertIn("matched=True", text)
        self.assertIn("500,300", text)

    def test_add_list_rule_replaces_file(self):
        text = asset.add_rule(
            {
                "task": TASK,
                "json_relpath": "res/list.json",
                "rule_type": "list",
                "rules": [{"itemName": "道馆", "roiFront": "33,253,42,26"}],
                "list_meta": {
                    "name": "test_list",
                    "direction": "vertical",
                    "type": "ocr",
                    "roiBack": "35,157,37,250",
                    "description": "测试列表",
                },
            }
        )
        self.assertIn("L_TEST_LIST", text)
        data = json.loads((RES_DIR / "list.json").read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "test_list")


if __name__ == "__main__":
    unittest.main()
