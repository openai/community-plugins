import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from masters_nudge.contracts import SessionRef, ToolFault
from masters_nudge.storage import Journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = SessionRef("s", "host-turn", str(self.root))

    def test_attempt_exists_only_after_a_terminal_result(self):
        journal = Journal(self.root)
        journal.start_round(self.session, "first")
        first, task = journal.begin(self.session, [{"tool_use_id": "patch-1"}])

        self.assertEqual(journal.recent(), [])
        self.assertEqual([row["id"] for row in journal.unresolved()], [task["batch_id"]])
        self.assertTrue(journal.finish(self.session, first, task, "silence", {"raw_output": "done"}))
        self.assertEqual(journal.recent()[0]["outcome"], "silence")
        self.assertEqual(journal.unresolved(), [])

    def test_new_user_round_can_start_while_old_result_becomes_historical(self):
        journal = Journal(self.root)
        journal.start_round(self.session, "first")
        first, first_task = journal.begin(self.session, [{"tool_use_id": "patch-1"}])
        self.assertIsNotNone(first)

        journal.start_round(self.session, "latest")
        second, task = Journal(self.root).begin(self.session, [{"tool_use_id": "patch-3"}])
        self.assertNotEqual(first, second)
        self.assertEqual(task["request"], "latest")
        self.assertFalse(journal.finish(self.session, first, first_task, "feedback", {"raw_output": "old"}))
        self.assertTrue(journal.finish(self.session, second, task, "silence", {"raw_output": "new"}))
        old = next(row for row in journal.recent() if row["id"] == first)
        self.assertEqual(old["outcome"], "feedback")
        self.assertEqual(old["delivered"], 0)
        self.assertFalse(journal.finish(self.session, second, task, "feedback", {"raw_output": "duplicate"}))

    def test_expired_judgment_is_a_durable_fault(self):
        journal = Journal(self.root, provider_timeout_sec=-20)
        journal.start_round(self.session, "first")
        first, _ = journal.begin(self.session, [{"tool_use_id": "patch-1"}])

        with self.assertRaisesRegex(ToolFault, "未在期限內留下結果"):
            Journal(self.root, provider_timeout_sec=-20).begin(
                self.session, [{"tool_use_id": "patch-2"}],
            )

        result = next(row for row in journal.recent() if row["id"] == first)
        self.assertEqual(result["outcome"], "fault")
        self.assertEqual(journal.unresolved(), [])
        with self.assertRaisesRegex(ToolFault, "未在期限內留下結果"):
            journal.begin(self.session, [{"tool_use_id": "patch-3"}])
        self.assertEqual(journal.unresolved(), [])

    def test_exhausted_hook_budget_is_a_durable_fault(self):
        journal = Journal(self.root)
        journal.start_round(self.session, "first")
        first, task = journal.begin(self.session, [{"tool_use_id": "patch-1"}])
        self.assertTrue(journal.finish(self.session, first, task, "silence", {}))

        with self.assertRaisesRegex(ToolFault, "已無執行時間"):
            Journal(self.root, hook_timeout_sec=0).begin(
                self.session, [{"tool_use_id": "patch-2"}],
            )

        self.assertEqual(journal.unresolved(), [])
        self.assertEqual(journal.recent()[0]["outcome"], "fault")

    def test_upgrade_keeps_old_evidence_and_current_quota(self):
        with sqlite3.connect(self.root / "feedback.sqlite3") as db:
            db.executescript("""
                CREATE TABLE rounds(session TEXT PRIMARY KEY,turn TEXT,goal TEXT,request TEXT);
                CREATE TABLE attempts(id TEXT PRIMARY KEY,session TEXT,turn TEXT,started REAL,
                    finished REAL,outcome TEXT,delivered INTEGER NOT NULL DEFAULT 0,
                    detail TEXT NOT NULL DEFAULT '{}');
                INSERT INTO rounds VALUES('s','host-turn','goal','old request');
            """)
            for n in range(2):
                db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)",
                           (str(n), "s", "host-turn", n, n+0.1, "silence", 0, json.dumps({"original": n})))
        db.close()
        journal = Journal(self.root)
        with journal.connect() as db:
            self.assertEqual(db.execute("SELECT file_baseline FROM rounds").fetchone()[0], "{}")
        self.assertIsNone(journal.begin(self.session, [{"tool_use_id": "limited"}]))
        self.assertEqual({row["detail"]["original"] for row in journal.recent()}, {0, 1})
        journal.start_round(self.session, "new request")
        attempt, task = journal.begin(self.session, [{"tool_use_id": "new"}])
        self.assertEqual(task["file_baseline"], "{}")
        self.assertTrue(journal.finish(self.session, attempt, task, "silence", {}))
        self.assertEqual(len(Journal(self.root).recent()), 3)

    def test_upgrade_preserves_skipped_test_count(self):
        journal = Journal(self.root)
        journal.start_round(self.session, "first")
        with journal.connect() as db:
            db.execute("UPDATE rounds SET skipped_test_patches=7")
            db.execute("ALTER TABLE rounds RENAME COLUMN skipped_test_patches TO skipped_new_test_patches")
        upgraded = Journal(self.root)
        with upgraded.connect() as db:
            row = db.execute("SELECT skipped_test_patches,request FROM rounds").fetchone()
        self.assertEqual(row["skipped_test_patches"], 7)
        self.assertEqual(row["request"], "first")

    def test_legacy_test_silences_leave_product_quota_unchanged(self):
        journal = Journal(self.root)
        journal.start_round(self.session, "first")
        for i in range(2):
            attempt, task = journal.begin(self.session, [{"tool_use_id": f"legacy-test-{i}"}])
            journal.finish(self.session, attempt, task, "silence", {"judgment_scope": "task_contract"})
        upgraded = Journal(self.root)
        for i in range(2):
            attempt, task = upgraded.begin(self.session, [{"tool_use_id": f"product-{i}"}])
            upgraded.finish(self.session, attempt, task, "silence", {})
        self.assertIsNone(upgraded.begin(self.session, [{"tool_use_id": "limited"}]))
        self.assertEqual(len(upgraded.recent()), 4)
