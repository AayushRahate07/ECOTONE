import math
import uuid
import datetime
import logging
from typing import Optional, List, Dict, Any

from shared.database import ServiceDatabase
from shared.outbox import OutboxWorker
from shared.event_bus import bus
from shared.config import TOPICS

logger = logging.getLogger("AEGIS_RESOURCE_SERVICE")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resources (
    resource_id TEXT PRIMARY KEY,
    name TEXT,
    resource_type TEXT,
    depot_name TEXT,
    lat REAL,
    lon REAL,
    battery_pct INTEGER DEFAULT 100,
    current_workload INTEGER DEFAULT 0,
    status TEXT DEFAULT 'AVAILABLE',
    hourly_cost REAL DEFAULT 150.0,
    reserved_by TEXT
);

CREATE TABLE IF NOT EXISTS resource_reservations (
    reservation_id TEXT PRIMARY KEY,
    resource_id TEXT,
    project_id TEXT,
    status TEXT DEFAULT 'RESERVED',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS spatial_constraints (
    constraint_id TEXT PRIMARY KEY,
    name TEXT,
    constraint_type TEXT,
    min_lat REAL,
    max_lat REAL,
    min_lon REAL,
    max_lon REAL
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

def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def calculate_composite_score(dist_km: float, travel_hours: float, battery_pct: int, workload: int, priority_weight: float = 1.0) -> float:
    score = ((100 - min(dist_km, 100)) * 0.35 +
             (10 - min(travel_hours, 10)) * 10 * 0.25 +
             battery_pct * 0.25 +
             (10 - workload) * 10 * 0.15)
    return score * priority_weight

class ResourceService:
    def __init__(self, db: ServiceDatabase):
        self.db = db
        bus.subscribe(TOPICS["COMMAND_RESERVE_RESOURCES"], self.handle_reserve_resources)
        bus.subscribe(TOPICS["COMMAND_RELEASE_RESOURCES"], self.handle_release_resources)

    def handle_reserve_resources(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]
        required_equipment = payload.get("required_equipment", [])
        proj_lat = payload.get("lat", 18.0)
        proj_lon = payload.get("lon", 73.0)
        priority_weight = payload.get("priority_weight", 1.0)

        reserved_resource_ids = []

        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["COMMAND_RESERVE_RESOURCES"]):
                return

            for eq_type in required_equipment:
                rows = tx.fetchall(
                    "SELECT resource_id, lat, lon, battery_pct, current_workload "
                    "FROM resources WHERE resource_type = ? AND status = 'AVAILABLE'",
                    (eq_type,)
                )
                
                candidates = []
                for row in rows:
                    r_id, r_lat, r_lon, bat_pct, workld = row
                    dist = haversine_distance_km(proj_lat, proj_lon, r_lat, r_lon)
                    travel_hours = dist / 50.0  # Assumed 50km/h travel speed
                    score = calculate_composite_score(dist, travel_hours, bat_pct, workld, priority_weight)
                    candidates.append((score, r_id))
                
                candidates.sort(reverse=True, key=lambda x: x[0])

                for score, best_resource_id in candidates:
                    cur = tx.execute(
                        "UPDATE resources SET status = 'RESERVED', reserved_by = ? "
                        "WHERE resource_id = ? AND status = 'AVAILABLE'",
                        (project_id, best_resource_id)
                    )
                    if cur.rowcount == 0:
                        continue
                    
                    tx.execute(
                        "INSERT INTO resource_reservations (reservation_id, resource_id, project_id, status, created_at) "
                        "VALUES (?, ?, ?, 'RESERVED', ?)",
                        (str(uuid.uuid4()), best_resource_id, project_id, datetime.datetime.now(datetime.timezone.utc).isoformat())
                    )
                    reserved_resource_ids.append(best_resource_id)
                    break

            if reserved_resource_ids:
                tx.write_outbox(TOPICS["RESOURCES_RESERVED"], {
                    "project_id": project_id,
                    "saga_id": payload.get("saga_id", project_id),
                    "reserved_resources": reserved_resource_ids,
                    "allocated_resources": reserved_resource_ids
                })
                logger.info(f"[RESOURCE SERVICE] Reserved {len(reserved_resource_ids)} resources for project {project_id}")
            else:
                tx.write_outbox(TOPICS["RESOURCE_UNAVAILABLE"], {
                    "project_id": project_id,
                    "saga_id": payload.get("saga_id", project_id),
                    "reason": "Could not reserve required resources"
                })
                logger.info(f"[RESOURCE SERVICE] Unavailable resources for project {project_id}")

    def handle_release_resources(self, event: Dict[str, Any]):
        event_id = event["event_id"]
        payload = event["payload"]
        project_id = payload["project_id"]

        with self.db.begin() as tx:
            if not tx.try_claim_event(event_id, TOPICS["COMMAND_RELEASE_RESOURCES"]):
                return

            rows = tx.fetchall(
                "SELECT resource_id FROM resource_reservations "
                "WHERE project_id = ? AND status = 'RESERVED'",
                (project_id,)
            )

            released_ids = []
            for (r_id,) in rows:
                tx.execute(
                    "UPDATE resources SET status = 'AVAILABLE', reserved_by = NULL "
                    "WHERE resource_id = ?",
                    (r_id,)
                )
                tx.execute(
                    "UPDATE resource_reservations SET status = 'RELEASED' "
                    "WHERE resource_id = ? AND project_id = ? AND status = 'RESERVED'",
                    (r_id, project_id)
                )
                released_ids.append(r_id)

            tx.write_outbox(TOPICS["RESOURCES_RELEASED"], {
                "project_id": project_id,
                "saga_id": payload.get("saga_id", project_id),
                "released_resources": released_ids
            })
            logger.info(f"[RESOURCE SERVICE] Released {len(released_ids)} resources for project {project_id}")


# Initialize module-level dependencies
resource_db = ServiceDatabase("resource_service")
resource_db.initialize(SCHEMA_SQL)

with resource_db.begin() as tx:
    resources_seed = [
        ("res-drone-001", "LiDAR Drone Alpha", "DRONE", "Pune Depot", 18.5204, 73.8567, 95, 2, 250.0),
        ("res-drone-002", "LiDAR Drone Beta", "DRONE", "Satara Depot", 17.6805, 74.0183, 88, 1, 250.0),
        ("res-seq-001", "eDNA Sequencer Unit-1", "SEQUENCER", "IISER Pune Lab", 18.5529, 73.8152, 100, 0, 400.0),
        ("res-radar-001", "Ground Radar GPR-7", "RADAR", "Kolhapur Station", 16.7050, 74.2433, 75, 4, 180.0),
        ("res-truck-001", "All-Terrain 4x4 Eco Truck", "VEHICLE", "Western Ghats Outpost", 17.9000, 73.7000, 90, 1, 120.0),
    ]
    for r in resources_seed:
        tx.execute(
            "INSERT OR IGNORE INTO resources (resource_id, name, resource_type, depot_name, lat, lon, battery_pct, current_workload, status, hourly_cost) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'AVAILABLE', ?)",
            r
        )

    constraints_seed = [
        ("constraint-1", "Koyna Wildlife Sanctuary Restricted Zone", "PROTECTED_FOREST", 17.40, 17.60, 73.65, 73.80),
        ("constraint-2", "Western Ghats Airspace No-Fly Polygon", "NO_FLY_ZONE", 18.10, 18.30, 73.40, 73.60),
    ]
    for c in constraints_seed:
        tx.execute(
            "INSERT OR IGNORE INTO spatial_constraints (constraint_id, name, constraint_type, min_lat, max_lat, min_lon, max_lon) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            c
        )

resource_service = ResourceService(resource_db)
resource_outbox = OutboxWorker(resource_db, "resource_service")
