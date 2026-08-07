import sys
import os
import uuid
import datetime
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.project_service import project_db
from services.resource_service import resource_db
from services.funding_service import funding_db
from services.team_service import team_db
from services.regulatory_service import regulatory_db
from scripts.simulate_saga import reset_db_state

def create_uuid():
    return str(uuid.uuid4())

def seed():
    # 1. Clean existing project data first
    with project_db.begin() as tx:
        tx.execute("DELETE FROM saga_log")
        tx.execute("DELETE FROM saga_state")
        tx.execute("DELETE FROM projects")
        tx.execute("DELETE FROM outbox")
        tx.execute("DELETE FROM processed_events")

    with regulatory_db.begin() as tx:
        tx.execute("DELETE FROM permits")

    # 2. Reset resource/team/funding state
    reset_db_state()
    print("[SEED] Database state reset.")

    now = datetime.datetime.now(datetime.timezone.utc)

    # Need a grant ID for funding allocations
    with funding_db.begin() as tx:
        grants = tx.fetchall("SELECT grant_id FROM grants LIMIT 1")
        if not grants:
            grant_id = create_uuid()
            tx.execute("INSERT INTO grants (grant_id, grant_code, title, total_budget) VALUES (?, ?, ?, ?)",
                       (grant_id, 'G-SEED', 'Seed Grant', 10000000.0))
        else:
            grant_id = grants[0][0]

    projects_data = [
        # 1. Sahyadri Ridge LiDAR Mapping
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "title": "Sahyadri Ridge LiDAR Mapping",
            "status": "ACTIVE", "priority": "HIGH",
            "location": "Satara Corridor", "lat": 17.6805, "lon": 74.0183,
            "budget": 450000.0, "equip": ["DRONE", "VEHICLE"],
            "saga_status": "COMPLETED", "current_step": "STEP_5_ACTIVE",
            "comp": (0, 0, 0), "permit_status": "APPROVED",
            "days_ago": 12, "reached_step": 4
        },
        # 2. Kas Plateau Endemic Flora Census
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "title": "Kas Plateau Endemic Flora Census",
            "status": "ACTIVE", "priority": "CRITICAL",
            "location": "Kas Pathar UNESCO Site", "lat": 17.7167, "lon": 73.8000,
            "budget": 280000.0, "equip": ["SEQUENCER"],
            "saga_status": "COMPLETED", "current_step": "STEP_5_ACTIVE",
            "comp": (0, 0, 0), "permit_status": "APPROVED",
            "days_ago": 8, "reached_step": 4
        },
        # 3. Bhimashankar Corridor eDNA Survey
        {
            "id": "33333333-3333-3333-3333-333333333333",
            "title": "Bhimashankar Corridor eDNA Survey",
            "status": "DRAFT", "priority": "HIGH",
            "location": "Bhimashankar Wildlife Sanctuary", "lat": 19.0719, "lon": 73.5358,
            "budget": 520000.0, "equip": ["DRONE", "SEQUENCER", "RADAR"],
            "saga_status": "RUNNING", "current_step": "STEP_2_RESOURCES",
            "comp": (0, 0, 0), "permit_status": None,
            "days_ago": 3, "reached_step": 1
        },
        # 4. Raigad Fort Heritage GPR Scan
        {
            "id": "44444444-4444-4444-4444-444444444444",
            "title": "Raigad Fort Heritage GPR Scan",
            "status": "COMPENSATING", "priority": "MEDIUM",
            "location": "Raigad Fort Complex", "lat": 18.2348, "lon": 73.4473,
            "budget": 380000.0, "equip": ["RADAR", "VEHICLE"],
            "saga_status": "COMPENSATING", "current_step": "STEP_4_PERMITS",
            "comp": (1, 0, 1), "permit_status": "REJECTED",
            "days_ago": 6, "reached_step": 4
        },
        # 5. Tamhini Ghat Amphibian Monitoring
        {
            "id": "55555555-5555-5555-5555-555555555555",
            "title": "Tamhini Ghat Amphibian Monitoring",
            "status": "CANCELLED", "priority": "LOW",
            "location": "Tamhini Ghat", "lat": 18.4530, "lon": 73.4250,
            "budget": 180000.0, "equip": ["SEQUENCER", "VEHICLE"],
            "saga_status": "COMPENSATED", "current_step": "STEP_4_PERMITS",
            "comp": (1, 1, 1), "permit_status": "REJECTED",
            "days_ago": 15, "reached_step": 4
        }
    ]

    for p in projects_data:
        pid = p["id"]
        created = (now - datetime.timedelta(days=p["days_ago"])).isoformat()
        saga_id = create_uuid()

        with project_db.begin() as tx:
            tx.execute('''
                INSERT INTO projects (project_id, title, description, status, location_name, site_lat, site_lon, priority, budget_requested, required_equipment, required_team, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (pid, p["title"], "Seed project", p["status"], p["location"], p["lat"], p["lon"], p["priority"], p["budget"], json.dumps(p["equip"]), json.dumps(["Ecologist"]), created, created))
            
            tx.execute('''
                INSERT INTO saga_state (saga_id, project_id, current_step, status, comp_resources_done, comp_funds_done, comp_team_done, data_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (saga_id, pid, p["current_step"], p["saga_status"], p["comp"][0], p["comp"][1], p["comp"][2], "{}", created))
            
            tx.execute('INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                (create_uuid(), saga_id, 'INIT', 'SUCCESS', 'Saga started', created))
            tx.execute('INSERT INTO saga_log (log_id, saga_id, step, status, details, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                (create_uuid(), saga_id, 'FUNDS', 'SUCCESS', 'Funds allocated', created))

        # Funding
        if p["reached_step"] >= 1:
            fund_status = 'RELEASED' if p["comp"][1] == 1 else 'RESERVED'
            with funding_db.begin() as tx:
                tx.execute('''
                    INSERT INTO project_funding (allocation_id, grant_id, project_id, reserved_amount, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (create_uuid(), grant_id, pid, p["budget"], fund_status, created))

        # Resources
        if p["reached_step"] >= 2:
            res_status = 'RELEASED' if p["comp"][0] == 1 else 'RESERVED'
            with resource_db.begin() as tx:
                for eq in p["equip"]:
                    rows = tx.fetchall("SELECT resource_id FROM resources WHERE resource_type = ? AND status = 'AVAILABLE' LIMIT 1", (eq,))
                    if rows:
                        r_id = rows[0][0]
                        tx.execute('INSERT INTO resource_reservations (reservation_id, resource_id, project_id, status, created_at) VALUES (?, ?, ?, ?, ?)',
                            (create_uuid(), r_id, pid, res_status, created))
                        if res_status == 'RESERVED':
                            tx.execute("UPDATE resources SET status = 'RESERVED', reserved_by = ? WHERE resource_id = ?", (pid, r_id))

        # Team
        if p["reached_step"] >= 3:
            t_status = 'RELEASED' if p["comp"][2] == 1 else 'ASSIGNED'
            with team_db.begin() as tx:
                rows = tx.fetchall("SELECT member_id FROM team_members WHERE availability_status = 'AVAILABLE' LIMIT 1")
                if rows:
                    m_id = rows[0][0]
                    tx.execute('INSERT INTO team_assignments (assignment_id, member_id, project_id, role_assigned, status, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                        (create_uuid(), m_id, pid, 'Ecologist', t_status, created))
                    if t_status == 'ASSIGNED':
                        tx.execute("UPDATE team_members SET availability_status = 'ASSIGNED' WHERE member_id = ?", (m_id,))

        # Regulatory
        if p["reached_step"] >= 4 and p["permit_status"]:
            with regulatory_db.begin() as tx:
                tx.execute('''
                    INSERT INTO permits (permit_id, project_id, permit_type, issuing_agency, status, created_at, decision_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (create_uuid(), pid, 'ENVIRONMENTAL', 'Forest Dept', p["permit_status"], created, created))
                
        print(f"Seeded: {p['title']} [{p['status']}]")

    print("\nAll operations seeded successfully.")

if __name__ == "__main__":
    seed()
