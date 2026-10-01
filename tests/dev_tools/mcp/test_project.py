# This Python file uses the following encoding: utf-8
import shutil
import unittest

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.runtime import PROJECT_ROOT
from dev_tools.mcp.tools import docs, project

TMP_REL = "log/mcp/_unittest_docs"
TMP_DIR = PROJECT_ROOT / TMP_REL


class ProjectToolsTest(unittest.TestCase):
    def test_list_configs_contains_oas1(self):
        text = project.list_configs({})
        self.assertIn("oas1", text)

    def test_select_config_ok(self):
        text = project.select_config({"name": "oas1"})
        self.assertIn("oas1", text)

    def test_select_config_unknown_raises(self):
        with self.assertRaises(McpToolError):
            project.select_config({"name": "__no_such_config__"})

    def test_list_tasks_contains_orochi(self):
        text = project.list_tasks({})
        self.assertIn("Orochi", text)
        self.assertIn("script_task.py", text)

    def test_get_project_info_smoke(self):
        text = project.get_project_info({})
        self.assertIn("项目根", text)
        self.assertIn("oas1", text)


class DocsToolsTest(unittest.TestCase):
    def setUp(self):
        if TMP_DIR.exists():
            shutil.rmtree(TMP_DIR)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        (TMP_DIR / "demo.md").write_text("# 示例文档\n\n内容", encoding="utf-8")
        self._original = docs.DOCS_ROOT
        docs.DOCS_ROOT = TMP_DIR

    def tearDown(self):
        docs.DOCS_ROOT = self._original
        if TMP_DIR.exists():
            shutil.rmtree(TMP_DIR)

    def test_list_docs(self):
        text = docs.list_docs({})
        self.assertIn("demo.md", text)
        self.assertIn("示例文档", text)

    def test_read_doc(self):
        text = docs.read_doc({"name": "demo"})
        self.assertIn("示例文档", text)

    def test_read_doc_missing_raises(self):
        with self.assertRaises(McpToolError):
            docs.read_doc({"name": "nope"})

    def test_read_doc_rejects_traversal(self):
        with self.assertRaises(McpToolError):
            docs.read_doc({"name": "../README.md"})


if __name__ == "__main__":
    unittest.main()
