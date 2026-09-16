# This Python file uses the following encoding: utf-8
import json
import subprocess
import sys
import unittest

from dev_tools.mcp.runtime import PROJECT_ROOT


class EntrySmokeTest(unittest.TestCase):
    def test_stdio_roundtrip_through_real_process(self):
        payload = (
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        )
        completed = subprocess.run(
            [sys.executable, "dev_tools/mcp_server.py"],
            input=payload,
            capture_output=True,
            timeout=180,
            cwd=str(PROJECT_ROOT),
        )
        stdout_lines = [
            line
            for line in completed.stdout.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        messages = [json.loads(line) for line in stdout_lines]
        self.assertEqual(len(messages), 2, msg=f"stdout 被污染或缺少响应: {stdout_lines!r}")
        self.assertEqual(messages[0]["id"], 1)
        self.assertIn("serverInfo", messages[0]["result"])
        tools = messages[1]["result"]["tools"]
        names = {tool["name"] for tool in tools}
        expected = {
            "get_project_info", "list_configs", "select_config", "list_tasks",
            "list_files", "read_file", "write_file", "delete_file", "search_code",
            "list_docs", "read_doc",
            "screenshot", "ocr_region", "crop_asset", "add_rule", "read_rule_file", "test_rule",
            "run_task", "list_runs", "get_run_logs", "stop_run",
            "get_error_screenshots", "run_and_get_result",
        }
        self.assertEqual(names, expected)


if __name__ == "__main__":
    unittest.main()
