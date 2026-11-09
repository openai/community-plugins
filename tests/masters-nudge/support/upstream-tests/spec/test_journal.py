from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from masters_nudge.contracts import SessionRef, ToolFault
from masters_nudge.storage import AUDIT_LIMIT, JOURNAL_MAX_BYTES, Journal, recent_nudges


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = SessionRef("s", "host-turn", str(self.root))

    def test_summary_failure_does_not_undo_the_result_or_its_quota(self):
        journal = Journal(self.root)
        journal.start_round(self.session, "task")
        with mock.patch.object(journal, "_record", side_effect=sqlite3.OperationalError("audit unavailable")):
            for _ in range(2):
                attempt, task = journal.begin(self.session)
                self.assertTrue(journal.finish(self.session, attempt, task, "silence"))
            self.assertIsNone(journal.begin(self.session))
        self.assertEqual(journal.recent(), [])
        journal.start_round(self.session, "next prompt")
        self.assertIsNotNone(journal.begin(self.session))

    def test_summary_overwrite_and_expiry_keep_storage_bounded(self):
        journal = Journal(self.root)
        recorded = []
        sizes = []
        for index in range(AUDIT_LIMIT * 3):
            session = SessionRef(str(index), "t", str(self.root))
            with mock.patch("masters_nudge.storage.ROUND_IDLE_SEC", 10):
                with mock.patch("masters_nudge.storage.time.time", return_value=index * 11):
                    journal.start_round(session, "task")
                    attempt, task = journal.begin(session)
                    self.assertTrue(journal.finish(session, attempt, task, "silence"))
                    recorded.append(attempt)
            if (index + 1) % AUDIT_LIMIT == 0:
                sizes.append(journal.path.stat().st_size)
        self.assertEqual([item["id"] for item in journal.recent(AUDIT_LIMIT)], recorded[-AUDIT_LIMIT:][::-1])
        self.assertLessEqual(sizes[-1] - sizes[-2], 4096)
        with journal.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM work_rounds").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM work_reviews").fetchone()[0], 0)

    def test_round_pressure_preserves_live_state_and_expired_turn_is_rejected(self):
        journal = Journal(self.root)
        other = SessionRef("other", "new", str(self.root))
        with mock.patch("masters_nudge.storage.ROUND_LIMIT", 1), \
             mock.patch("masters_nudge.storage.ROUND_IDLE_SEC", 10):
            with mock.patch("masters_nudge.storage.time.time", return_value=100):
                journal.start_round(self.session, "task")
                attempt, task = journal.begin(self.session)
                with self.assertRaisesRegex(ToolFault, "已滿"):
                    journal.start_round(other, "other")
                self.assertTrue(journal.finish(self.session, attempt, task, "silence"))
            with mock.patch("masters_nudge.storage.time.time", return_value=111):
                journal.start_round(other, "other")
                with self.assertRaisesRegex(ToolFault, "找不到"):
                    journal.begin(self.session)
                self.assertIsNotNone(journal.begin(other))

    def test_full_legacy_database_cutover_rolls_back_then_can_review(self):
        path = self.root / "feedback.sqlite3"
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        with db:
            max_pages = JOURNAL_MAX_BYTES // 4096
            db.execute("PRAGMA page_size=4096")
            db.execute(f"PRAGMA max_page_count={max_pages}")
            db.execute("CREATE TABLE attempts(payload BLOB)")
            db.execute("INSERT INTO attempts VALUES(zeroblob(?))", (120 * 1024 * 1024,))
            while db.execute("PRAGMA page_count").fetchone()[0] < max_pages:
                db.execute("INSERT INTO attempts VALUES(zeroblob(4050))")
            self.assertEqual(db.execute("PRAGMA freelist_count").fetchone()[0], 0)
        self.assertEqual(path.stat().st_size, JOURNAL_MAX_BYTES)
        self.assertEqual(recent_nudges(self.root), [])
        with mock.patch("masters_nudge.storage.SCHEMA", ("invalid SQL",)):
            with self.assertRaises(sqlite3.Error):
                Journal(self.root)
        self.assertGreater(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
        db.close()

        journal = Journal(self.root)
        journal.start_round(self.session, "new prompt")
        attempt, task = journal.begin(self.session)
        self.assertTrue(journal.finish(self.session, attempt, task, "silence"))
        with journal.connect() as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertGreater(db.execute("PRAGMA freelist_count").fetchone()[0], 0)
        self.assertEqual(journal.recent()[0]["outcome"], "silence")
        self.assertEqual(path.stat().st_size, JOURNAL_MAX_BYTES)
