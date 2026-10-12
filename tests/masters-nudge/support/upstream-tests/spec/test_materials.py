import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from masters_nudge.contracts import MATERIAL_MAX_CHARS, MaterialLine, MaterialPacket, ToolFault, material_lines
from masters_nudge.evidence import fit_packet
from masters_nudge.prompting import delivery_text
from masters_nudge.provider_contract import parse_feedback


class ContractTests(unittest.TestCase):
    def feedback(self):
        return {"feedback": {"observed": "x:1 x := y", "why": "sources(y) = 2", "structure": "consumer <- y",
                             "required": "保留現有查詢結果"}}

    def test_actor_delivery_uses_fixed_formal_fields(self):
        feedback = parse_feedback(json.dumps(self.feedback()))
        lines = delivery_text(feedback).splitlines()
        self.assertEqual(
            lines[:5],
            ["Masters’ Nudge", "OBSERVED: x:1 x := y", "WHY: sources(y) = 2",
             "STRUCTURE: consumer <- y", "REQUIRED: 保留現有查詢結果"],
        )
        self.assertTrue("\n".join(lines[5:]).strip(), "Actor guidance is missing")

    def test_output_limits_and_partial_feedback(self):
        import copy
        valid = self.feedback()
        self.assertIsNotNone(parse_feedback(json.dumps(valid)))
        invalid = []
        for key, value in (("observed", "x" * 41), ("why", ""), ("why", "x" * 26),
                           ("structure", "x" * 62), ("required", ""), ("required", "x" * 36)):
            item = copy.deepcopy(valid)
            item["feedback"][key] = value
            invalid.append(item)
        for obsolete, value in (("criterion", 4), ("evidence", []), ("violates", "legacy"), ("do_not_break", "legacy")):
            item = copy.deepcopy(valid)
            item["feedback"][obsolete] = value
            invalid.append(item)
        item = copy.deepcopy(valid)
        item["feedback"].update(observed="x" * 40, why="x" * 25,
                                structure="x" * 61, required="x" * 35)
        self.assertEqual(len(parse_feedback(json.dumps(item)).message), 200)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ToolFault):
                parse_feedback(json.dumps(item))

    def test_schema_field_limits_make_the_combined_limit_unrepresentable(self):
        schema = json.loads((Path(__file__).resolve().parents[2] / "nudge-schema.json").read_text(encoding="utf-8"))
        feedback = schema["properties"]["feedback"]["anyOf"][1]["properties"]
        limits = [feedback[name]["maxLength"] for name in ("observed", "why", "structure", "required")]
        labels = len("OBSERVED: \nWHY: \nSTRUCTURE: \nREQUIRED: ")
        self.assertEqual(limits, [40, 25, 61, 35])
        self.assertEqual(sum(limits) + labels, 200)

    def test_before_structure_is_dropped_before_task_or_current_code(self):
        before = MaterialLine("before_structure", "old.py", 1, "x" * 21000)
        current = MaterialLine("current_structure", "now.py", 1, "current = True")
        task = MaterialLine("task_contract", "request", 1, "keep behavior")
        packet = fit_packet(MaterialPacket((task, before, current), "repo"))
        self.assertEqual(packet.lines, (task, current))

    def test_null_is_valid_but_broken_output_is_fault(self):
        self.assertIsNone(parse_feedback('{"feedback":null}'))
        for raw in ('', '{}', '{"feedback":null,"error":"timeout"}', '{"nudge":null}'):
            with self.subTest(raw=raw), self.assertRaises(ToolFault):
                parse_feedback(raw)

    def test_packet_can_have_empty_sources_and_never_silently_truncates_contract(self):
        packet = MaterialPacket(material_lines("task_contract", "task/latest", "保留 A"), "repo")
        self.assertEqual(json.loads(fit_packet(packet).render())["before_structure"], [])
        oversized = MaterialPacket(material_lines("task_contract", "task/latest", "x" * 21000), "repo")
        self.assertEqual(fit_packet(oversized), oversized)

    def test_equal_goal_and_request_have_one_latest_contract_copy(self):
        from masters_nudge.contracts import SessionRef
        from masters_nudge.evidence import build_packet
        task = "same contract"
        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True)
            packet = build_packet(SessionRef("s", "t", raw), {"goal": task, "request": task}, ())
        contracts = [line for line in packet.lines if line.source == "task_contract"]
        self.assertEqual([(line.path, line.text) for line in contracts], [("task/latest", task)])

    def test_missing_file_baseline_is_unknown_not_inferred_from_patch_or_current_file(self):
        from masters_nudge.contracts import SessionRef, ToolCompleted
        from masters_nudge.evidence import build_packet
        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True)
            (Path(raw) / "test_new.py").write_text("test()", encoding="utf-8")
            patch = "*** Begin Patch\n*** Add File: test_new.py\n+test()\n*** End Patch"
            packet = build_packet(SessionRef("s", "t", raw), {"goal": "g", "request": "r"},
                                  (ToolCompleted("edit", "apply_patch", patch, "Success"),))
        self.assertEqual(json.loads(packet.categories()["file_origin"][0]["lines"][0]), {
            "existed_at_task_start": None, "baseline_turn_id": None,
        })

    def test_small_changed_file_is_carried_as_current_structure(self):
        from masters_nudge.contracts import SessionRef, ToolCompleted
        from masters_nudge.evidence import build_packet

        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True)
            (Path(raw) / "job.py").write_text("source = job.status\nretry_state = source\n", encoding="utf-8")
            patch = f"*** Begin Patch\n*** Update File: {Path(raw) / 'job.py'}\n@@\n+retry_state = source\n*** End Patch"
            event = ToolCompleted("edit-1", "apply_patch", {"command": patch}, "Success")
            packet = build_packet(SessionRef("s", "t", raw),
                                  {"goal": "keep one source", "request": "keep one source"}, (event,))
            self.assertEqual(packet.categories()["current_structure"], [{
                "path": "job.py", "start": 1,
                "lines": ["source = job.status", "retry_state = source"],
            }])

    def test_ignored_file_is_not_carried_as_current_structure(self):
        from masters_nudge.contracts import SessionRef, ToolCompleted
        from masters_nudge.evidence import build_packet

        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True)
            (Path(raw) / ".gitignore").write_text("secret.py\n", encoding="utf-8")
            (Path(raw) / "secret.py").write_text("secret = True\n", encoding="utf-8")
            patch = "*** Begin Patch\n*** Update File: secret.py\n@@\n+secret = True\n*** End Patch"
            event = ToolCompleted("edit-1", "apply_patch", {"command": patch}, "Success")
            packet = build_packet(SessionRef("s", "t", raw), {"goal": "g", "request": "r"}, (event,))
            self.assertEqual(packet.categories()["current_structure"], [])

    def test_changed_file_is_not_added_when_it_exceeds_shared_budget(self):
        from masters_nudge.contracts import SessionRef, ToolCompleted
        from masters_nudge.evidence import build_packet

        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True)
            (Path(raw) / "job.py").write_text("x" * 19000 + "\n", encoding="utf-8")
            patch = "*** Begin Patch\n*** Update File: job.py\n@@\n+x\n*** End Patch"
            event = ToolCompleted("edit-1", "apply_patch", {"command": patch}, "Success")
            task = "t" * 1200
            packet = build_packet(SessionRef("s", "t", raw), {"goal": task, "request": task}, (event,))
            self.assertEqual(packet.categories()["current_structure"], [])
            self.assertLessEqual(packet.material_chars, 20000)

    def test_shared_budget_counts_materials_and_nudges_not_transport_metadata(self):
        packet = MaterialPacket(material_lines("task_contract", "task/latest", "保留 A"),
                                "repo", "metadata/" * 3000,
                                previous_nudges=({"observed": "x := y", "structure": "view <- y"},))
        self.assertIs(fit_packet(packet), packet)
        data = json.loads(packet.render())
        data.pop("workspace")
        data.pop("transcript_path")
        from masters_nudge.contracts import json_text
        self.assertEqual(packet.material_chars, len(json_text(data)))

    def test_previous_nudges_remain_when_optional_before_structure_is_dropped(self):
        history = ({"observed": "copy := source", "structure": "view <- source"},)
        before = MaterialLine("before_structure", "old.py", 1, "x" * 21000)
        packet = fit_packet(MaterialPacket((before,), "repo", previous_nudges=history))
        self.assertEqual(packet.lines, ())
        self.assertEqual(json.loads(packet.render())["previous_nudges"], list(history))

    def test_packet_groups_one_path_instead_of_repeating_it_for_every_line(self):
        packet = MaterialPacket(material_lines("batch_change", "tool/edit/input", "a\nb\nc"), "repo")
        category = packet.categories()["batch_change"]
        self.assertEqual(category, [{"path": "tool/edit/input", "start": 1,
                                     "lines": ["a", "b", "c"]}])
        self.assertEqual(packet.render().count("tool/edit/input"), 1)

    def test_required_patch_and_task_survive_material_budget_boundary(self):
        from masters_nudge.contracts import SessionRef, ToolCompleted
        from masters_nudge.evidence import build_packet
        template = "*** Begin Patch\n*** Add File: new.py\n+{}\n*** End Patch"
        task = {"goal": "keep behavior", "request": "keep behavior"}
        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True)
            session = SessionRef("s", "t", raw)
            def packet_for(patch):
                return build_packet(session, task, (ToolCompleted("edit-1", "apply_patch", patch, "Success"),))
            base_size = packet_for(template.format("")).material_chars
            for offset in (-1, 0, 1):
                with self.subTest(offset=offset):
                    target = MATERIAL_MAX_CHARS + offset
                    patch = template.format("x" * (target - base_size))
                    packet = packet_for(patch)
                    self.assertEqual(packet.material_chars, target)
                    self.assertEqual([line.text for line in packet.lines if line.source == "batch_change"],
                                     patch.splitlines())
                    self.assertEqual([line.text for line in packet.lines if line.source == "task_contract"],
                                     [task["request"]])

    def test_oversize_success_output_is_compressed_without_losing_task_or_change(self):
        from masters_nudge.contracts import SessionRef, ToolCompleted
        from masters_nudge.evidence import build_packet
        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True)
            patch = "*** Begin Patch\n*** Update File: job.py\n@@\n+x=1\n*** End Patch"
            event = ToolCompleted("edit-1", "apply_patch", patch,
                                  {"exit_code": 0, "output": "ok\n" * 12000})
            packet = build_packet(SessionRef("s", "t", raw), {"goal": "保留行為", "request": "修改 x"}, (event,))
            self.assertLessEqual(packet.material_chars, 20000)
            self.assertIn("x=1", packet.render())
            self.assertIn("修改 x", packet.render())
            self.assertIn("output_omitted", packet.render())

    def test_tool_result_string_fields_are_verbatim_lines_not_escaped_json(self):
        from masters_nudge.contracts import SessionRef, ToolCompleted
        from masters_nudge.evidence import build_packet
        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True)
            patch = "*** Begin Patch\n*** Update File: job.py\n@@\n+x=1\n*** End Patch"
            event = ToolCompleted("edit-1", "apply_patch", patch,
                                  {"exit_code": 0, "output": 'profile["enabled"] = True\r\nnext()'})
            packet = build_packet(SessionRef("s", "t", raw), {"goal": "g", "request": "r"}, (event,))
            output = [line for line in packet.lines if line.path == "tool/edit-1/output/output"]
            self.assertEqual([line.text for line in output], ['profile["enabled"] = True', "next()"])
            self.assertEqual([line.line for line in output], [1, 2])


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        from masters_nudge.read_only_repo_mcp import RepositoryTools
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "job.py").write_text("status = 1\nretry_state = status\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
        (self.repo / "secret.txt").write_text("hidden", encoding="utf-8")
        self.tools = RepositoryTools(self.repo, 1000, self.root / "reads.jsonl")

    def test_read_returns_exact_file_line_and_original_text(self):
        result = self.tools.call("read_file", {"path": "job.py", "start_line": 2, "end_line": 2})
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["segments"], [{"path": "job.py", "start": 2,
                                                 "lines": ["retry_state = status"]}])

    def test_compact_read_preserves_room_for_followup_search(self):
        from masters_nudge.read_only_repo_mcp import RepositoryTools
        source = "".join(f"value_{i} = {i}\n" for i in range(100))
        (self.repo / "long.py").write_text(source, encoding="utf-8")
        tools = RepositoryTools(self.repo, 4000, self.root / "compact-reads.jsonl")
        first = json.loads(tools.call("read_file", {"path": "long.py"})["content"][0]["text"])
        self.assertFalse(first["truncated"])
        self.assertEqual(first["segments"], [{"path": "long.py", "start": 1,
                                               "lines": source.splitlines()}])
        second = json.loads(tools.call("search_repo", {"query": "value_99"})["content"][0]["text"])
        self.assertEqual(second["segments"], [{"path": "long.py", "start": 100,
                                                "lines": ["value_99 = 99"]}])

    def test_bad_line_types_report_and_record_fault_instead_of_crashing(self):
        for value in (None, "1", [], {}):
            with self.subTest(value=value):
                result = self.tools.call("read_file", {"path": "job.py", "start_line": value, "end_line": 2})
                self.assertTrue(result["isError"])
        from masters_nudge.read_only_repo_mcp import read_audit
        records = read_audit(self.root / "reads.jsonl")
        self.assertEqual(len(records), 4)
        self.assertTrue(all(record["fault"] for record in records))
        result = self.tools.call("read_file", {"path": "job.py", "start_line": 1, "end_line": 1})
        self.assertFalse(result["isError"])

    def test_search_and_read_share_persistent_budget(self):
        from masters_nudge.read_only_repo_mcp import RepositoryTools
        last = None
        for _ in range(12):
            last = self.tools.call("search_repo", {"query": "status"})
        restarted = RepositoryTools(self.repo, 1000, self.root / "reads.jsonl")
        last = restarted.call("read_file", {"path": "job.py"})
        entries = [json.loads(line) for line in (self.root / "reads.jsonl").read_text().splitlines()]
        self.assertLessEqual(sum(len(row["text"]) for row in entries), 1000)
        self.assertTrue(any(row["exhausted"] for row in entries))
        self.assertTrue(json.loads(last["content"][0]["text"])["exhausted"])

    def test_search_does_not_spawn_one_git_process_per_file(self):
        for number in range(30):
            (self.repo / f"module_{number}.py").write_text("ordinary = 1\n", encoding="utf-8")
        with mock.patch.object(self.tools, "_git", wraps=self.tools._git) as git:
            result = self.tools.call("search_repo", {"query": "missing_identifier"})
        self.assertFalse(result["isError"])
        self.assertLessEqual(git.call_count, 1)

    def test_outside_ignored_and_write_requests_are_faults(self):
        for name, args in (("read_file", {"path": "../secret"}), ("read_file", {"path": "secret.txt"}),
                           ("write_file", {"path": "job.py", "content": "x"})):
            result = self.tools.call(name, args)
            self.assertTrue(result["isError"])
        self.assertEqual((self.repo / "job.py").read_text(), "status = 1\nretry_state = status\n")
