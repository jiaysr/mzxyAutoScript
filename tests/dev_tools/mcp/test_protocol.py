# This Python file uses the following encoding: utf-8
import json
import unittest

from dev_tools.mcp import protocol


class ParseMessageTest(unittest.TestCase):
    def test_parse_ok(self):
        msg = protocol.parse_message(b'{"jsonrpc":"2.0","id":1,"method":"ping"}')
        self.assertEqual(msg["method"], "ping")
        self.assertEqual(msg["id"], 1)

    def test_parse_tolerates_crlf_and_spaces(self):
        msg = protocol.parse_message(b'  {"jsonrpc":"2.0","method":"ping"}\r\n')
        self.assertEqual(msg["method"], "ping")

    def test_parse_empty_raises_parse_error(self):
        with self.assertRaises(protocol.JsonRpcError) as ctx:
            protocol.parse_message(b"  \r\n")
        self.assertEqual(ctx.exception.code, protocol.PARSE_ERROR)

    def test_parse_bad_json_raises_parse_error(self):
        with self.assertRaises(protocol.JsonRpcError) as ctx:
            protocol.parse_message(b"{not json")
        self.assertEqual(ctx.exception.code, protocol.PARSE_ERROR)

    def test_parse_non_object_raises_invalid_request(self):
        with self.assertRaises(protocol.JsonRpcError) as ctx:
            protocol.parse_message(b"[1,2,3]")
        self.assertEqual(ctx.exception.code, protocol.INVALID_REQUEST)


class EncodeMessageTest(unittest.TestCase):
    def test_encode_is_single_line_utf8(self):
        raw = protocol.encode_message({"jsonrpc": "2.0", "id": 1, "result": {"text": "中文"}})
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.count(b"\n"), 1)
        self.assertIn("中文".encode("utf-8"), raw)

    def test_encoded_message_is_decodable(self):
        raw = protocol.encode_message({"jsonrpc": "2.0", "id": 2, "result": {"a": 1}})
        self.assertEqual(json.loads(raw.decode("utf-8"))["result"]["a"], 1)

    def test_error_response_shape(self):
        resp = protocol.JsonRpcError(-32601, "no method", {"x": 1}).to_response(7)
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 7)
        self.assertEqual(resp["error"]["code"], -32601)
        self.assertEqual(resp["error"]["message"], "no method")
        self.assertEqual(resp["error"]["data"], {"x": 1})

    def test_error_response_without_data(self):
        resp = protocol.JsonRpcError(-32603, "internal").to_response(None)
        self.assertNotIn("data", resp["error"])
        self.assertIsNone(resp["id"])


if __name__ == "__main__":
    unittest.main()
