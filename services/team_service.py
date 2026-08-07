import uuid
import datetime
import logging
from typing import Dict, Any
import json

from shared.event_bus import bus
from shared.config import TOPICS
from shared.database import ServiceDatabase
from shared.outbox import OutboxWorker

logger = logging.getLogger("TEAM_SERVICE")

team_db = ServiceDatabase("team_service")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS team_members (
    member_id TEXT PRIMARY KEY,
    name TEXT,
    role TEXT,
    specialty TEXT,
    location_base TEXT,
    availability_status TEXT DEFAULT 'AVAILABLE'
);
CREATE TABLE IF NOT EXISTS team_assignments (
    assignment_id TEXT PRIMARY KEY,
    member_id TEXT,
    project_id TEXT,
    role_assigned TEXT,
    status TEXT DEFAULT 'ASSIGNED',
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

class TeamService:
    def __init__(self):
        team_db.initialize(SCHEMA_SQL)
        self._seed_data()
        
        bus.subscribe(TOPICS["COMMAND_ASSIGN_TEAM"], self.handle_assign_team)
        bus.subscribe(TOPICS["COMMAND_UNASSIGN_TEAM"], self.handle_unassign_team)

    def _seed_data(self):
        members = [
            ("Dr. Aris Thorne", "Lead Ecologist", "eDNA Sequencing", "Pune"),
            ("Elena Rostova", "Drone Operator", "LiDAR Topography", "Satara"),
            ("Siddharth Mehta", "Botanist", "Flora Taxonomy", "Kolhapur"),
            ("Maya Lin", "Archaeologist", "GPR Excavation", "Mumbai")
        ]
        with team_db.begin() as tx:
            for name, role, specialty, location_base in members:
                existing = tx.fetchone("SELECT member_id FROM team_members WHERE name = ?", (name,))
                if not existing:
                    tx.execute(
                        "INSERT INTO team_members (member_id, name, role, specialty, location_base, availability_status) VALUES (?, ?, ?, ?, ?, 'AVAILABLE')",
                        (str(uuid.uuid4()), name, role, specialty, location_base)
                    )

    def handle_assign_team(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        event_type = event["event_type"]
        payload = event["payload"]
        project_id = payload["project_id"]
        
        with team_db.begin() as tx:
            if not tx.try_claim_event(event_id, event_type):
                return
                
            members = tx.fetchall("SELECT member_id, name, role FROM team_members WHERE availability_status = 'AVAILABLE' LIMIT 2")
            
            assigned_team = []
            now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
            
            for m_id, name, role in members:
                tx.execute("UPDATE team_members SET availability_status = 'ASSIGNED' WHERE member_id = ?", (m_id,))
                tx.execute(
                    "INSERT INTO team_assignments (assignment_id, member_id, project_id, role_assigned, status, created_at) VALUES (?, ?, ?, ?, 'ASSIGNED', ?)",
                    (str(uuid.uuid4()), m_id, project_id, role, now_str)
                )
                assigned_team.append({"member_id": m_id, "name": name, "role": role})
                
            out_payload = {
                "project_id": project_id,
                "saga_id": payload.get("saga_id", project_id),
                "assigned_team": assigned_team
            }
            tx.write_outbox(TOPICS["TEAM_ASSIGNED"], out_payload)

    def handle_unassign_team(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        event_type = event["event_type"]
        payload = event["payload"]
        project_id = payload["project_id"]
        
        with team_db.begin() as tx:
            if not tx.try_claim_event(event_id, event_type):
                return
                
            assignments = tx.fetchall("SELECT member_id, assignment_id FROM team_assignments WHERE project_id = ? AND status = 'ASSIGNED'", (project_id,))
            
            for m_id, a_id in assignments:
                tx.execute("UPDATE team_members SET availability_status = 'AVAILABLE' WHERE member_id = ?", (m_id,))
                tx.execute("UPDATE team_assignments SET status = 'UNASSIGNED' WHERE assignment_id = ?", (a_id,))
                
            out_payload = {
                "project_id": project_id,
                "saga_id": payload.get("saga_id", project_id),
                "status": "UNASSIGNED"
            }
            tx.write_outbox(TOPICS["TEAM_UNASSIGNED"], out_payload)

team_service = TeamService()
team_outbox = OutboxWorker(team_db, "team_service")
