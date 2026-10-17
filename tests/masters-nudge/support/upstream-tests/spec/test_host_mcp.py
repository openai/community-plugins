import io
import json
import unittest
import threading
import os
from unittest.mock import Mock, ANY, patch

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

    def readline(self, size=-1):
        line = super().readline(size)
        if not line:
            if not self.output.finished.wait(5):
                raise RuntimeError("Server did not reply")
        return line


class HostMcpTests(unittest.TestCase):
    def test_full_queue_and_duplicate_ids_cannot_hide_cancellation(self):
        read_fd, write_fd = os.pipe()
        input_stream = os.fdopen(read_fd, 'r', encoding='utf-8')
        writer = os.fdopen(write_fd, 'w', encoding='utf-8')
        self.addCleanup(input_stream.close)
        self.addCleanup(writer.close)
        started = threading.Event()
        output = FlushingOutput()
        adapter = Mock()
        def review(arguments, cancel_event):
            started.set()
            if not cancel_event.wait(5):
                raise RuntimeError('Cancellation was blocked')
            return None
        adapter.process.side_effect = review
        core = Mock()
        errors = []
        def serve():
            try:
                mcp_entry.serve(core, adapter, input_stream=input_stream, output_stream=output)
            except Exception as exc:
                errors.append(exc)
        def send(value):
            writer.write(json.dumps(value) + '\n')
            writer.flush()
        def call(request_id):
            return {'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
                    'params': {'name': 'review_patch', 'arguments': {}}}
        with patch.object(mcp_entry, 'MAX_PENDING_REQUESTS', 2):
            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                send(call(1))
                self.assertTrue(started.wait(5))
                send(call(2))
                send(call(3))
                send(call(1))
                send({'method': 'notifications/cancelled', 'params': {'requestId': 2}})
                send({'method': 'notifications/cancelled', 'params': {'requestId': 1}})
                writer.close()
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                replies = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(sum(r['error']['code'] == -32600 for r in replies), 2)
                self.assertEqual(sum(r['error']['code'] == -32800 for r in replies), 2)
                self.assertEqual(adapter.process.call_count, 1)
                core.journal.delivered.assert_not_called()
            finally:
                writer.close()
                worker.join(5)

    def test_oversize_message_is_drained_before_next_request(self):
        output = io.StringIO()
        core = Mock()
        with patch.object(mcp_entry, 'MAX_REQUEST_CHARS', 64):
            mcp_entry.serve(core, Mock(), input_stream=io.StringIO(
                'x' * 200 + '\n' + json.dumps({'id': 1, 'method': 'ping'}) + '\n'), output_stream=output)
        core.log_error.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())['result'], {})

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
