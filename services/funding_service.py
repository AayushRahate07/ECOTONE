import uuid
import datetime
import logging
from typing import Dict, Any

from shared.event_bus import bus
from shared.config import TOPICS
from shared.database import ServiceDatabase
from shared.outbox import OutboxWorker

logger = logging.getLogger("FUNDING_SERVICE")

funding_db = ServiceDatabase("funding_service")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY,
    grant_code TEXT UNIQUE,
    title TEXT,
    total_budget REAL,
    reserved_budget REAL DEFAULT 0,
    spent_budget REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS project_funding (
    allocation_id TEXT PRIMARY KEY,
    grant_id TEXT,
    project_id TEXT,
    reserved_amount REAL,
    status TEXT DEFAULT 'RESERVED',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS funding_ledger (
    ledger_id TEXT PRIMARY KEY,
    project_id TEXT,
    transaction_type TEXT,
    amount REAL,
    balance_after REAL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY,
    event_type TEXT,
    payload TEXT,
    status TEXT DEFAULT 'PENDING',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT,
    processed_at TEXT
);
"""

class FundingService:
    def __init__(self):
        funding_db.initialize(SCHEMA_SQL)
        self._seed_data()
        
        bus.subscribe(TOPICS["COMMAND_RESERVE_FUNDS"], self.handle_reserve_funds)
        bus.subscribe(TOPICS["COMMAND_RELEASE_FUNDS"], self.handle_release_funds)

    def _seed_data(self):
        with funding_db.begin() as tx:
            tx.execute(
                "INSERT OR IGNORE INTO grants (grant_id, grant_code, title, total_budget) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), "GRANT-WESTERN-GHATS-2026", "Western Ghats Eco-Restoration", 5000000.0)
            )
            tx.execute(
                "INSERT OR IGNORE INTO grants (grant_id, grant_code, title, total_budget) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), "GRANT-HERITAGE-ASI-2026", "ASI Heritage Sites Mapping", 3500000.0)
            )

    def handle_reserve_funds(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        event_type = event["event_type"]
        payload = event["payload"]
        project_id = payload["project_id"]
        amount = payload.get("amount", 0.0)
        grant_code = payload.get("grant_code", "GRANT-WESTERN-GHATS-2026")

        with funding_db.begin() as tx:
            if not tx.try_claim_event(event_id, event_type):
                logger.info(f"[FUNDING SERVICE] Duplicate event {event_id} ignored.")
                return

            cursor = tx.execute(
                "UPDATE grants SET reserved_budget = reserved_budget + ? WHERE grant_code = ? AND (total_budget - reserved_budget) >= ?",
                (amount, grant_code, amount)
            )
            if cursor.rowcount == 0:
                logger.info(f"[FUNDING SERVICE] Insufficient funds for project {project_id}.")
                tx.write_outbox(TOPICS['FUNDS_INSUFFICIENT'], {
                    "project_id": project_id,
                    "saga_id": payload.get("saga_id", project_id),
                    "reason": "Grant budget limit exceeded"
                })
                return

            grant_row = tx.fetchone("SELECT grant_id, reserved_budget FROM grants WHERE grant_code = ?", (grant_code,))
            grant_id = grant_row[0]
            new_reserved = grant_row[1]
            now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
            
            allocation_id = str(uuid.uuid4())
            tx.execute(
                "INSERT INTO project_funding (allocation_id, grant_id, project_id, reserved_amount, status, created_at) VALUES (?, ?, ?, ?, 'RESERVED', ?)",
                (allocation_id, grant_id, project_id, amount, now_str)
            )
            
            tx.execute(
                "INSERT INTO funding_ledger (ledger_id, project_id, transaction_type, amount, balance_after, created_at) VALUES (?, ?, 'RESERVE', ?, ?, ?)",
                (str(uuid.uuid4()), project_id, amount, new_reserved, now_str)
            )
            
            tx.write_outbox(TOPICS['FUNDS_RESERVED'], {
                "project_id": project_id,
                "saga_id": payload.get("saga_id", project_id),
                "grant_code": grant_code,
                "reserved_amount": amount
            })

    def handle_release_funds(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        event_type = event["event_type"]
        payload = event["payload"]
        project_id = payload["project_id"]

        with funding_db.begin() as tx:
            if not tx.try_claim_event(event_id, event_type):
                return
                
            alloc = tx.fetchone("SELECT allocation_id, grant_id, reserved_amount FROM project_funding WHERE project_id = ? AND status = 'RESERVED'", (project_id,))
            if not alloc:
                tx.write_outbox(TOPICS['FUNDS_RELEASED'], {
                    "project_id": project_id,
                    "saga_id": payload.get("saga_id", project_id),
                    "released_amount": 0,
                    "status": "RELEASED"
                })
                return
                
            allocation_id, grant_id, reserved_amount = alloc
            
            tx.execute("UPDATE grants SET reserved_budget = reserved_budget - ? WHERE grant_id = ?", (reserved_amount, grant_id))
            tx.execute("UPDATE project_funding SET status = 'RELEASED' WHERE allocation_id = ?", (allocation_id,))
            
            now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
            tx.execute(
                "INSERT INTO funding_ledger (ledger_id, project_id, transaction_type, amount, balance_after, created_at) VALUES (?, ?, 'RELEASE_COMPENSATION', ?, 0, ?)",
                (str(uuid.uuid4()), project_id, reserved_amount, now_str)
            )
            
            tx.write_outbox(TOPICS['FUNDS_RELEASED'], {
                "project_id": project_id,
                "saga_id": payload.get("saga_id", project_id),
                "released_amount": reserved_amount if alloc else 0,
                "status": "RELEASED"
            })

funding_service = FundingService()
funding_outbox = OutboxWorker(funding_db, "funding_service")
