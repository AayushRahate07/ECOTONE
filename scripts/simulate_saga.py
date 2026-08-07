"""
AEGIS-ECO Integration Test & Saga Simulation Harness
======================================================

Runs end-to-end integration tests for:
  1. Happy Path Saga execution (DRAFT -> FUNDED -> RESERVED -> TEAM -> PERMIT APPROVED -> ACTIVE)
  2. Permit Rejection Compensation Cascade (DRAFT -> PERMIT REJECTED -> COMPENSATING -> CANCELLED)
  3. Analytics Read Model projection

Uses outbox workers to process transactional outbox events step-by-step.
"""

import sys
import os
import time
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.project_service import project_service, project_outbox
from services.resource_service import resource_service, resource_outbox
from services.funding_service import funding_service, funding_outbox
from services.regulatory_service import regulatory_gateway, regulatory_outbox
from services.team_service import team_service, team_outbox
from services.analytics_service import analytics_service

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("SAGA_SIMULATOR")


def drain_outboxes(max_rounds=10):
    """Run all service outbox workers until no more PENDING events."""
    for _ in range(max_rounds):
        total = 0
        total += project_outbox.run_once()
        total += funding_outbox.run_once()
        total += resource_outbox.run_once()
        total += team_outbox.run_once()
        total += regulatory_outbox.run_once()
        if total == 0:
            break


def reset_db_state():
    """Reset resource availability, team member status, and grant budgets for simulation test isolation."""
    from services.resource_service import resource_db
    from services.funding_service import funding_db
    from services.team_service import team_db
    from services.analytics_service import analytics_service

    analytics_service.reset()

    with resource_db.begin() as tx:
        tx.execute("UPDATE resources SET status = 'AVAILABLE', reserved_by = NULL")
        tx.execute("DELETE FROM resource_reservations")

    with funding_db.begin() as tx:
        tx.execute("UPDATE grants SET reserved_budget = 0, spent_budget = 0")
        tx.execute("DELETE FROM project_funding")
        tx.execute("DELETE FROM funding_ledger")

    with team_db.begin() as tx:
        tx.execute("UPDATE team_members SET availability_status = 'AVAILABLE'")
        tx.execute("DELETE FROM team_assignments")


def run_simulation():
    reset_db_state()

    print("=" * 80)
    print("      AEGIS-ECO INTEGRATION TEST & SAGA SIMULATION HARNESS")
    print("=" * 80)

    # 1. TEST HAPPY PATH
    print("\n--- [TEST 1] EXECUTE HAPPY PATH SAGA ---")
    regulatory_gateway.set_simulation_mode("HAPPY_PATH")
    
    project_1 = project_service.create_project(
        title="Western Ghats eDNA Biodiversity Survey",
        description="Comprehensive eDNA sampling and species identification across Satara corridor",
        location_name="Satara Forest Corridor",
        site_lat=17.6805,
        site_lon=74.0183,
        budget_requested=350000.0,
        required_equipment=["DRONE", "SEQUENCER"],
        required_team=["Lead Ecologist", "Drone Operator"],
        priority="HIGH"
    )
    
    pid_1 = project_1["project_id"]
    print(f"-> Project Initiated ID: {pid_1}")

    # Process all outbox steps to complete saga
    drain_outboxes()

    proj_1_data = project_service.get_project(pid_1)
    logs_1 = project_service.get_saga_log(pid_1)

    print(f"-> Final Project State: {proj_1_data['status']}")
    print(f"-> Saga Step History Log Count: {len(logs_1)}")
    
    for log in logs_1:
        print(f"   [LOG] {log['step']} | Status: {log['status']} | {log['details']}")

    assert proj_1_data["status"] == "ACTIVE", f"Happy path project should be ACTIVE but got {proj_1_data['status']}"
    print("[PASS] TEST 1 PASSED: Happy Path Saga completed successfully!")

    # 2. TEST PERMIT REJECTION COMPENSATION CASCADE
    print("\n--- [TEST 2] EXECUTE PERMIT REJECTION COMPENSATION CASCADE ---")
    regulatory_gateway.set_simulation_mode("FORCE_REJECT")

    project_2 = project_service.create_project(
        title="Koyna Restricted Corridor Mapping",
        description="LiDAR drone survey near Koyna sanctuary border",
        location_name="Koyna Buffer Zone",
        site_lat=17.4500,
        site_lon=73.7000,
        budget_requested=420000.0,
        required_equipment=["DRONE", "RADAR"],
        required_team=["Drone Operator", "Archaeologist"],
        priority="CRITICAL"
    )

    pid_2 = project_2["project_id"]
    print(f"-> Project Initiated ID: {pid_2}")

    # Process all outbox steps to execute compensation cascade
    drain_outboxes()

    proj_2_data = project_service.get_project(pid_2)
    logs_2 = project_service.get_saga_log(pid_2)

    print(f"-> Final Project State: {proj_2_data['status']}")
    print(f"-> Saga Step History Log Count: {len(logs_2)}")

    for log in logs_2:
        print(f"   [LOG] {log['step']} | Status: {log['status']} | {log['details']}")

    assert proj_2_data["status"] == "CANCELLED", f"Compensation cascade project should be CANCELLED but got {proj_2_data['status']}"
    print("[PASS] TEST 2 PASSED: Compensation Cascade executed cleanly and project marked CANCELLED!")

    # 3. VERIFY ANALYTICS READ-MODEL
    print("\n--- [TEST 3] VERIFY ANALYTICS READ MODEL & DASHBOARD TELEMETRY ---")
    summary = analytics_service.get_dashboard_summary()
    print(f"-> Total Event Bus Messages Processed: {summary['metrics']['event_count']}")
    print(f"-> Total Tracked Projects in Read Model: {summary['metrics']['total_projects']}")
    print(f"-> Active Projects: {summary['metrics']['active_projects']}")
    print(f"-> Compensated/Cancelled Projects: {summary['metrics']['cancelled_projects']}")
    print(f"-> Total Reserved Grant Funds: ${summary['metrics']['reserved_funds']:,.2f}")
    
    assert summary['metrics']['total_projects'] >= 2, "Read model should track at least 2 projects"
    assert summary['metrics']['active_projects'] >= 1, "Should have at least 1 active project"
    assert summary['metrics']['cancelled_projects'] >= 1, "Should have at least 1 cancelled project"
    print("[PASS] TEST 3 PASSED: Read-model analytics projection matches expected domain metrics!")

    print("\n" + "=" * 80)
    print("ALL SAGA INTEGRATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_simulation()
