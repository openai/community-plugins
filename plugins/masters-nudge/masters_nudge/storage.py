"""Bounded SQLite handoff between Codex hooks and the review worker."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from .contracts import FEEDBACK_LIMIT, SILENCE_LIMIT, SessionRef, ToolFault, json_text
from .evidence import BASELINE_MAX_BYTES, capture_file_baseline
from .runtime import HOOK_TIMEOUT_SEC, PROVIDER_TIMEOUT_SEC

FINALIZATION_RESERVE_SEC = 20
JOURNAL_MAX_BYTES = 128 * 1024 * 1024
ROUND_LIMIT = 64
WORK_LIMIT = 64
ROUND_IDLE_SEC = 24 * 60 * 60
TASK_MAX_BYTES = 64 * 1024
ID_MAX_BYTES = 128
AUDIT_LIMIT = 128
FEEDBACK_SLOT_BYTES = 1024
SCHEMA_VERSION = 1
LEGACY_TABLES = ("rounds", "attempts", "batches", "judgment_events")
FAULT_KINDS = frozenset({
    "configuration", "input", "internal", "interrupted", "mcp_audit", "mcp_budget",
    "mcp_git", "mcp_input", "mcp_path", "mcp_read", "mcp_startup", "output", "provider",
    "storage", "superseded", "timeout", "workspace",
})

SCHEMA = (
    """CREATE TABLE work_rounds(
        session TEXT PRIMARY KEY, turn TEXT NOT NULL, round_id TEXT NOT NULL UNIQUE,
        goal TEXT NOT NULL, request TEXT NOT NULL, file_baseline TEXT NOT NULL,
        expires REAL NOT NULL, fault TEXT,
        feedback_count INTEGER NOT NULL DEFAULT 0, silence_count INTEGER NOT NULL DEFAULT 0)""",
    f"""CREATE TABLE work_reviews(
        id TEXT PRIMARY KEY, round_id TEXT NOT NULL REFERENCES work_rounds(round_id) ON DELETE CASCADE,
        started REAL NOT NULL, deadline REAL NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('queued','running','ready','sending','sent')),
        feedback BLOB NOT NULL CHECK(length(feedback)={FEEDBACK_SLOT_BYTES}), model TEXT NOT NULL)""",
    "CREATE INDEX work_reviews_round ON work_reviews(round_id)",
    f"""CREATE TABLE recent_reviews(
        ordinal INTEGER PRIMARY KEY,
        slot INTEGER NOT NULL UNIQUE CHECK(slot >= 0 AND slot < {AUDIT_LIMIT}),
        id TEXT NOT NULL, round_id TEXT NOT NULL, started REAL NOT NULL, finished REAL NOT NULL,
        outcome TEXT NOT NULL, reason TEXT, delivered INTEGER, model TEXT NOT NULL)""",
)


class Journal:
    def __init__(self, directory: Path, *, hook_timeout_sec: float = HOOK_TIMEOUT_SEC,
                 provider_timeout_sec: float = PROVIDER_TIMEOUT_SEC, poll_interval: float = 0.05,
                 model: str = ""):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "feedback.sqlite3"
        self.hook_timeout_sec = hook_timeout_sec
        self.provider_timeout_sec = provider_timeout_sec
        self.poll_interval = poll_interval
        self.model = model[:80]
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                self._initialize(db)
        with self.transaction() as (db, audit):
            self._recover(db, audit)

    @staticmethod
    def _initialize(db):
        # The legacy journal can already be at its page cap. Free its pages
        # before creating the new tables, all in one recoverable transaction.
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            raise ToolFault("storage", "不支援的工作庫版本")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {*LEGACY_TABLES, "sqlite_sequence"}:
            raise ToolFault("storage", "工作庫包含無法識別的資料表")
        for table in LEGACY_TABLES:
            db.execute(f"DROP TABLE IF EXISTS {table}")
        for statement in SCHEMA:
            db.execute(statement)
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def connect(self, *, timeout=5):
        with closing(sqlite3.connect(self.path, timeout=timeout)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            max_pages = JOURNAL_MAX_BYTES // page_size
            if db.execute(f"PRAGMA max_page_count={max_pages}").fetchone()[0] > max_pages:
                raise ToolFault("storage", "工作庫超過 SQLite 頁數上限")
            with db:
                yield db

    @contextmanager
    def transaction(self):
        audit = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db, audit
        if audit:
            try:
                self._record(audit)
            except sqlite3.Error:
                # Summary is observational and never gates a state transition.
                pass

    def _record(self, records):
        with self.connect(timeout=0) as db:
            db.execute("BEGIN IMMEDIATE")
            for item, outcome, reason, finished in records:
                # Derive the next slot from the latest entry; the ring's
                # timestamps can repeat or go backwards, so use an ordinal.
                ordinal = db.execute("SELECT COALESCE(MAX(rowid),0)+1 FROM recent_reviews").fetchone()[0]
                db.execute("DELETE FROM recent_reviews WHERE slot=?", ((ordinal - 1) % AUDIT_LIMIT,))
                db.execute(
                    "INSERT INTO recent_reviews(ordinal,slot,id,round_id,started,finished,outcome,reason,delivered,model) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (ordinal, (ordinal - 1) % AUDIT_LIMIT, item["id"], item["round_id"],
                     item["started"], finished, outcome, reason, None if outcome == "feedback" else 0, item["model"]),
                )

    @staticmethod
    def _close(db, audit, round_id, reason, now):
        for item in db.execute(
            "SELECT id,round_id,started,model FROM work_reviews WHERE round_id=? AND state IN ('queued','running')",
            (round_id,),
        ):
            audit.append((item, "fault", reason, now))
        db.execute("DELETE FROM work_reviews WHERE round_id=?", (round_id,))
        db.execute("UPDATE work_rounds SET fault=? WHERE round_id=?", (reason, round_id))

    def _recover(self, db, audit):
        now = time.time()
        expired = db.execute(
            "SELECT DISTINCT round_id FROM work_reviews WHERE deadline<=? AND state IN ('queued','running')", (now,),
        ).fetchall()
        for row in expired:
            self._close(db, audit, row["round_id"], "timeout", now)
        db.execute("DELETE FROM work_reviews WHERE deadline<=? AND state IN ('ready','sending')", (now,))
        db.execute(
            "DELETE FROM work_rounds WHERE expires<=? AND NOT EXISTS("
            "SELECT 1 FROM work_reviews WHERE round_id=work_rounds.round_id AND state<>'sent')", (now,),
        )

    @staticmethod
    def _check_size(value, limit):
        try:
            if isinstance(value, str) and len(value.encode("utf-8")) <= limit:
                return
        except UnicodeError:
            pass
        raise ToolFault("input_size", "回合識別碼或任務文字超出儲存上限")

    def start_round(self, session: SessionRef, request: str, goal: str = ""):
        for value, limit in ((session.session_id, ID_MAX_BYTES), (session.turn_id, ID_MAX_BYTES),
                             (request, TASK_MAX_BYTES), (goal, TASK_MAX_BYTES)):
            self._check_size(value, limit)
        # Git runs outside the writer transaction. Recheck the observed round
        # before using the captured baseline to supersede it.
        with self.connect() as db:
            observed = self._current(db, session)
        original = goal or (observed["goal"] if observed else request)
        baseline = (observed["file_baseline"] if observed and observed["goal"] == original
                    else json_text(capture_file_baseline(session)))
        if len(baseline.encode("utf-8")) > BASELINE_MAX_BYTES:
            baseline = "{}"

        fault = None
        with self.transaction() as (db, audit):
            self._recover(db, audit)
            current = self._current(db, session)
            if current and (not observed or current["round_id"] != observed["round_id"]):
                fault = ToolFault("busy", "另一個提示已更新此會話")
            elif not current and db.execute("SELECT COUNT(*) FROM work_rounds").fetchone()[0] >= ROUND_LIMIT:
                fault = ToolFault("busy", "Nudge 的會話工作空間已滿")
            else:
                if current:
                    self._close(db, audit, current["round_id"], "superseded", time.time())
                    db.execute("DELETE FROM work_rounds WHERE session=?", (session.session_id,))
                elif observed:
                    # Recovery reclaimed the old task while we were preparing.
                    if observed["goal"] == original:
                        baseline = "{}"
                db.execute(
                    "INSERT INTO work_rounds(session,turn,round_id,goal,request,file_baseline,expires) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (session.session_id, session.turn_id, uuid.uuid4().hex, original, request,
                     baseline, time.time() + ROUND_IDLE_SEC),
                )
        if fault:
            raise fault

    @staticmethod
    def _current(db, session):
        return db.execute("SELECT * FROM work_rounds WHERE session=?", (session.session_id,)).fetchone()

    @staticmethod
    def _budget_exhausted(row):
        return row["feedback_count"] >= FEEDBACK_LIMIT or row["silence_count"] >= SILENCE_LIMIT

    def begin(self, session: SessionRef, *, skip_provider=False, cancel_event=None):
        attempt = uuid.uuid4().hex
        started = time.time()
        wait_started = time.monotonic()
        fault = None
        with self.transaction() as (db, audit):
            self._recover(db, audit)
            row = self._current(db, session)
            if row is None:
                fault = ToolFault("input", "找不到本輪任務交接，請重新提交提示")
            elif row["turn"] != session.turn_id:
                return None
            elif row["fault"]:
                fault = ToolFault(row["fault"], "這個回合已結束")
            elif skip_provider or self._budget_exhausted(row):
                return None
            elif db.execute("SELECT COUNT(*) FROM work_reviews WHERE state<>'sent'").fetchone()[0] >= WORK_LIMIT:
                fault = ToolFault("busy", "Nudge 的審查工作空間已滿")
            else:
                db.execute(
                    "INSERT INTO work_reviews VALUES(?,?,?,?, 'queued',zeroblob(?),?)",
                    (attempt, row["round_id"], started, started + self.hook_timeout_sec, FEEDBACK_SLOT_BYTES, self.model),
                )
                self._touch(db, row["round_id"])
        if fault:
            raise fault

        while True:
            fault = None
            with self.transaction() as (db, audit):
                self._recover(db, audit)
                current = self._current(db, session)
                if not current or current["round_id"] != row["round_id"]:
                    return None
                if cancel_event is not None and cancel_event.is_set() and not current["fault"]:
                    self._close(db, audit, row["round_id"], "interrupted", time.time())
                    fault = ToolFault("interrupted", "審查已取消")
                elif current["fault"]:
                    fault = ToolFault(current["fault"], "這個回合已結束")
                else:
                    head = db.execute(
                        "SELECT id,state FROM work_reviews WHERE round_id=? AND state IN ('queued','running') "
                        "ORDER BY state='running' DESC,rowid LIMIT 1", (row["round_id"],),
                    ).fetchone()
                    if not head or head["id"] == attempt:
                        if not head or self._budget_exhausted(current):
                            db.execute("DELETE FROM work_reviews WHERE id=?", (attempt,))
                            return None
                        remaining = self.hook_timeout_sec - (time.monotonic() - wait_started) - FINALIZATION_RESERVE_SEC
                        if remaining <= 0:
                            self._close(db, audit, row["round_id"], "timeout", time.time())
                            fault = ToolFault("timeout", "排隊後已無審查時間")
                        else:
                            return self._claim(db, current, attempt, min(self.provider_timeout_sec, remaining))
            if fault:
                raise fault
            time.sleep(self.poll_interval)

    @staticmethod
    def _claim(db, row, attempt, timeout):
        db.execute("UPDATE work_reviews SET state='running',deadline=? WHERE id=?",
                   (time.time() + timeout + FINALIZATION_RESERVE_SEC, attempt))
        previous = [json.loads(item["feedback"].rstrip(b" ")) for item in db.execute(
            "SELECT feedback FROM work_reviews WHERE round_id=? AND state='sent' ORDER BY rowid", (row["round_id"],),
        )]
        return attempt, {**dict(row), "previous_nudges": previous, "provider_timeout_sec": timeout}

    @staticmethod
    def _touch(db, round_id):
        db.execute("UPDATE work_rounds SET expires=? WHERE round_id=?", (time.time() + ROUND_IDLE_SEC, round_id))

    def finish(self, session: SessionRef, attempt: str, task: dict, outcome: str, *, feedback=None, reason=None):
        if outcome not in ("feedback", "silence", "fault") or (outcome == "feedback") != (feedback is not None):
            raise ValueError("invalid review result")
        body = json_text(feedback).encode("utf-8") if feedback is not None else b""
        if len(body) > FEEDBACK_SLOT_BYTES:
            raise ValueError("feedback exceeds reserved bytes")
        reason = (reason if reason in FAULT_KINDS else "internal") if outcome == "fault" else None
        now = time.time()
        with self.transaction() as (db, audit):
            self._recover(db, audit)
            current = self._current(db, session)
            item = db.execute("SELECT * FROM work_reviews WHERE id=? AND state='running'", (attempt,)).fetchone()
            if not current or not item or current["round_id"] != item["round_id"] or item["round_id"] != task["round_id"]:
                return False
            round_id = item["round_id"]
            audit.append((item, outcome, reason, now))
            self._touch(db, round_id)
            if outcome == "fault":
                db.execute("DELETE FROM work_reviews WHERE id=?", (attempt,))
                self._close(db, audit, round_id, reason, now)
            elif outcome == "silence":
                db.execute("DELETE FROM work_reviews WHERE id=?", (attempt,))
                db.execute("UPDATE work_rounds SET silence_count=silence_count+1 WHERE round_id=?", (round_id,))
            else:
                db.execute("UPDATE work_reviews SET state='ready',feedback=?,deadline=? WHERE id=?",
                           (body.ljust(FEEDBACK_SLOT_BYTES), now + FINALIZATION_RESERVE_SEC, attempt))
                db.execute("UPDATE work_rounds SET feedback_count=feedback_count+1 WHERE round_id=?", (round_id,))
            return True

    def authorize_delivery(self, attempt: str) -> bool:
        # This commit orders delivery against an Interrupt or a replacement
        # prompt. Never hold a database lock while writing to the host.
        with self.connect() as db:
            return db.execute(
                "UPDATE work_reviews SET state='sending',deadline=? WHERE id=? AND state='ready' AND deadline>?",
                (time.time() + FINALIZATION_RESERVE_SEC, attempt, time.time()),
            ).rowcount == 1

    def delivered(self, attempt: str):
        with self.connect() as db:
            db.execute("UPDATE work_reviews SET state='sent' WHERE id=? AND state='sending'", (attempt,))
        try:
            with self.connect(timeout=0) as db:
                db.execute("UPDATE recent_reviews SET delivered=1 WHERE id=? AND outcome='feedback'", (attempt,))
        except sqlite3.Error:
            pass

    def interrupt(self, session: SessionRef):
        with self.transaction() as (db, audit):
            row = self._current(db, session)
            if row and row["turn"] == session.turn_id and not row["fault"]:
                self._close(db, audit, row["round_id"], "interrupted", time.time())

    def is_current_round(self, session: SessionRef, round_id: str) -> bool:
        with self.connect() as db:
            row = self._current(db, session)
            return bool(row and row["round_id"] == round_id)

    def cancelled(self, attempt: str) -> bool:
        with self.connect() as db:
            return db.execute(
                "SELECT 1 FROM work_reviews WHERE id=? AND state='running' AND deadline>?", (attempt, time.time()),
            ).fetchone() is None

    def unresolved(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,round_id,started,state FROM work_reviews WHERE state IN ('queued','running') ORDER BY rowid",
            )]

    def recent(self, limit: int = 20) -> list[dict]:
        return recent_nudges(self.path.parent, limit=limit)


def recent_nudges(data_dir: Path, *, limit: int = 20) -> list[dict]:
    path = data_dir / "feedback.sqlite3"
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            return []
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT id,round_id,started,finished,outcome,reason,delivered,model "
            "FROM recent_reviews ORDER BY rowid DESC LIMIT ?", (max(0, min(limit, AUDIT_LIMIT)),),
        )]
