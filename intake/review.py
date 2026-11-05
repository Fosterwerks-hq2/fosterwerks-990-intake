"""The review queue: proposed prospect records wait here until a person approves or rejects.

Kept in its own database (data/review.sqlite) so rebuilding the search index never touches
decisions. Same shape as Navira's review queue: pending -> approved | rejected, with who and when.
"""
import getpass
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .cohort import DATA
from .models import ProspectRecord, ReviewItem

DB = Path(os.environ.get("INTAKE_REVIEW_DB", DATA / "review.sqlite"))  # tests use a temp DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS review_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ein         TEXT NOT NULL,
    name        TEXT NOT NULL,
    record      TEXT NOT NULL,          -- ProspectRecord JSON
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    proposed_at TEXT NOT NULL,
    proposed_by TEXT NOT NULL,          -- which client proposed it (agent run, Claude Code...)
    actioned_at TEXT,
    actioned_by TEXT,
    note        TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _item(r) -> ReviewItem:
    d = dict(r)
    d["record"] = ProspectRecord.model_validate_json(d["record"])
    return ReviewItem(**d)


def propose(record: ProspectRecord, proposed_by: str) -> ReviewItem:
    with connect() as con:
        cur = con.execute("INSERT INTO review_queue (ein, name, record, proposed_at, proposed_by) "
                          "VALUES (?,?,?,?,?)", (record.ein, record.name, record.model_dump_json(),
                                                 _now(), proposed_by))
        return _item(con.execute("SELECT * FROM review_queue WHERE id = ?",
                                 (cur.lastrowid,)).fetchone())


def items(status: str | None = "pending") -> list[ReviewItem]:
    with connect() as con:
        sql, args = "SELECT * FROM review_queue", []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        return [_item(r) for r in con.execute(sql + " ORDER BY id", args)]


def decide(item_id: int, status: str, note: str | None = None, by: str | None = None) -> ReviewItem:
    """Approve or reject a pending item. Only pending items can be decided."""
    if status not in ("approved", "rejected"):
        raise ValueError("status must be approved or rejected")
    with connect() as con:
        n = con.execute("UPDATE review_queue SET status=?, actioned_at=?, actioned_by=?, note=? "
                        "WHERE id=? AND status='pending'",
                        (status, _now(), by or getpass.getuser(), note, item_id)).rowcount
        if not n:
            raise ValueError(f"no pending item {item_id}")
        return _item(con.execute("SELECT * FROM review_queue WHERE id=?", (item_id,)).fetchone())
