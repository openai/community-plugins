"""Durable terminal facts for rounds, eligible patches and Provider results."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .contracts import FEEDBACK_LIMIT, SILENCE_LIMIT, SessionRef, ToolFault, json_text
from .evidence import capture_file_baseline
from .runtime import HOOK_TIMEOUT_SEC, PROVIDER_TIMEOUT_SEC

FINALIZATION_RESERVE_SEC = 20


class Journal:
    def __init__(self, directory: Path, *, hook_timeout_sec: float = HOOK_TIMEOUT_SEC,
                 provider_timeout_sec: float = PROVIDER_TIMEOUT_SEC, poll_interval: float = 0.05):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "feedback.sqlite3"
        self.hook_timeout_sec = hook_timeout_sec
        self.provider_timeout_sec = provider_timeout_sec
        self.poll_interval = poll_interval
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rounds(
                    session TEXT PRIMARY KEY, turn TEXT NOT NULL, goal TEXT NOT NULL, request TEXT NOT NULL,
                    round_id TEXT NOT NULL,
                    skipped_test_patches INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS attempts(
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, turn TEXT NOT NULL,
                    started REAL NOT NULL, finished REAL, outcome TEXT, delivered INTEGER NOT NULL DEFAULT 0,
                    detail TEXT NOT NULL DEFAULT '{}', round_id TEXT NOT NULL,
                    batch_id TEXT);
                CREATE TABLE IF NOT EXISTS batches(
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, turn TEXT NOT NULL,
                    received REAL NOT NULL, payload TEXT NOT NULL, round_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS judgment_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session TEXT NOT NULL, turn TEXT NOT NULL, round_id TEXT NOT NULL,
                    happened REAL NOT NULL, kind TEXT NOT NULL,
                    attempt TEXT NOT NULL, batch_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS judgment_events_round_seq
                    ON judgment_events(round_id, seq);
                CREATE INDEX IF NOT EXISTS judgment_events_attempt_seq
                    ON judgment_events(attempt, seq);
            """)
            db.execute("BEGIN IMMEDIATE")
            for table in ("rounds", "attempts"):
                columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                if "round_id" not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN round_id TEXT NOT NULL DEFAULT ''")
                if table == "rounds":
                    if "file_baseline" not in columns:
                        db.execute("ALTER TABLE rounds ADD COLUMN file_baseline TEXT NOT NULL DEFAULT '{}'")
                    if "skipped_test_patches" not in columns:
                        if "skipped_new_test_patches" in columns:
                            db.execute("ALTER TABLE rounds RENAME COLUMN skipped_new_test_patches TO skipped_test_patches")
                        else:
                            db.execute("ALTER TABLE rounds ADD COLUMN skipped_test_patches INTEGER NOT NULL DEFAULT 0")
            attempt_columns = {row["name"] for row in db.execute("PRAGMA table_info(attempts)")}
            if "batch_id" not in attempt_columns:
                db.execute("ALTER TABLE attempts ADD COLUMN batch_id TEXT")
            batch_columns = {row["name"] for row in db.execute("PRAGMA table_info(batches)")}
            if "id" not in batch_columns:
                db.execute("ALTER TABLE batches ADD COLUMN id TEXT")
            if "round_id" not in batch_columns:
                db.execute("ALTER TABLE batches ADD COLUMN round_id TEXT NOT NULL DEFAULT ''")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS attempts_batch_id "
                "ON attempts(batch_id) WHERE batch_id IS NOT NULL"
            )
            for row in db.execute("SELECT session,turn FROM rounds WHERE round_id=''").fetchall():
                round_id = uuid.uuid4().hex
                db.execute("UPDATE rounds SET round_id=? WHERE session=?", (round_id, row["session"]))
                db.execute("UPDATE attempts SET round_id=? WHERE session=? AND turn=? AND round_id=''",
                           (round_id, row["session"], row["turn"]))
            for row in db.execute("SELECT rowid FROM batches WHERE id IS NULL OR id=''").fetchall():
                db.execute("UPDATE batches SET id=? WHERE rowid=?", (uuid.uuid4().hex, row["rowid"]))
            self._migrate_judgment_events(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def start_round(self, session: SessionRef, request: str, goal: str = ""):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM rounds WHERE session=?", (session.session_id,)).fetchone()
            original = goal or (old["goal"] if old else request)
            baseline = (old["file_baseline"] if old and old["goal"] == original
                        else json_text(capture_file_baseline(session)))
            db.execute(
                "INSERT OR REPLACE INTO rounds(session,turn,goal,request,round_id,file_baseline) VALUES(?,?,?,?,?,?)",
                (session.session_id, session.turn_id, original, request, uuid.uuid4().hex, baseline),
            )

    @staticmethod
    def _append_event(db, *, session: str, turn: str, round_id: str, kind: str,
                      attempt: str, batch_id: str, payload: object = None,
                      happened: float | None = None):
        db.execute(
            "INSERT INTO judgment_events(session,turn,round_id,happened,kind,attempt,batch_id,payload) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (session, turn, round_id, time.time() if happened is None else happened,
             kind, attempt, batch_id, json_text({} if payload is None else payload)),
        )

    def _migrate_judgment_events(self, db):
        if db.execute("SELECT 1 FROM judgment_events LIMIT 1").fetchone():
            return
        for row in db.execute("SELECT * FROM attempts ORDER BY started,id").fetchall():
            batch_id = row["batch_id"] or f"legacy-{row['id']}"
            self._append_event(
                db, session=row["session"], turn=row["turn"], round_id=row["round_id"],
                kind="started", attempt=row["id"], batch_id=batch_id, happened=row["started"],
            )
            if row["outcome"] is not None:
                self._append_event(
                    db, session=row["session"], turn=row["turn"], round_id=row["round_id"],
                    kind="finished", attempt=row["id"], batch_id=batch_id,
                    happened=row["finished"] or row["started"],
                    payload={"outcome": row["outcome"], "detail": json.loads(row["detail"])},
                )

    @staticmethod
    def _fold_events(rows) -> list[dict]:
        attempts = {}
        for row in rows:
            item = attempts.setdefault(row["attempt"], {
                "id": row["attempt"], "batch_id": row["batch_id"],
                "session": row["session"], "turn": row["turn"], "round_id": row["round_id"],
                "queued": None, "started": None, "finished": None, "outcome": None, "detail": {},
            })
            if row["kind"] == "queued":
                item["queued"] = row["happened"]
            elif row["kind"] == "started":
                item["started"] = row["happened"]
            elif row["kind"] == "finished":
                value = json.loads(row["payload"])
                item.update(finished=row["happened"], outcome=value.get("outcome"),
                            detail=value.get("detail") if isinstance(value.get("detail"), dict) else {})
            elif row["kind"] == "skipped":
                item.update(finished=row["happened"], outcome="skipped")
        return list(attempts.values())

    def _round_events(self, db, round_id: str) -> list[dict]:
        return self._fold_events(db.execute(
            "SELECT * FROM judgment_events WHERE round_id=? ORDER BY seq", (round_id,),
        ))

    def _finish_event(self, db, item: dict, outcome: str, detail: dict):
        finished = time.time()
        self._append_event(
            db, session=item["session"], turn=item["turn"], round_id=item["round_id"],
            kind="finished", attempt=item["id"], batch_id=item["batch_id"],
            happened=finished, payload={"outcome": outcome, "detail": detail},
        )
        db.execute(
            "INSERT OR IGNORE INTO attempts("
            "id,session,turn,started,finished,outcome,delivered,detail,round_id,batch_id"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
            (item["id"], item["session"], item["turn"], item["started"] or item["queued"],
             finished, outcome, 0, json_text(detail), item["round_id"], item["batch_id"]),
        )

    def _skip_queued(self, db, items: list[dict], cause: str):
        """Make every follower terminal when its round can no longer produce judgments."""
        for item in items:
            if (item["id"] != cause and item["queued"] is not None
                    and item["started"] is None and item["finished"] is None):
                self._append_event(
                    db, session=item["session"], turn=item["turn"], round_id=item["round_id"],
                    kind="skipped", attempt=item["id"], batch_id=item["batch_id"],
                    payload={"cause": cause},
                )

    @staticmethod
    def _fault(items: list[dict]) -> dict | None:
        return next((item for item in items if item["outcome"] == "fault"), None)

    @staticmethod
    def _tool_fault(item: dict) -> ToolFault:
        fault = item["detail"].get("fault", {})
        return ToolFault(fault.get("kind", "interrupted"),
                         fault.get("detail", "前一個 Provider 判斷失敗"))

    @staticmethod
    def _budget_exhausted(db, round_id: str) -> bool:
        # Preserve the accounting of test-only silences recorded by older versions.
        counts = {item["outcome"]: item["n"] for item in db.execute(
            "SELECT outcome, COUNT(*) n FROM attempts WHERE round_id=? "
            "AND (outcome='feedback' OR (outcome='silence' "
            "AND COALESCE(json_extract(detail,'$.judgment_scope'),'implementation')<>'task_contract')) "
            "GROUP BY outcome", (round_id,),
        )}
        return counts.get("feedback", 0) >= FEEDBACK_LIMIT or counts.get("silence", 0) >= SILENCE_LIMIT

    def begin(self, session: SessionRef, payload: object, *,
              skip_provider: bool = False) -> tuple[str, dict] | None:
        """Queue one patch and claim it only after every earlier judgment is terminal."""
        wait_started = time.monotonic()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM rounds WHERE session=?", (session.session_id,)).fetchone()
            if row is None:
                raise ToolFault("input", "缺少本輪使用者要求事件")
            if row["turn"] != session.turn_id:
                return None
            fault = self._fault(self._round_events(db, row["round_id"]))
            if fault is not None:
                raise self._tool_fault(fault)
            if not skip_provider and self._budget_exhausted(db, row["round_id"]):
                return None
            batch_id = uuid.uuid4().hex
            attempt = uuid.uuid4().hex
            started = time.time()
            db.execute(
                "INSERT INTO batches(id,session,turn,received,payload,round_id) VALUES(?,?,?,?,?,?)",
                (batch_id, session.session_id, session.turn_id, started, json_text(payload), row["round_id"]),
            )
            if skip_provider:
                db.execute("UPDATE rounds SET skipped_test_patches=skipped_test_patches+1 "
                           "WHERE session=?", (session.session_id,))
                self._append_event(
                    db, session=session.session_id, turn=session.turn_id, round_id=row["round_id"],
                    kind="skipped", attempt=attempt, batch_id=batch_id,
                    payload={"reason": "verification_tool"}, happened=started,
                )
                return None
            self._append_event(
                db, session=session.session_id, turn=session.turn_id, round_id=row["round_id"],
                kind="queued", attempt=attempt, batch_id=batch_id, happened=started,
            )

        while True:
            should_wait = False
            fault_to_raise = None
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM rounds WHERE session=?", (session.session_id,)).fetchone()
                if current is None or current["turn"] != session.turn_id:
                    return None
                items = self._round_events(db, current["round_id"])
                fault = self._fault(items)
                if fault is not None:
                    self._skip_queued(db, items, fault["id"])
                    fault_to_raise = self._tool_fault(fault)
                else:
                    active = next((item for item in items if item["started"] is not None
                                   and item["finished"] is None), None)
                    if active is not None:
                        deadline = active["started"] + self.provider_timeout_sec + FINALIZATION_RESERVE_SEC
                        if time.time() >= deadline:
                            detail = {"fault": {"kind": "timeout",
                                               "detail": "前一個 Provider 判斷未在期限內留下結果"}}
                            self._finish_event(db, active, "fault", detail)
                            self._skip_queued(db, items, active["id"])
                            fault_to_raise = ToolFault("timeout", detail["fault"]["detail"])
                        else:
                            should_wait = True
                    else:
                        queued = [item for item in items if item["queued"] is not None
                                  and item["started"] is None and item["finished"] is None]
                        queued.sort(key=lambda item: item["queued"])
                        own = next(item for item in items if item["id"] == attempt)
                        if queued and queued[0]["id"] == attempt:
                            if self._budget_exhausted(db, current["round_id"]):
                                self._append_event(
                                    db, session=own["session"], turn=own["turn"], round_id=own["round_id"],
                                    kind="skipped", attempt=attempt, batch_id=batch_id,
                                )
                                return None
                            elapsed = time.monotonic() - wait_started
                            remaining = self.hook_timeout_sec - elapsed - FINALIZATION_RESERVE_SEC
                            if remaining <= 0:
                                detail = {"fault": {"kind": "timeout",
                                                   "detail": "等待前一個 Provider 判斷後已無執行時間"}}
                                self._finish_event(db, own, "fault", detail)
                                self._skip_queued(db, items, own["id"])
                                fault_to_raise = ToolFault("timeout", detail["fault"]["detail"])
                            else:
                                claim_time = time.time()
                                self._append_event(
                                    db, session=own["session"], turn=own["turn"],
                                    round_id=own["round_id"], kind="started", attempt=attempt,
                                    batch_id=batch_id, happened=claim_time,
                                )
                                previous_nudges = [json.loads(row["raw_output"])["feedback"]
                                    for row in db.execute(
                                        "SELECT json_extract(detail,'$.raw_output') AS raw_output "
                                        "FROM attempts WHERE round_id=? AND outcome='feedback' AND delivered=1 "
                                        "ORDER BY started,id", (current["round_id"],),
                                    )]
                                return attempt, {**dict(current), "batch_id": batch_id,
                                                 "previous_nudges": previous_nudges,
                                                 "started": claim_time,
                                                 "provider_timeout_sec": min(self.provider_timeout_sec, remaining)}
                        should_wait = True
            if fault_to_raise is not None:
                raise fault_to_raise
            if should_wait:
                time.sleep(self.poll_interval)

    def finish(
        self, session: SessionRef, attempt: str, task: dict, outcome: str, detail: dict,
    ) -> bool:
        """Atomically add one terminal Provider fact for the batch that caused it."""
        batch_id = task.get("batch_id")
        round_id = task.get("round_id")
        if not isinstance(batch_id, str) or not isinstance(round_id, str):
            return False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            items = self._round_events(db, round_id)
            item = next((value for value in items if value["id"] == attempt
                         and value["batch_id"] == batch_id), None)
            if item is None or item["started"] is None or item["finished"] is not None:
                return False
            self._finish_event(db, item, outcome, detail)
            current = db.execute(
                "SELECT round_id FROM rounds WHERE session=?",
                (session.session_id,),
            ).fetchone()
            return bool(current and current["round_id"] == round_id)

    def delivered(self, attempt: str):
        with self.connect() as db:
            db.execute("UPDATE attempts SET delivered=1 WHERE id=? AND outcome='feedback'", (attempt,))

    def unresolved(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT b.* FROM batches b WHERE b.round_id<>'' AND EXISTS("
                "SELECT 1 FROM judgment_events q WHERE q.batch_id=b.id AND q.kind IN ('queued','started')"
                ") AND NOT EXISTS(SELECT 1 FROM judgment_events f WHERE f.batch_id=b.id "
                "AND f.kind IN ('finished','skipped')) ORDER BY b.received"
            )]

    def recent(self, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            return [dict(row) | {"detail": json.loads(row["detail"])} for row in db.execute(
                "SELECT * FROM attempts ORDER BY started DESC LIMIT ?",
                (max(0, min(limit, 200)),),
            )]


def append_error(path: Path, component: str, message: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json_text({"time": time.time(), "component": component, "message": message}) + "\n")


def recent_nudges(data_dir: Path, *, limit: int = 20) -> list[dict]:
    if not (data_dir / "feedback.sqlite3").exists():
        return []
    return Journal(data_dir).recent(limit)
