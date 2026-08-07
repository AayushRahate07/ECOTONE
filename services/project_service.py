import json
import logging
import uuid
import datetime
from typing import Optional, List, Dict, Any

from shared.database import ServiceDatabase
from shared.outbox import OutboxWorker
from shared.event_bus import bus
from shared.config import TOPICS

logger = logging.getLogger("AEGIS_PROJECT_SERVICE")

# Schema for the project service database
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    status TEXT DEFAULT 'DRAFT',
    location_name TEXT,
    site_lat REAL,
    site_lon REAL,
    priority TEXT DEFAULT 'MEDIUM',
    budget_requested REAL,
    required_equipment TEXT,
    required_team TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS saga_state (
    saga_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    current_step TEXT NOT NULL,
    status TEXT NOT NULL,
    comp_resources_done INTEGER DEFAULT 0,
    comp_funds_done INTEGER DEFAULT 0,
    comp_team_done INTEGER DEFAULT 0,
    data_json TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS saga_log (
    log_id TEXT PRIMARY KEY,
    saga_id TEXT,
    step TEXT,
    status TEXT,
    details TEXT,
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

class ProjectService:
    def __init__(self, db: ServiceDatabase):
        self.db = db
        
        # Subscribe to relevant events
        bus.subscribe(TOPICS["FUNDS_RESERVED"], self.handle_funds_reserved)
        bus.subscribe(TOPICS["FUNDS_INSUFFICIENT"], self.handle_funds_failed)
        bus.subscribe(TOPICS["RESOURCES_RESERVED"], self.handle_resources_reserved)
        bus.subscribe(TOPICS["RESOURCE_UNAVAILABLE"], self.handle_resources_failed)
        bus.subscribe(TOPICS["TEAM_ASSIGNED"], self.handle_team_assigned)
        bus.subscribe(TOPICS["PERMIT_APPROVED"], self.handle_permit_approved)
        bus.subscribe(TOPICS["PERMIT_REJECTED"], self.handle_permit_rejected)
        
        # Compensation acknowledgement topics
        bus.subscribe(TOPICS["RESOURCES_RELEASED"], self.handle_resources_released)
        bus.subscribe(TOPICS["FUNDS_RELEASED"], self.handle_funds_released)
        bus.subscribe(TOPICS["TEAM_UNASSIGNED"], self.handle_team_unassigned)

    def _now(self):
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def create_project(self, title: str, description: str, location_name: str, site_lat: float, site_lon: float,
                       budget_requested: float, required_equipment: List[str], required_team: List[str],
                       priority: str = 'MEDIUM') -> Dict[str, Any]:
        project_id = str(uuid.uuid4())
        saga_id = project_id  # Using project_id as saga_id for simplicity
        
        now = self._now()
        
        project_data = {
            "project_id": project_id,
            "title": title,
            "description": description,
            "status": "DRAFT",
            "location_name": location_name,
            "site_lat": site_lat,
            "site_lon": site_lon,
            "priority": priority,
            "budget_requested": budget_requested,
            "required_equipment": required_equipment,
            "required_team": required_team,
            "created_at": now,
            "updated_at": now
        }

        with self.db.begin() as tx:
            tx.execute(
                """
                INSERT INTO projects (project_id, title, description, status, location_name, site_lat, site_lon, priority, budget_requested, required_equipment, required_team, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, title, description, "DRAFT", location_name, site_lat, site_lon, priority, budget_requested, json.dumps(required_equipment), json.dumps(required_team), now, now)
            )
            
            tx.execute(
                """
                INSERT INTO saga_state (saga_id, project_id, current_step, status, data_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (saga_id, project_id, "STEP_1_FUNDING", "RUNNING", "{}", now)
            )
            
            tx.execute(
                """
                INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), saga_id, "STEP_1_FUNDING", "STARTED", "Project created, reserving funds", now)
            )
            
            payload = {
                "project_id": project_id,
                "saga_id": saga_id,
                "amount": budget_requested,
                "grant_code": "GRANT-WESTERN-GHATS-2026"
            }
            tx.write_outbox(TOPICS["COMMAND_RESERVE_FUNDS"], payload)

        logger.info(f"[SAGA ORCHESTRATOR] Project created {project_id}, started funding step")
        return project_data

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.db.begin() as tx:
            row = tx.fetchone("SELECT * FROM projects WHERE project_id = ?", (project_id,))
            if not row:
                return None
            return {
                "project_id": row[0],
                "title": row[1],
                "description": row[2],
                "status": row[3],
                "location_name": row[4],
                "site_lat": row[5],
                "site_lon": row[6],
                "priority": row[7],
                "budget_requested": row[8],
                "required_equipment": json.loads(row[9]),
                "required_team": json.loads(row[10]),
                "created_at": row[11],
                "updated_at": row[12]
            }

    def get_saga_log(self, project_id: str) -> List[Dict[str, Any]]:
        saga_id = project_id
        with self.db.begin() as tx:
            rows = tx.fetchall("SELECT log_id, saga_id, step, status, details, created_at FROM saga_log WHERE saga_id = ? ORDER BY created_at ASC", (saga_id,))
            return [
                {
                    "log_id": r[0],
                    "saga_id": r[1],
                    "step": r[2],
                    "status": r[3],
                    "details": r[4],
                    "created_at": r[5]
                }
                for r in rows
            ]

    def _check_compensation_complete(self, tx, saga_id: str, project_id: str):
        row = tx.fetchone("SELECT comp_resources_done, comp_funds_done, comp_team_done FROM saga_state WHERE saga_id = ?", (saga_id,))
        if not row:
            return
        
        comp_resources_done, comp_funds_done, comp_team_done = row
        
        if comp_resources_done and comp_funds_done and comp_team_done:
            now = self._now()
            tx.execute("UPDATE projects SET status = 'CANCELLED', updated_at = ? WHERE project_id = ?", (now, project_id))
            tx.execute("UPDATE saga_state SET status = 'COMPENSATED', updated_at = ? WHERE saga_id = ?", (now, saga_id))
            
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "COMPENSATION_COMPLETE", "COMPENSATED", "All compensations acknowledged", now)
            )
            
            tx.write_outbox(TOPICS["PROJECT_CANCELLED"], {"project_id": project_id, "saga_id": saga_id})
            logger.info(f"[SAGA ORCHESTRATOR] Saga {saga_id} fully compensated and cancelled")

    def recover_pending_sagas(self):
        logger.info(f"[SAGA ORCHESTRATOR] Recovering pending sagas")
        with self.db.begin() as tx:
            rows = tx.fetchall("SELECT saga_id, project_id, comp_resources_done, comp_funds_done, comp_team_done FROM saga_state WHERE status = 'COMPENSATING'")
            for row in rows:
                saga_id, project_id, res_done, funds_done, team_done = row
                
                if not res_done:
                    tx.write_outbox(TOPICS["COMMAND_RELEASE_RESOURCES"], {"project_id": project_id, "saga_id": saga_id})
                if not funds_done:
                    tx.write_outbox(TOPICS["COMMAND_RELEASE_FUNDS"], {"project_id": project_id, "saga_id": saga_id})
                if not team_done:
                    tx.write_outbox(TOPICS["COMMAND_UNASSIGN_TEAM"], {"project_id": project_id, "saga_id": saga_id})

    def handle_funds_reserved(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["FUNDS_RESERVED"]):
                return
            
            now = self._now()
            tx.execute("UPDATE saga_state SET current_step = 'STEP_2_RESOURCES', updated_at = ? WHERE saga_id = ?", (now, saga_id))
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "STEP_2_RESOURCES", "RUNNING", "Funds reserved, requesting resources", now)
            )
            
            project_row = tx.fetchone("SELECT required_equipment, site_lat, site_lon, priority FROM projects WHERE project_id = ?", (project_id,))
            required_equipment = json.loads(project_row[0]) if project_row else []
            site_lat = project_row[1] if project_row else 18.0
            site_lon = project_row[2] if project_row else 73.0
            priority = project_row[3] if project_row else "MEDIUM"
            priority_weights = {"CRITICAL": 1.5, "HIGH": 1.2, "MEDIUM": 1.0, "LOW": 0.8}
            
            out_payload = {
                "project_id": project_id,
                "saga_id": saga_id,
                "required_equipment": required_equipment,
                "lat": site_lat,
                "lon": site_lon,
                "priority_weight": priority_weights.get(priority, 1.0)
            }
            tx.write_outbox(TOPICS["COMMAND_RESERVE_RESOURCES"], out_payload)
            logger.info(f"[SAGA ORCHESTRATOR] Funds reserved for {project_id}, reserving resources")

    def handle_funds_failed(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["FUNDS_INSUFFICIENT"]):
                return
            
            now = self._now()
            tx.execute("UPDATE projects SET status = 'CANCELLED', updated_at = ? WHERE project_id = ?", (now, project_id))
            tx.execute("UPDATE saga_state SET status = 'FAILED', updated_at = ? WHERE saga_id = ?", (now, saga_id))
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "STEP_1_FUNDING", "FAILED", "Funds insufficient", now)
            )
            tx.write_outbox(TOPICS["PROJECT_CANCELLED"], {"project_id": project_id, "saga_id": saga_id})
            logger.warning(f"[SAGA ORCHESTRATOR] Funds failed for {project_id}, project cancelled")

    def handle_resources_reserved(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        allocated_resources = payload.get("allocated_resources", payload.get("reserved_resources", []))
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["RESOURCES_RESERVED"]):
                return
            
            now = self._now()
            saga_row = tx.fetchone("SELECT data_json FROM saga_state WHERE saga_id = ?", (saga_id,))
            saga_data = json.loads(saga_row[0]) if saga_row and saga_row[0] else {}
            saga_data["allocated_resources"] = allocated_resources
            
            tx.execute(
                "UPDATE saga_state SET current_step = 'STEP_3_TEAM', data_json = ?, updated_at = ? WHERE saga_id = ?",
                (json.dumps(saga_data), now, saga_id)
            )
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "STEP_3_TEAM", "RUNNING", "Resources reserved, requesting team", now)
            )
            
            project_row = tx.fetchone("SELECT required_team FROM projects WHERE project_id = ?", (project_id,))
            required_team = json.loads(project_row[0]) if project_row else []
            
            out_payload = {
                "project_id": project_id,
                "saga_id": saga_id,
                "required_team": required_team
            }
            tx.write_outbox(TOPICS["COMMAND_ASSIGN_TEAM"], out_payload)
            logger.info(f"[SAGA ORCHESTRATOR] Resources reserved for {project_id}, assigning team")

    def handle_resources_failed(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["RESOURCE_UNAVAILABLE"]):
                return
            
            now = self._now()
            tx.execute("UPDATE projects SET status = 'CANCELLED', updated_at = ? WHERE project_id = ?", (now, project_id))
            tx.execute("UPDATE saga_state SET status = 'FAILED', updated_at = ? WHERE saga_id = ?", (now, saga_id))
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "STEP_2_RESOURCES", "FAILED", "Resources unavailable", now)
            )
            tx.write_outbox(TOPICS["PROJECT_CANCELLED"], {"project_id": project_id, "saga_id": saga_id})
            logger.warning(f"[SAGA ORCHESTRATOR] Resources failed for {project_id}, project cancelled")

    def handle_team_assigned(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        assigned_team = payload.get("assigned_team", [])
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["TEAM_ASSIGNED"]):
                return
            
            now = self._now()
            saga_row = tx.fetchone("SELECT data_json FROM saga_state WHERE saga_id = ?", (saga_id,))
            saga_data = json.loads(saga_row[0]) if saga_row and saga_row[0] else {}
            saga_data["assigned_team"] = assigned_team
            
            tx.execute(
                "UPDATE saga_state SET current_step = 'STEP_4_PERMITS', data_json = ?, updated_at = ? WHERE saga_id = ?",
                (json.dumps(saga_data), now, saga_id)
            )
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "STEP_4_PERMITS", "RUNNING", "Team assigned, requesting permit", now)
            )
            
            out_payload = {
                "project_id": project_id,
                "saga_id": saga_id
            }
            tx.write_outbox(TOPICS["COMMAND_REQUEST_PERMIT"], out_payload)
            logger.info(f"[SAGA ORCHESTRATOR] Team assigned for {project_id}, requesting permit")

    def handle_permit_approved(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["PERMIT_APPROVED"]):
                return
            
            now = self._now()
            tx.execute("UPDATE projects SET status = 'ACTIVE', updated_at = ? WHERE project_id = ?", (now, project_id))
            tx.execute("UPDATE saga_state SET status = 'COMPLETED', updated_at = ? WHERE saga_id = ?", (now, saga_id))
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "STEP_4_PERMITS", "COMPLETED", "Permit approved, saga finished", now)
            )
            tx.write_outbox(TOPICS["PROJECT_ACTIVATED"], {"project_id": project_id, "saga_id": saga_id})
            logger.info(f"[SAGA ORCHESTRATOR] Permit approved for {project_id}, saga completed")

    def handle_permit_rejected(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["PERMIT_REJECTED"]):
                return
            
            now = self._now()
            tx.execute("UPDATE projects SET status = 'COMPENSATING', updated_at = ? WHERE project_id = ?", (now, project_id))
            tx.execute(
                "UPDATE saga_state SET status = 'COMPENSATING', comp_resources_done = 0, comp_funds_done = 0, comp_team_done = 0, updated_at = ? WHERE saga_id = ?",
                (now, saga_id)
            )
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "COMPENSATING", "STARTED", "Permit rejected, rolling back all", now)
            )
            
            out_payload = {"project_id": project_id, "saga_id": saga_id}
            tx.write_outbox(TOPICS["COMMAND_RELEASE_RESOURCES"], out_payload)
            tx.write_outbox(TOPICS["COMMAND_RELEASE_FUNDS"], out_payload)
            tx.write_outbox(TOPICS["COMMAND_UNASSIGN_TEAM"], out_payload)
            logger.warning(f"[SAGA ORCHESTRATOR] Permit rejected for {project_id}, started compensation")

    def handle_resources_released(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["RESOURCES_RELEASED"]):
                return
            tx.execute("UPDATE saga_state SET comp_resources_done = 1 WHERE saga_id = ?", (saga_id,))
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "COMP_RESOURCES", "DONE", "Resources released", self._now())
            )
            logger.info(f"[SAGA ORCHESTRATOR] Resources released for {project_id}")
            self._check_compensation_complete(tx, saga_id, project_id)

    def handle_funds_released(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["FUNDS_RELEASED"]):
                return
            tx.execute("UPDATE saga_state SET comp_funds_done = 1 WHERE saga_id = ?", (saga_id,))
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "COMP_FUNDS", "DONE", "Funds released", self._now())
            )
            logger.info(f"[SAGA ORCHESTRATOR] Funds released for {project_id}")
            self._check_compensation_complete(tx, saga_id, project_id)

    def handle_team_unassigned(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        saga_id = payload.get("saga_id", project_id)
        
        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["TEAM_UNASSIGNED"]):
                return
            tx.execute("UPDATE saga_state SET comp_team_done = 1 WHERE saga_id = ?", (saga_id,))
            tx.execute(
                "INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), saga_id, "COMP_TEAM", "DONE", "Team unassigned", self._now())
            )
            logger.info(f"[SAGA ORCHESTRATOR] Team unassigned for {project_id}")
            self._check_compensation_complete(tx, saga_id, project_id)


# Initialize the service globally
project_db = ServiceDatabase("project_service")
project_db.initialize(SCHEMA_SQL)
project_service = ProjectService(project_db)
project_outbox = OutboxWorker(project_db, "project_service")
