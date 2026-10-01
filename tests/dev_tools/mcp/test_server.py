# This Python file uses the following encoding: utf-8
import io
import json
import unittest

from dev_tools.mcp.errors import McpToolError
from dev_tools.mcp.protocol import METHOD_NOT_FOUND, JsonRpcError
from dev_tools.mcp.server import ToolRegistry, require_int, require_str, run_stdio, schema_object


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.tool("echo", "回显文本", schema_object({"text": {"type": "string"}}, ["text"]))
    def echo(args):
        return require_str(args, "text")

    @registry.tool("boom", "抛业务错误", schema_object({}))
    def boom(args):
        raise McpToolError("故意失败", hint="这是提示")

    @registry.tool("crash", "抛未知异常", schema_object({}))
    def crash(args):
        raise RuntimeError("boom!")

    return registry


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.registry = make_registry()

    def test_initialize_echoes_client_protocol_version(self):
        resp = self.registry.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}}
        )
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", resp["result"]["capabilities"])
        self.assertEqual(resp["result"]["serverInfo"]["name"], "oas-mcp")

    def test_initialize_without_version_uses_default(self):
        resp = self.registry.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")

    def test_initialized_notification_has_no_response(self):
        self.assertIsNone(
            self.registry.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_ping_returns_empty_result(self):
        resp = self.registry.dispatch({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_tools_list(self):
        resp = self.registry.dispatch({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        names = {tool["name"] for tool in resp["result"]["tools"]}
        self.assertEqual(names, {"echo", "boom", "crash"})
        for tool in resp["result"]["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_tools_call_text_result(self):
        resp = self.registry.dispatch(
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "echo", "arguments": {"text": "你好"}}}
        )
        result = resp["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(result["content"][0]["text"], "你好")

    def test_tools_call_business_error_sets_is_error(self):
        resp = self.registry.dispatch(
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "boom"}}
        )
        result = resp["result"]
        self.assertTrue(result["isError"])
        self.assertIn("故意失败", result["content"][0]["text"])
        self.assertIn("这是提示", result["content"][0]["text"])

    def test_tools_call_unknown_tool_sets_is_error(self):
        resp = self.registry.dispatch(
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nope"}}
        )
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("未知工具", resp["result"]["content"][0]["text"])

    def test_tools_call_unexpected_exception_sets_is_error(self):
        resp = self.registry.dispatch(
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "crash"}}
        )
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("RuntimeError", resp["result"]["content"][0]["text"])

    def test_unknown_method_raises_method_not_found(self):
        with self.assertRaises(JsonRpcError) as ctx:
            self.registry.dispatch({"jsonrpc": "2.0", "id": 9, "method": "nope"})
        self.assertEqual(ctx.exception.code, METHOD_NOT_FOUND)

    def test_missing_method_raises_invalid_request(self):
        with self.assertRaises(JsonRpcError):
            self.registry.dispatch({"jsonrpc": "2.0", "id": 10})

    def test_duplicate_tool_registration_raises(self):
        with self.assertRaises(ValueError):
            self.registry.register(self.registry._tools["echo"])


class ParamHelperTest(unittest.TestCase):
    def test_require_str_strips_and_validates(self):
        self.assertEqual(require_str({"a": "  x  "}, "a"), "x")
        with self.assertRaises(McpToolError):
            require_str({}, "a")
        with self.assertRaises(McpToolError):
            require_str({"a": ""}, "a")
        with self.assertRaises(McpToolError):
            require_str({"a": 1}, "a")
        self.assertEqual(require_str({}, "a", required=False, default="d"), "d")

    def test_require_int_coerces_and_bounds(self):
        self.assertEqual(require_int({"n": "5"}, "n"), 5)
        self.assertEqual(require_int({}, "n", default=3), 3)
        with self.assertRaises(McpToolError):
            require_int({"n": "abc"}, "n")
        with self.assertRaises(McpToolError):
            require_int({"n": 0}, "n", minimum=1)


class RunStdioTest(unittest.TestCase):
    def test_loop_roundtrip_and_notification_skipped(self):
        payload = (
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        )
        stdout = io.BytesIO()
        run_stdio(make_registry(), stdin=io.BytesIO(payload), stdout=stdout)
        lines = [json.loads(line) for line in stdout.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(lines[1]["id"], 2)
        self.assertIn("tools", lines[1]["result"])

    def test_bad_json_line_gets_parse_error(self):
        stdout = io.BytesIO()
        run_stdio(make_registry(), stdin=io.BytesIO(b"not-json\n"), stdout=stdout)
        resp = json.loads(stdout.getvalue().decode("utf-8").splitlines()[0])
        self.assertEqual(resp["error"]["code"], -32700)
        self.assertIsNone(resp["id"])

    def test_eof_exits_loop(self):
        stdout = io.BytesIO()
        run_stdio(make_registry(), stdin=io.BytesIO(b""), stdout=stdout)
        self.assertEqual(stdout.getvalue(), b"")


if __name__ == "__main__":
    unittest.main()
