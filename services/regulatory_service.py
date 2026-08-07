import uuid
import datetime
import logging
from typing import Dict, Any

from shared.event_bus import bus
from shared.config import TOPICS
from shared.database import ServiceDatabase
from shared.outbox import OutboxWorker

logger = logging.getLogger("REGULATORY_SERVICE")

regulatory_db = ServiceDatabase("regulatory_service")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS permits (
    permit_id TEXT PRIMARY KEY,
    project_id TEXT,
    permit_type TEXT,
    issuing_agency TEXT,
    status TEXT DEFAULT 'PENDING',
    created_at TEXT,
    decision_date TEXT,
    rejection_reason TEXT
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

class RegulatoryService:
    def __init__(self):
        regulatory_db.initialize(SCHEMA_SQL)
        self.mode = 'HAPPY_PATH'
        
        bus.subscribe(TOPICS["COMMAND_REQUEST_PERMIT"], self.handle_request_permit)

    def set_simulation_mode(self, mode: str):
        self.mode = mode

    def handle_request_permit(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        event_type = event["event_type"]
        payload = event["payload"]
        project_id = payload["project_id"]
        permit_type = payload.get("permit_type", "GENERAL")
        
        with regulatory_db.begin() as tx:
            if not tx.try_claim_event(event_id, event_type):
                return
                
            permit_id = str(uuid.uuid4())
            now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
            
            if self.mode == 'HAPPY_PATH':
                status = 'APPROVED'
                issuing_agency = 'Ministry of Environment, Forest & Climate Change'
                rejection_reason = None
                outbox_topic = TOPICS["PERMIT_APPROVED"]
            else:
                status = 'REJECTED'
                issuing_agency = 'Karnataka Forest Department'
                rejection_reason = 'Site intersects protected tiger corridor; drone flight permit denied.'
                outbox_topic = TOPICS["PERMIT_REJECTED"]
                payload["rejection_reason"] = rejection_reason
                
            tx.execute(
                "INSERT INTO permits (permit_id, project_id, permit_type, issuing_agency, status, created_at, decision_date, rejection_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (permit_id, project_id, permit_type, issuing_agency, status, now_str, now_str, rejection_reason)
            )
            
            payload["permit_id"] = permit_id
            payload["permit_status"] = status
            
            tx.write_outbox(outbox_topic, payload)

regulatory_gateway = RegulatoryService()
regulatory_outbox = OutboxWorker(regulatory_db, "regulatory_service")
