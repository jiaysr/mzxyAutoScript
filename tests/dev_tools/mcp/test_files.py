# This Python file uses the following encoding: utf-8
import shutil
import unittest

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.runtime import PROJECT_ROOT
from dev_tools.mcp.tools import files

TMP_REL = "log/mcp/_unittest_files"
TMP_DIR = PROJECT_ROOT / TMP_REL


class PathGuardTest(unittest.TestCase):
    def test_resolve_relative_ok(self):
        target = files.resolve_path("config/template.json", must_exist=True)
        self.assertTrue(target.name == "template.json")

    def test_absolute_path_rejected(self):
        with self.assertRaises(McpToolError):
            files.resolve_path("D:/tmp/x.txt")

    def test_parent_traversal_rejected(self):
        with self.assertRaises(McpToolError):
            files.resolve_path("../../Windows/System32/drivers/etc/hosts")

    def test_missing_path_raises_when_required(self):
        with self.assertRaises(McpToolError):
            files.resolve_path("no_such_file_zzz.txt", must_exist=True)

    def test_write_denied_in_git_dir(self):
        target = PROJECT_ROOT / ".git" / "mcp_test.txt"
        with self.assertRaises(McpToolError):
            files.ensure_writable(target)

    def test_delete_outside_whitelist_rejected(self):
        with self.assertRaises(McpToolError):
            files.ensure_deletable(PROJECT_ROOT / "module" / "logger.py")

    def test_delete_inside_whitelist_allowed(self):
        files.ensure_deletable(PROJECT_ROOT / TMP_REL / "x.txt")


class FileToolsTest(unittest.TestCase):
    def setUp(self):
        if TMP_DIR.exists():
            shutil.rmtree(TMP_DIR)
        TMP_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if TMP_DIR.exists():
            shutil.rmtree(TMP_DIR)

    def test_write_then_read_roundtrip(self):
        result = files.write_file({"path": f"{TMP_REL}/hello.txt", "content": "你好\nworld\n"})
        self.assertIn("hello.txt", result)
        text = files.read_file({"path": f"{TMP_REL}/hello.txt"})
        self.assertEqual(text, "你好\nworld\n")

    def test_read_missing_file_raises(self):
        with self.assertRaises(McpToolError):
            files.read_file({"path": f"{TMP_REL}/missing.txt"})

    def test_list_files_shows_created_file(self):
        files.write_file({"path": f"{TMP_REL}/a.py", "content": "print(1)\n"})
        text = files.list_files({"path": TMP_REL, "depth": 2, "include_log": True})
        self.assertIn("a.py", text)

    def test_delete_file(self):
        files.write_file({"path": f"{TMP_REL}/b.txt", "content": "x"})
        files.delete_file({"path": f"{TMP_REL}/b.txt"})
        with self.assertRaises(McpToolError):
            files.resolve_path(f"{TMP_REL}/b.txt", must_exist=True)

    def test_search_code_finds_token(self):
        files.write_file({"path": f"{TMP_REL}/token.py", "content": "ZZ_MM_XX_TOKEN = 1\n"})
        text = files.search_code(
            {"query": "ZZ_MM_XX_TOKEN", "scope": [TMP_REL], "max_results": 10}
        )
        self.assertIn("ZZ_MM_XX_TOKEN", text)
        self.assertIn("token.py", text)

    def test_search_code_bad_regex_raises(self):
        with self.assertRaises(McpToolError):
            files.search_code({"query": "([", "scope": [TMP_REL]})


if __name__ == "__main__":
    unittest.main()
