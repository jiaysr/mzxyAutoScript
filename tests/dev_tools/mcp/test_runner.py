# This Python file uses the following encoding: utf-8
import sys
import time
import unittest

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.tools.runner import RunManager


class RunManagerTest(unittest.TestCase):
    def setUp(self):
        self.manager = RunManager()

    def tearDown(self):
        self.manager.stop_all()

    def _start(self, code: str, timeout: int = 0):
        return self.manager.start_command(
            [sys.executable, "-c", code],
            run_id=f"test_{time.time_ns()}",
            task="__unittest__",
            config="oas1",
            timeout_seconds=timeout,
        )

    def test_captures_log_and_status(self):
        record = self._start("print('hello_mcp'); print('中文输出')")
        deadline = time.time() + 60
        while time.time() < deadline:
            self.manager.refresh(record)
            if record.status != "running":
                break
            time.sleep(0.2)
        self.assertEqual(record.status, "exited")
        text, _ = self.manager.read_log(record.run_id, lines=50)
        self.assertIn("hello_mcp", text)
        self.assertIn("中文输出", text)

    def test_keyword_filter(self):
        record = self._start("print('alpha'); print('beta'); print('alpha2')")
        deadline = time.time() + 60
        while time.time() < deadline and record.status == "running":
            self.manager.refresh(record)
            time.sleep(0.2)
        text, _ = self.manager.read_log(record.run_id, keyword="alpha")
        self.assertIn("alpha", text)
        self.assertNotIn("beta", text)

    def test_stop_running_process(self):
        record = self._start("import time; print('started'); time.sleep(120)")
        time.sleep(2)
        self.manager.refresh(record)
        self.assertEqual(record.status, "running")
        self.manager.stop(record.run_id)
        self.assertEqual(record.status, "stopped")

    def test_timeout_marks_timeout(self):
        record = self._start("import time; time.sleep(120)", timeout=2)
        deadline = time.time() + 60
        while time.time() < deadline:
            self.manager.refresh(record)
            if record.status != "running":
                break
            time.sleep(0.5)
        self.assertEqual(record.status, "timeout")

    def test_read_log_missing_run_raises(self):
        with self.assertRaises(McpToolError):
            self.manager.read_log("nope_run_id")

    def test_list_runs_and_error_screenshots_tool(self):
        record = self._start("print('x')")
        time.sleep(1)
        self.manager.refresh(record)
        text = self.manager.describe_runs()
        self.assertIn(record.run_id, text)


if __name__ == "__main__":
    unittest.main()
