"""
AEGIS-ECO Database Layer

SQLite-backed per-service databases with real ACID transaction support.
Each service gets its own isolated database file.
Designed so swapping to PostgreSQL requires only changing the connection constructor.

Transaction pattern:
    with db.begin() as tx:
        tx.try_claim_event(event_id, event_type)  # idempotency
        tx.execute("UPDATE ...", ...)               # domain mutation
        tx.write_outbox(event_type, payload)        # outbox INSERT
        # COMMIT happens at end of `with` block
        # ROLLBACK happens on exception
"""

import os
import sqlite3
import json
import uuid
import logging
import datetime
import threading

logger = logging.getLogger("AEGIS_DB")

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class Transaction:
    """Represents an active database transaction.
    
    All operations within a `with db.begin() as tx:` block share one
    SQLite transaction.  COMMIT fires at the end of the block;
    ROLLBACK fires on exception.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._committed = False

    # ---- raw SQL --------------------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        if "%s" in sql:
            sql = sql.replace("%s", "?")
        return self._conn.execute(sql, params)

    def fetchone(self, sql: str, params: tuple = ()):
        if "%s" in sql:
            sql = sql.replace("%s", "?")
        return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()):
        if "%s" in sql:
            sql = sql.replace("%s", "?")
        return self._conn.execute(sql, params).fetchall()

    # ---- idempotency (inside THIS transaction) --------------------------

    def try_claim_event(self, event_id: str, event_type: str) -> bool:
        """Attempt to claim an event for processing.
        
        Uses INSERT OR IGNORE + changes() check.
        Returns True if this is a NEW event (we claimed it).
        Returns False if it was already processed (duplicate).
        
        MUST be called inside the active transaction — the row is only
        visible after the outer COMMIT, preventing other workers from
        processing the same event concurrently.
        """
        if not event_id:
            return True
        self._conn.execute(
            "INSERT OR IGNORE INTO processed_events (event_id, event_type, processed_at) "
            "VALUES (?, ?, ?)",
            (event_id, event_type, datetime.datetime.now(datetime.timezone.utc).isoformat())
        )
        # SQLite: changes() returns 0 if INSERT was ignored (duplicate)
        claimed = self._conn.execute("SELECT changes()").fetchone()[0] > 0
        return claimed

    # ---- outbox (inside THIS transaction) --------------------------------

    def write_outbox(self, event_type: str, payload: dict) -> str:
        """Insert an outbox entry inside the current transaction.
        
        Does NOT publish to the event bus.  The OutboxWorker is
        responsible for polling PENDING entries and publishing them.
        """
        payload_copy = dict(payload)
        # Always generate a unique event_id for each outbox entry unless explicitly overridden
        event_id = str(uuid.uuid4())
        payload_copy["event_id"] = event_id
        self._conn.execute(
            "INSERT INTO outbox (event_id, event_type, payload, status, created_at) "
            "VALUES (?, ?, ?, 'PENDING', ?)",
            (event_id, event_type, json.dumps(payload_copy),
             datetime.datetime.now(datetime.timezone.utc).isoformat())
        )
        return event_id

    # ---- lifecycle -------------------------------------------------------

    def commit(self):
        self._conn.execute("COMMIT")
        self._committed = True

    def rollback(self):
        if not self._committed:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass


class ServiceDatabase:
    """One database per microservice. Automatically connects to PostgreSQL if available,
    otherwise falls back to an isolated SQLite database file.
    """

    def __init__(self, service_name: str, db_name: str = None):
        self.service_name = service_name
        self.db_name = db_name or f"aegis_{service_name}_db"
        
        # Check PostgreSQL availability
        from shared.pg_database import PostgresDatabase, HAS_PSYCOPG2
        self._pg = PostgresDatabase(service_name, db_name=self.db_name)
        if HAS_PSYCOPG2 and self._pg.is_available():
            self._use_pg = True
            logger.info(f"[DB] Using Real PostgreSQL server for '{self.service_name}' ({self.db_name})")
        else:
            self._use_pg = False
            os.makedirs(DB_DIR, exist_ok=True)
            self._db_path = os.path.join(DB_DIR, f"{service_name}.db")
            self._lock = threading.Lock()
            self._conn = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._use_pg:
            return self._pg._get_conn()
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.isolation_level = None
        return self._conn

    def initialize(self, schema_sql: str):
        if self._use_pg:
            return  # PostgreSQL schema is initialized via setup_postgres_db.py
        conn = self._get_conn()
        conn.executescript(schema_sql)
        logger.info(f"[DB] Initialized SQLite database for '{self.service_name}' at {self._db_path}")

    def begin(self):
        if self._use_pg:
            return self._pg.begin()
        return TransactionContext(self)

    def close(self):
        if self._use_pg:
            self._pg.close()
        elif self._conn:
            self._conn.close()
            self._conn = None

    def destroy(self):
        """Delete the database file. Used in tests."""
        self.close()
        if os.path.exists(self._db_path):
            os.remove(self._db_path)


class TransactionContext:
    """Context manager that wraps a Transaction with BEGIN/COMMIT/ROLLBACK."""

    def __init__(self, db: ServiceDatabase):
        self._db = db
        self._tx = None

    def __enter__(self) -> Transaction:
        self._db._lock.acquire()
        conn = self._db._get_conn()
        conn.execute("BEGIN IMMEDIATE")
        self._tx = Transaction(conn)
        return self._tx

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None and not self._tx._committed:
                self._tx.commit()
            elif exc_type is not None:
                self._tx.rollback()
        finally:
            self._db._lock.release()
        return False  # propagate exceptions
