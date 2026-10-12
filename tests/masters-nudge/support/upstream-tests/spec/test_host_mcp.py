import io
import json
import unittest
from unittest.mock import Mock

import mcp_entry


class FlushingOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushed = False

    def flush(self):
        self.flushed = True
        super().flush()


class HostMcpTests(unittest.TestCase):
    def test_tool_call_returns_hook_output_then_records_delivery(self):
        output = FlushingOutput()
        journal = Mock()
        journal.delivered.side_effect = lambda attempt: self.assertTrue(output.flushed)
        core = Mock(journal=journal)
        core.log_error = Mock()
        adapter = Mock()
        adapter.process.return_value = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "OBSERVED: x\nWHY: y\nSTRUCTURE: z",
            },
            "_masters_nudge": "attempt-1",
        }
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "review_patch",
                "arguments": {
                    "hook_event_name": "PostToolUse",
                    "session_id": "s",
                    "turn_id": "t",
                    "cwd": "C:/repo",
                    "transcript_path": "",
                    "tool_name": "apply_patch",
                    "tool_use_id": "call-1",
                    "tool_input": {"patch": "change"},
                    "tool_response": "Done",
                },
            },
        }

        mcp_entry.serve(
            core,
            adapter,
            input_stream=io.StringIO(json.dumps(request) + "\n"),
            output_stream=output,
        )

        reply = json.loads(output.getvalue())
        hook_output = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(hook_output["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("STRUCTURE: z", hook_output["hookSpecificOutput"]["additionalContext"])
        journal.delivered.assert_called_once_with("attempt-1")
        adapter.process.assert_called_once_with(request["params"]["arguments"])

    def test_server_advertises_only_the_post_patch_judgment(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        output = io.StringIO()
        mcp_entry.serve(Mock(), Mock(), input_stream=io.StringIO("\n".join(map(json.dumps, messages)) + "\n"), output_stream=output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "masters-nudge")
        self.assertEqual([tool["name"] for tool in replies[1]["result"]["tools"]], ["review_patch"])


if __name__ == "__main__":
    unittest.main()
