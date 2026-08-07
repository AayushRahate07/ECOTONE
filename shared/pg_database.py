"""
AEGIS-ECO PostgreSQL + PostGIS Database Layer
==============================================

Real PostgreSQL database manager supporting PostGIS spatial queries,
JSONB outbox insertion, and PostgreSQL ACID transactions.

Usage:
    from shared.pg_database import PostgresDatabase

    db = PostgresDatabase("funding_service", db_name="aegis_funding_db")
    with db.begin() as tx:
        if not tx.try_claim_event(event_id, event_type):
            return  # duplicate event
        
        tx.execute("UPDATE grants SET reserved_budget = reserved_budget + %s WHERE grant_code = %s", (amount, code))
        tx.write_outbox(event_type, payload)
"""

import json
import uuid
import logging
import datetime
from typing import Optional, List, Dict, Any, Tuple

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    psycopg2 = None
    HAS_PSYCOPG2 = False

from shared.config import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD
)

logger = logging.getLogger("AEGIS_PG_DB")


class PostgresTransaction:
    """Active transaction wrapper over a psycopg2 Connection/Cursor."""

    def __init__(self, conn):
        self._conn = conn
        self._cursor = conn.cursor()
        self._committed = False

    def execute(self, query: str, params: tuple = ()):
        """Execute a query within the active transaction."""
        self._cursor.execute(query, params)
        return self._cursor

    def fetchone(self, query: str, params: tuple = ()) -> Optional[tuple]:
        self._cursor.execute(query, params)
        return self._cursor.fetchone()

    def fetchall(self, query: str, params: tuple = ()) -> List[tuple]:
        self._cursor.execute(query, params)
        return self._cursor.fetchall()

    def try_claim_event(self, event_id: str, event_type: str) -> bool:
        """PostgreSQL Idempotency Claim:
        INSERT INTO processed_events (event_id, event_type, processed_at)
        VALUES (%s, %s, NOW()) ON CONFLICT (event_id) DO NOTHING;
        
        Returns True if row was inserted (claimed), False if conflict (duplicate).
        Must be called inside the active PostgreSQL transaction.
        """
        if not event_id:
            return True
            
        self._cursor.execute(
            """
            INSERT INTO processed_events (event_id, event_type, processed_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (event_id) DO NOTHING;
            """,
            (event_id, event_type)
        )
        return self._cursor.rowcount > 0

    def write_outbox(self, event_type: str, payload: dict) -> str:
        """Atomic PostgreSQL Outbox INSERT:
        Inserts a PENDING outbox entry into the outbox table inside
        the active PostgreSQL transaction.
        """
        payload_copy = dict(payload)
        event_id = str(uuid.uuid4())
        payload_copy["event_id"] = event_id
        
        self._cursor.execute(
            """
            INSERT INTO outbox (event_id, event_type, payload, status, created_at)
            VALUES (%s, %s, %s, 'PENDING', NOW());
            """,
            (event_id, event_type, json.dumps(payload_copy))
        )
        return event_id

    def commit(self):
        if not self._committed:
            self._conn.commit()
            self._committed = True

    def rollback(self):
        if not self._committed:
            try:
                self._conn.rollback()
            except Exception:
                pass


class PostgresDatabase:
    """Per-service PostgreSQL database manager."""

    def __init__(self, service_name: str, db_name: str = "postgres"):
        self.service_name = service_name
        self.db_name = db_name
        self._conn = None

    def is_available(self) -> bool:
        """Check if real PostgreSQL server is reachable."""
        if not HAS_PSYCOPG2:
            return False
        try:
            conn = psycopg2.connect(
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                dbname=self.db_name,
                connect_timeout=2
            )
            conn.close()
            return True
        except Exception:
            return False

    def _get_conn(self):
        if not HAS_PSYCOPG2:
            raise RuntimeError("psycopg2 is not installed.")
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                dbname=self.db_name
            )
            self._conn.autocommit = False
        return self._conn

    def begin(self):
        return PostgresTransactionContext(self)

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None


class PostgresTransactionContext:
    def __init__(self, db: PostgresDatabase):
        self.db = db
        self._tx = None

    def __enter__(self) -> PostgresTransaction:
        conn = self.db._get_conn()
        self._tx = PostgresTransaction(conn)
        return self._tx

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._tx.commit()
        else:
            self._tx.rollback()
        return False
