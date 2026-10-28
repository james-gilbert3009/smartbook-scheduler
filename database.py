"""
database.py - SQLite storage.

Two tables (services, appointments), indexes, and two database TRIGGERS that
refuse overlapping confirmed appointments. The triggers are a safety net: even
if the Python code had a bug, SQLite itself would still block double-booking.
"""
import os
import sqlite3
from contextlib import contextmanager

import config

DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "smartbook.db")


def set_db_path(path: str) -> None:
    """Point the app at another database file (used by the tests)."""
    global DB_PATH
    DB_PATH = path


SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL UNIQUE,
    description      TEXT    NOT NULL DEFAULT '',
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    price            REAL    NOT NULL CHECK (price >= 0)
);

CREATE TABLE IF NOT EXISTS appointments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name TEXT    NOT NULL,
    contact       TEXT    NOT NULL,
    service_id    INTEGER NOT NULL REFERENCES services(id),
    start_time    TEXT    NOT NULL,            -- 'YYYY-MM-DD HH:MM' local time
    end_time      TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'confirmed'
                  CHECK (status IN ('confirmed', 'cancelled')),
    created_at    TEXT    NOT NULL,            -- UTC, 'YYYY-MM-DD HH:MM:SS'
    CHECK (end_time > start_time)
);

CREATE INDEX IF NOT EXISTS idx_appt_start   ON appointments(start_time);
CREATE INDEX IF NOT EXISTS idx_appt_contact ON appointments(contact);
CREATE INDEX IF NOT EXISTS idx_appt_status  ON appointments(status, start_time);

-- Two confirmed appointments may never overlap in time.
CREATE TRIGGER IF NOT EXISTS trg_no_overlap_insert
BEFORE INSERT ON appointments
WHEN NEW.status = 'confirmed'
BEGIN
    SELECT RAISE(ABORT, 'OVERLAP')
    WHERE EXISTS (SELECT 1 FROM appointments
                  WHERE status = 'confirmed'
                    AND start_time < NEW.end_time
                    AND end_time   > NEW.start_time);
END;

CREATE TRIGGER IF NOT EXISTS trg_no_overlap_update
BEFORE UPDATE OF start_time, end_time, status ON appointments
WHEN NEW.status = 'confirmed'
BEGIN
    SELECT RAISE(ABORT, 'OVERLAP')
    WHERE EXISTS (SELECT 1 FROM appointments
                  WHERE id <> NEW.id
                    AND status = 'confirmed'
                    AND start_time < NEW.end_time
                    AND end_time   > NEW.start_time);
END;
"""


def get_connection() -> sqlite3.Connection:
    # isolation_level=None -> we control transactions explicitly (see transaction()).
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def connection():
    """Read-only style helper: open, yield, always close."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction():
    """
    Write helper. BEGIN IMMEDIATE takes SQLite's write lock straight away, so
    'check availability -> insert' happens as one indivisible step: nobody else
    can slip a booking in between. Any error rolls everything back.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables/indexes/triggers and insert the fictional services once."""
    with connection() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO services (name, description, duration_minutes, price) VALUES (?, ?, ?, ?)",
                config.SERVICES,
            )
