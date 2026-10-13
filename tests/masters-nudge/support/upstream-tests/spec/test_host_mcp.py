import io
import json
import unittest
import threading
from unittest.mock import Mock, ANY

import mcp_entry


class FlushingOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushed = False
        self.finished = threading.Event()

    def flush(self):
        self.flushed = True
        super().flush()
        self.finished.set()


class RequestStream(io.StringIO):
    def __init__(self, text, output):
        super().__init__(text)
        self.output = output

    def __next__(self):
        try:
            return super().__next__()
        except StopIteration:
            if not self.output.finished.wait(5):
                raise RuntimeError("Server did not reply")
            raise


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
            input_stream=RequestStream(json.dumps(request) + "\n", output),
            output_stream=output,
        )

        reply = json.loads(output.getvalue())
        hook_output = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(hook_output["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("STRUCTURE: z", hook_output["hookSpecificOutput"]["additionalContext"])
        journal.delivered.assert_called_once_with("attempt-1")
        adapter.process.assert_called_once_with(request["params"]["arguments"], cancel_event=ANY)

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
