import os
import sys
import json
import logging
import datetime
import math
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.project_service import project_service, project_db
from services.resource_service import resource_service, resource_db, haversine_distance_km, calculate_composite_score
from services.funding_service import funding_service, funding_db
from services.regulatory_service import regulatory_gateway, regulatory_db
from services.team_service import team_service, team_db
from services.analytics_service import analytics_service
from shared.config import TOPICS
from shared.pg_database import PostgresDatabase, HAS_PSYCOPG2
from shared.kafka_bus import KafkaEventBus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AEGIS_SERVER")


class AegisRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(os.path.dirname(__file__), "web"), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # CORS headers helper
        def send_json(data, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        if path == "/api/analytics":
            summary = analytics_service.get_dashboard_summary()
            send_json(summary)
            return

        if path == "/api/projects":
            with project_db.begin() as tx:
                rows = tx.fetchall("SELECT project_id, title, description, status, location_name, site_lat, site_lon, priority, budget_requested, required_equipment, required_team, created_at FROM projects ORDER BY created_at DESC;")
            projects = []
            for r in rows:
                created = datetime.datetime.fromisoformat(r[11].replace('Z', '+00:00')) if r[11] else datetime.datetime.now(datetime.timezone.utc)
                days_waiting = (datetime.datetime.now(datetime.timezone.utc) - created).days
                
                with resource_db.begin() as tx:
                    res_rows = tx.fetchall("SELECT lat, lon FROM resources WHERE status = 'AVAILABLE'")
                min_dist = None
                for rlat, rlon in res_rows:
                    d = haversine_distance_km(r[5], r[6], rlat, rlon)
                    if min_dist is None or d < min_dist:
                        min_dist = d
                
                with resource_db.begin() as tx:
                    constraints = tx.fetchall("SELECT name, constraint_type FROM spatial_constraints WHERE min_lat <= ? AND max_lat >= ? AND min_lon <= ? AND max_lon >= ?", (r[5], r[5], r[6], r[6]))
                risk_flags = [c[0] for c in constraints]
                
                with resource_db.begin() as tx:
                    reserved = tx.fetchall("SELECT r.hourly_cost FROM resources r WHERE r.reserved_by = ?", (r[0],))
                total_asset_cost = sum(c[0] * 8 for c in reserved)
                budget_remaining = (r[8] or 0) - total_asset_cost

                projects.append({
                    "project_id": r[0], "title": r[1], "description": r[2], "status": r[3],
                    "location_name": r[4], "site_lat": r[5], "site_lon": r[6], "priority": r[7],
                    "budget_requested": r[8],
                    "required_equipment": json.loads(r[9]) if isinstance(r[9], str) else r[9],
                    "required_team": json.loads(r[10]) if isinstance(r[10], str) else r[10],
                    "created_at": r[11],
                    "days_waiting": days_waiting,
                    "nearest_asset_km": round(min_dist, 1) if min_dist is not None else None,
                    "risk_flags": risk_flags,
                    "budget_remaining": budget_remaining
                })
            send_json(projects)
            return

        if path == "/api/map_context":
            with resource_db.begin() as tx:
                res_rows = tx.fetchall("SELECT depot_name, lat, lon, resource_type FROM resources;")
                constraints_rows = tx.fetchall("SELECT name, constraint_type, min_lat, max_lat, min_lon, max_lon FROM spatial_constraints;")
            
            depot_map = {}
            for d_name, lat, lon, r_type in res_rows:
                if not d_name: continue
                if d_name not in depot_map:
                    depot_map[d_name] = {"name": d_name, "lat": lat, "lon": lon, "resource_count": 0, "types": set()}
                depot_map[d_name]["resource_count"] += 1
                depot_map[d_name]["types"].add(r_type)
            
            depots = []
            for d in depot_map.values():
                d["types"] = list(d["types"])
                depots.append(d)
                
            constraint_zones = []
            for c in constraints_rows:
                constraint_zones.append({
                    "name": c[0],
                    "type": c[1],
                    "bounds": [[c[2], c[4]], [c[3], c[5]]]
                })
            
            send_json({
                "depots": depots,
                "constraint_zones": constraint_zones
            })
            return

        if path.startswith("/api/projects/") and path != "/api/projects/analyze":
            pid = path.replace("/api/projects/", "")
            proj = project_service.get_project(pid)
            if not proj:
                send_json({"error": "Project not found"}, 404)
                return
            saga_log = project_service.get_saga_log(pid)
            proj["saga_log"] = saga_log

            # Derive per-step statuses from saga_log using forward-inference
            # The saga steps progress: FUNDING -> RESOURCES -> TEAM -> PERMITS
            # If a later step appears, all prior steps are implicitly completed.
            step_statuses = {
                "funding_status": "PENDING",
                "resource_status": "PENDING",
                "team_status": "PENDING",
                "permit_status": "PENDING",
            }
            
            # Track highest completed step index
            step_order = {"STEP_1": 0, "STEP_2": 1, "STEP_3": 2, "STEP_4": 3}
            highest_step = -1
            has_compensation = False
            
            for entry in saga_log:
                step = entry.get("step", "")
                status = entry.get("status", "")
                
                # Track compensation entries
                if "COMP" in step:
                    has_compensation = True
                    if "FUND" in step:
                        step_statuses["funding_status"] = "RELEASED"
                    elif "RESOURCE" in step:
                        step_statuses["resource_status"] = "RELEASED"
                    elif "TEAM" in step:
                        step_statuses["team_status"] = "UNASSIGNED"
                    continue
                
                if "COMPENSATING" in step:
                    has_compensation = True
                    step_statuses["permit_status"] = "REJECTED"
                    continue
                
                # Map step to index
                for prefix, idx in step_order.items():
                    if step.startswith(prefix):
                        if idx > highest_step:
                            highest_step = idx
                        break
                
                # Handle permit final status
                if "PERMIT" in step or "STEP_4" in step:
                    if status == "COMPLETED":
                        step_statuses["permit_status"] = "APPROVED"
                    elif "REJECT" in status or "FAILED" in status:
                        step_statuses["permit_status"] = "REJECTED"
                    elif status in ("RUNNING", "STARTED"):
                        step_statuses["permit_status"] = "IN_PROGRESS"
            
            # If no compensation, use forward-inference
            if not has_compensation:
                completed_names = ["funding_status", "resource_status", "team_status", "permit_status"]
                completed_values = ["RESERVED", "ALLOCATED", "ASSIGNED", None]  # permit handled separately
                in_progress_at = highest_step
                
                for i in range(min(highest_step, 3)):
                    if completed_values[i]:
                        step_statuses[completed_names[i]] = completed_values[i]
                
                # The current step is IN_PROGRESS unless it's completed
                if highest_step >= 0 and highest_step <= 3:
                    current_key = completed_names[highest_step]
                    if step_statuses[current_key] not in ("APPROVED", "REJECTED"):
                        if step_statuses[current_key] == "PENDING":
                            step_statuses[current_key] = "IN_PROGRESS"

            # Get saga_state row
            with project_db.begin() as tx:
                saga_row = tx.fetchone(
                    "SELECT saga_id, current_step, status, comp_resources_done, comp_funds_done, comp_team_done FROM saga_state WHERE project_id = ?",
                    (pid,)
                )
            if saga_row:
                proj["saga_state"] = {
                    "saga_id": saga_row[0],
                    "current_step": saga_row[1],
                    "status": saga_row[2],
                    "comp_resources_done": bool(saga_row[3]),
                    "comp_funds_done": bool(saga_row[4]),
                    "comp_team_done": bool(saga_row[5]),
                }
            else:
                proj["saga_state"] = {"saga_id": pid, "current_step": "UNKNOWN", "status": proj.get("status", "DRAFT")}

            proj.update(step_statuses)
            send_json(proj)
            return

        if path == "/api/resources":
            with resource_db.begin() as tx:
                r_rows = tx.fetchall("SELECT resource_id, name, resource_type, depot_name, lat, lon, battery_pct, current_workload, status, hourly_cost, reserved_by FROM resources;")
            resources = []
            for r in r_rows:
                resources.append({
                    "resource_id": r[0], "name": r[1], "resource_type": r[2], "depot_name": r[3],
                    "lat": r[4], "lon": r[5], "battery_pct": r[6], "current_workload": r[7],
                    "status": r[8], "hourly_cost": r[9], "reserved_by": r[10]
                })

            with team_db.begin() as tx:
                t_rows = tx.fetchall("SELECT member_id, name, role, specialty, location_base, availability_status FROM team_members;")
            team_members = []
            for t in t_rows:
                team_members.append({
                    "member_id": t[0], "name": t[1], "role": t[2], "specialty": t[3],
                    "location_base": t[4], "availability_status": t[5]
                })

            send_json({"resources": resources, "team_members": team_members})
            return

        if path == "/api/system/health":
            pg_ok = PostgresDatabase("project_service").is_available()
            kafka_ok = KafkaEventBus().is_available()
            
            with project_db.begin() as tx:
                pending_outbox = tx.fetchone("SELECT COUNT(*) FROM outbox WHERE status = 'PENDING';")[0]
                failed_events = 0
                active_sagas = tx.fetchone("SELECT COUNT(*) FROM saga_state WHERE status IN ('RUNNING', 'COMPENSATING');")[0]

            send_json({
                "health": {
                    "postgresql": "Healthy" if pg_ok else "Simulated WAL Engine",
                    "kafka": "Healthy" if kafka_ok else "Simulated Bus",
                    "redis": "Healthy",
                    "postgis": "Healthy" if pg_ok else "Simulated Spatial Engine"
                },
                "metrics": {
                    "pending_outbox": pending_outbox,
                    "failed_events": failed_events,
                    "active_sagas": active_sagas
                }
            })
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            data = {}

        def send_json(res_data, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(res_data).encode("utf-8"))

        if parsed.path == "/api/projects/analyze":
            lat = float(data.get("site_lat", 17.6805))
            lon = float(data.get("site_lon", 74.0183))
            req_eq = data.get("required_equipment", ["DRONE"])
            budget = float(data.get("budget_requested", 350000.0))
            
            with resource_db.begin() as tx:
                rows = tx.fetchall("SELECT resource_id, name, resource_type, depot_name, lat, lon, battery_pct, current_workload, hourly_cost FROM resources WHERE status = 'AVAILABLE';")
            
            candidates = []
            total_cost = 0.0
            for r_id, r_name, r_type, depot, r_lat, r_lon, battery, workload, cost in rows:
                if r_type not in req_eq:
                    continue
                dist = haversine_distance_km(lat, lon, r_lat, r_lon)
                travel_hours = dist / 50.0
                score = calculate_composite_score(dist, travel_hours, battery, workload, 1.2)
                candidates.append({
                    "resource_id": r_id, "name": r_name, "resource_type": r_type, "depot_name": depot,
                    "dist_km": round(dist, 1), "battery_pct": battery,
                    "workload": workload, "score": round(score, 2), "hourly_cost": cost
                })
                total_cost += cost
            
            candidates.sort(key=lambda x: x["score"], reverse=True)

            # Check spatial constraints
            with resource_db.begin() as tx:
                constraints = tx.fetchall("SELECT name, constraint_type, min_lat, max_lat, min_lon, max_lon FROM spatial_constraints;")
            
            violated = []
            for c_name, c_type, min_lat, max_lat, min_lon, max_lon in constraints:
                if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                    violated.append({"name": c_name, "type": c_type})

            send_json({
                "status": "SUCCESS",
                "recommended_resources": candidates[:len(req_eq)],
                "all_candidates": candidates,
                "spatial_restrictions_clear": len(violated) == 0,
                "spatial_violations": violated,
                "equipment_available": len(candidates) >= len(req_eq),
                "estimated_cost": total_cost,
                "within_budget": total_cost <= budget
            })
            return

        if parsed.path == "/api/projects/initiate":
            mode = data.get("sim_mode", "HAPPY_PATH")
            regulatory_gateway.set_simulation_mode(mode)

            project = project_service.create_project(
                title=data.get("title", "Western Ghats Survey"),
                description=data.get("description", "eDNA biodiversity survey"),
                location_name=data.get("location_name", "Satara Corridor"),
                site_lat=float(data.get("site_lat", 17.6805)),
                site_lon=float(data.get("site_lon", 74.0183)),
                budget_requested=float(data.get("budget_requested", 350000.0)),
                required_equipment=data.get("required_equipment", ["DRONE", "SEQUENCER"]),
                required_team=data.get("required_team", ["Lead Ecologist", "Drone Operator"]),
                priority=data.get("priority", "HIGH")
            )

            # Auto-run outbox loop so saga executes
            from scripts.simulate_saga import drain_outboxes
            drain_outboxes()

            full_proj = project_service.get_project(project["project_id"])
            send_json({"status": "SUCCESS", "project": full_proj})
            return

        send_json({"error": "Not Found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        def send_json(data, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        if path.startswith("/api/projects/"):
            pid = path.replace("/api/projects/", "")
            
            try:
                # 1. project_db
                with project_db.begin() as tx:
                    tx.execute("DELETE FROM projects WHERE project_id = ?;", (pid,))
                    tx.execute("DELETE FROM saga_state WHERE project_id = ?;", (pid,))
                    tx.execute("DELETE FROM saga_log WHERE saga_id = ?;", (pid,))

                # 2. funding_db
                with funding_db.begin() as tx:
                    allocations = tx.fetchall("SELECT grant_id, reserved_amount FROM project_funding WHERE project_id = ? AND status = 'RESERVED';", (pid,))
                    for grant_id, amount in allocations:
                        tx.execute("UPDATE grants SET reserved_budget = max(0, reserved_budget - ?) WHERE grant_id = ?;", (amount, grant_id))
                    tx.execute("DELETE FROM project_funding WHERE project_id = ?;", (pid,))
                    tx.execute("DELETE FROM funding_ledger WHERE project_id = ?;", (pid,))

                # 3. resource_db
                with resource_db.begin() as tx:
                    tx.execute("UPDATE resources SET status = 'AVAILABLE', reserved_by = NULL WHERE reserved_by = ?;", (pid,))
                    tx.execute("DELETE FROM resource_reservations WHERE project_id = ?;", (pid,))

                # 4. team_db
                with team_db.begin() as tx:
                    tx.execute("UPDATE team_members SET availability_status = 'AVAILABLE' WHERE member_id IN (SELECT member_id FROM team_assignments WHERE project_id = ?);", (pid,))
                    tx.execute("DELETE FROM team_assignments WHERE project_id = ?;", (pid,))

                # 5. regulatory_db
                with regulatory_db.begin() as tx:
                    tx.execute("DELETE FROM permits WHERE project_id = ?;", (pid,))

                send_json({"status": "SUCCESS", "message": f"Project {pid} deleted and resources released."})
            except Exception as e:
                logger.error(f"Error deleting project {pid}: {e}", exc_info=True)
                send_json({"error": str(e)}, 500)
            return

        send_json({"error": "Not Found"}, 404)


from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

def run_server(port=8080):
    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, AegisRequestHandler)
    logger.info(f"[ECOTONE SERVER] Running clean multi-page app at http://localhost:{port}")
    httpd.serve_forever()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run_server(port)
