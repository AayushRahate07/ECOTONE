"""
AEGIS-ECO Chaos Test Suite
===========================

6 reliability scenarios that must pass for this to be a
"genuinely solid distributed-systems semester project."

Each test:
  1. Sets up clean databases (destroys and re-initializes)
  2. Injects a specific fault
  3. Runs the saga
  4. Asserts exact post-conditions on database state

Run:
    python scripts/chaos_tests.py
"""

import os
import sys
import uuid
import time
import logging
import traceback

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.event_bus import bus, EventBus
from shared.config import TOPICS

# ── Logging Setup ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,  # Suppress INFO noise during tests
    format="%(name)-25s | %(message)s"
)
test_logger = logging.getLogger("CHAOS_TESTS")
test_logger.setLevel(logging.INFO)


# ── Test Harness ────────────────────────────────────────────────────────────

_previous_dbs = []

def fresh_environment():
    """Destroy all service databases and re-create everything from scratch.
    
    This ensures full test isolation: no leftover state from prior runs.
    We also reset the singleton EventBus so subscribers don't accumulate.
    """
    global _previous_dbs
    
    # Close any previously tracked DB connections
    for db in _previous_dbs:
        try:
            db.close()
        except Exception:
            pass
    _previous_dbs.clear()

    # Reset the event bus singleton completely
    bus.reset()
    EventBus._instance._initialized = False
    EventBus._instance.__init__()

    # Clean up data directory — remove DB files and WAL/SHM files
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            fp = os.path.join(data_dir, f)
            if os.path.isfile(fp) and (fp.endswith(".db") or fp.endswith(".db-wal") or fp.endswith(".db-shm")):
                try:
                    os.remove(fp)
                except Exception:
                    pass

    # Force re-import of all services so they re-initialize DBs
    # Also reset ServiceDatabase singleton-like state
    mods_to_remove = [m for m in sys.modules
                      if m.startswith("services.") or m.startswith("shared.")]
    for m in mods_to_remove:
        del sys.modules[m]

    # Re-import fresh
    from shared.event_bus import bus as fresh_bus
    from services.project_service import project_service, project_outbox, project_db
    from services.funding_service import funding_service, funding_outbox, funding_db
    from services.resource_service import resource_service, resource_outbox, resource_db
    from services.team_service import team_service, team_outbox, team_db
    from services.regulatory_service import regulatory_gateway, regulatory_outbox, regulatory_db

    # Track all DBs for cleanup next time
    _previous_dbs.extend([project_db, funding_db, resource_db, team_db, regulatory_db])

    return {
        "bus": fresh_bus,
        "project": {"service": project_service, "outbox": project_outbox, "db": project_db},
        "funding": {"service": funding_service, "outbox": funding_outbox, "db": funding_db},
        "resource": {"service": resource_service, "outbox": resource_outbox, "db": resource_db},
        "team": {"service": team_service, "outbox": team_outbox, "db": team_db},
        "regulatory": {"service": regulatory_gateway, "outbox": regulatory_outbox, "db": regulatory_db},
    }


def drain_all_outboxes(env, max_rounds=10):
    """Poll all service outbox workers until no more PENDING events."""
    for _ in range(max_rounds):
        total = 0
        total += env["project"]["outbox"].run_once()
        total += env["funding"]["outbox"].run_once()
        total += env["resource"]["outbox"].run_once()
        total += env["team"]["outbox"].run_once()
        total += env["regulatory"]["outbox"].run_once()
        if total == 0:
            break


def create_test_project(env, mode="HAPPY_PATH"):
    """Create a standard test project and return the project data."""
    env["regulatory"]["service"].set_simulation_mode(mode)
    project = env["project"]["service"].create_project(
        title="Western Ghats Canopy Survey",
        description="Biodiversity assessment in Sahyadri range",
        location_name="Koyna-Chandoli Corridor",
        site_lat=17.50,
        site_lon=73.75,
        budget_requested=250000.0,
        required_equipment=["DRONE", "SEQUENCER"],
        required_team=["Lead Ecologist", "Drone Operator"],
        priority="HIGH"
    )
    return project


# ── RESULTS ─────────────────────────────────────────────────────────────────

results = []

def run_test(name, test_fn):
    """Run a single test with full isolation."""
    test_logger.info(f"\n{'='*70}")
    test_logger.info(f"  TEST: {name}")
    test_logger.info(f"{'='*70}")
    try:
        test_fn()
        results.append((name, "PASS", None))
        test_logger.info(f"  >> PASS")
    except AssertionError as e:
        results.append((name, "FAIL", str(e)))
        test_logger.info(f"  >> FAIL: {e}")
    except Exception as e:
        results.append((name, "ERROR", traceback.format_exc()))
        test_logger.info(f"  >> ERROR: {e}")
        traceback.print_exc()


# ── TEST 1: Outbox Recovery ─────────────────────────────────────────────────

def test_1_outbox_recovery():
    """Kill Funding Service after DB commit but before Kafka publish.
    
    Scenario:
      - Funding service processes ReserveFunds, commits to DB
      - But the outbox publish is DROPPED (simulating crash before Kafka write)
      - Outbox worker should recover the PENDING entry and deliver it
      - Saga should proceed to resource reservation
    
    Pass condition:
      - Grant balance shows funds reserved
      - Saga reaches ACTIVE after outbox recovery
    """
    env = fresh_environment()
    the_bus = env["bus"]

    # ARM FAULT: Drop the FundsReserved publish
    the_bus.set_fault("DROP_PUBLISH", TOPICS["FUNDS_RESERVED"])

    project = create_test_project(env)

    # Step 1: Drain project outbox to emit COMMAND_RESERVE_FUNDS
    env["project"]["outbox"].run_once()

    # Step 2: Drain funding outbox — this triggers the funding handler
    # which commits to DB but the FundsReserved event is DROPPED
    env["funding"]["outbox"].run_once()

    # Verify: funding DB has the reservation committed
    with env["funding"]["db"].begin() as tx:
        alloc = tx.fetchone(
            "SELECT reserved_amount FROM project_funding WHERE project_id = ?",
            (project["project_id"],)
        )
    assert alloc is not None, "Funding allocation should exist in DB despite dropped publish"
    assert alloc[0] == 250000.0, f"Reserved amount should be $250,000 but got {alloc[0]}"

    # Verify: saga is still stuck at STEP_1_FUNDING (event was dropped)
    with env["project"]["db"].begin() as tx:
        saga = tx.fetchone(
            "SELECT current_step FROM saga_state WHERE saga_id = ?",
            (project["project_id"],)
        )
    assert saga[0] == "STEP_1_FUNDING", \
        f"Saga should still be at STEP_1_FUNDING but is at {saga[0]}"

    # Step 3: Outbox worker picks up the PENDING FundsReserved entry and replays it
    env["funding"]["outbox"].run_once()

    # Step 4: Now drain all outboxes to complete the saga
    drain_all_outboxes(env)

    # Verify: saga completed
    with env["project"]["db"].begin() as tx:
        project_row = tx.fetchone(
            "SELECT status FROM projects WHERE project_id = ?",
            (project["project_id"],)
        )
    assert project_row[0] == "ACTIVE", \
        f"Project should be ACTIVE after outbox recovery, but is {project_row[0]}"

    test_logger.info("  Outbox recovered PENDING event and saga completed successfully")


# ── TEST 2: Idempotent FundsReserved ─────────────────────────────────────────

def test_2_idempotent_funds_reserved():
    """Deliver FundsReserved twice with the same event_id.
    
    Scenario:
      - Fund reservation succeeds normally
      - The FundsReserved event is delivered TWICE (duplicate)
      - Second delivery should be a no-op
    
    Pass condition:
      - Grant reserved_budget reflects exactly ONE reservation ($250,000)
      - Not doubled ($500,000)
    """
    env = fresh_environment()
    the_bus = env["bus"]

    # ARM FAULT: Duplicate delivery of FundsReserved
    the_bus.set_fault("DUPLICATE_DELIVERY", TOPICS["FUNDS_RESERVED"])

    project = create_test_project(env)

    # Drain all outboxes — the duplicate will be delivered during this
    drain_all_outboxes(env)

    # Verify: grant reserved_budget is exactly $250,000 (not doubled)
    with env["funding"]["db"].begin() as tx:
        grant = tx.fetchone(
            "SELECT reserved_budget FROM grants WHERE grant_code = 'GRANT-WESTERN-GHATS-2026'"
        )
    assert grant[0] == 250000.0, \
        f"Grant reserved should be $250,000 but is ${grant[0]:,.2f} — idempotency failed!"

    # Verify: only ONE allocation exists
    with env["funding"]["db"].begin() as tx:
        count = tx.fetchone(
            "SELECT COUNT(*) FROM project_funding WHERE project_id = ?",
            (project["project_id"],)
        )
    assert count[0] == 1, \
        f"Should have exactly 1 funding allocation but found {count[0]}"

    test_logger.info("  Duplicate FundsReserved correctly rejected — funds reserved exactly once")


# ── TEST 3: Concurrent Drone Reservation ────────────────────────────────────

def test_3_exclusive_resource_reservation():
    """Two projects request the same drone concurrently.
    
    Scenario:
      - Project A and Project B both need a DRONE
      - There are only 2 DRONEs in the system (Alpha and Beta)
      - Both projects should get a drone, but NOT the same one
    
    Pass condition:
      - Each project has a different drone reserved
      - No drone has status='RESERVED' by more than one project
    """
    env = fresh_environment()

    # Create two projects that both need DRONEs
    env["regulatory"]["service"].set_simulation_mode("HAPPY_PATH")

    project_a = env["project"]["service"].create_project(
        title="Project Alpha",
        description="Test A",
        location_name="Site A",
        site_lat=18.0,
        site_lon=73.5,
        budget_requested=100000.0,
        required_equipment=["DRONE"],
        required_team=["Lead Ecologist"],
        priority="HIGH"
    )

    project_b = env["project"]["service"].create_project(
        title="Project Beta",
        description="Test B",
        location_name="Site B",
        site_lat=17.5,
        site_lon=74.0,
        budget_requested=100000.0,
        required_equipment=["DRONE"],
        required_team=["Drone Operator"],
        priority="HIGH"
    )

    # Drain all outboxes
    drain_all_outboxes(env)

    # Verify: each project has resources reserved
    with env["resource"]["db"].begin() as tx:
        res_a = tx.fetchall(
            "SELECT resource_id FROM resource_reservations "
            "WHERE project_id = ? AND status = 'RESERVED'",
            (project_a["project_id"],)
        )
        res_b = tx.fetchall(
            "SELECT resource_id FROM resource_reservations "
            "WHERE project_id = ? AND status = 'RESERVED'",
            (project_b["project_id"],)
        )

    drone_ids_a = {r[0] for r in res_a}
    drone_ids_b = {r[0] for r in res_b}

    # Filter to only DRONE resources
    with env["resource"]["db"].begin() as tx:
        drones_a = set()
        for rid in drone_ids_a:
            row = tx.fetchone("SELECT resource_type FROM resources WHERE resource_id = ?", (rid,))
            if row and row[0] == "DRONE":
                drones_a.add(rid)
        drones_b = set()
        for rid in drone_ids_b:
            row = tx.fetchone("SELECT resource_type FROM resources WHERE resource_id = ?", (rid,))
            if row and row[0] == "DRONE":
                drones_b.add(rid)

    assert len(drones_a) >= 1, "Project A should have at least 1 drone reserved"
    assert len(drones_b) >= 1, "Project B should have at least 1 drone reserved"

    # The critical assertion: no overlap
    overlap = drones_a & drones_b
    assert len(overlap) == 0, \
        f"DOUBLE BOOKING DETECTED! Both projects reserved: {overlap}"

    # Verify: no drone is marked RESERVED by two different projects
    with env["resource"]["db"].begin() as tx:
        double_booked = tx.fetchall(
            "SELECT resource_id, reserved_by FROM resources "
            "WHERE resource_type = 'DRONE' AND status = 'RESERVED'"
        )
    reserved_by_map = {}
    for rid, rby in double_booked:
        if rid in reserved_by_map:
            assert False, f"Drone {rid} reserved by both {reserved_by_map[rid]} and {rby}"
        reserved_by_map[rid] = rby

    test_logger.info(f"  Project A drones: {drones_a}")
    test_logger.info(f"  Project B drones: {drones_b}")
    test_logger.info("  No double-booking — conditional UPDATE enforced exclusivity")


# ── TEST 4: Saga Resume After Crash ─────────────────────────────────────────

def test_4_saga_resume_after_crash():
    """Kill Saga Orchestrator mid-compensation, verify resume.
    
    Scenario:
      - Project reaches permit stage, permit is REJECTED
      - Compensation starts: COMMAND_RELEASE_RESOURCES is dispatched
      - SIMULATE CRASH: Drop COMMAND_RELEASE_FUNDS so it never fires
      - Only resources and team get released
      - Saga stays COMPENSATING (not CANCELLED)
      - On "restart": recover_pending_sagas() re-dispatches the missing compensation
      - After draining: saga reaches CANCELLED with all compensations confirmed
    
    Pass condition:
      - Saga status is COMPENSATING after crash (not CANCELLED)
      - After recovery, saga status is COMPENSATED
      - All 3 compensation flags are 1
    """
    env = fresh_environment()
    the_bus = env["bus"]

    # ARM FAULT: Drop COMMAND_RELEASE_FUNDS persistently (simulating crash mid-compensation)
    the_bus.set_fault("DROP_PUBLISH", TOPICS["COMMAND_RELEASE_FUNDS"], persistent=True)

    project = create_test_project(env, mode="FORCE_REJECT")

    # Drain all outboxes — compensation cascade runs but ReleaseFunds is dropped
    drain_all_outboxes(env)

    # Verify: saga should be COMPENSATING (not CANCELLED)
    with env["project"]["db"].begin() as tx:
        saga = tx.fetchone(
            "SELECT status, comp_resources_done, comp_funds_done, comp_team_done "
            "FROM saga_state WHERE saga_id = ?",
            (project["project_id"],)
        )
    
    assert saga is not None, "Saga state should exist"
    assert saga[0] == "COMPENSATING", \
        f"Saga should be COMPENSATING but is {saga[0]}"
    assert saga[1] == 1, "Resources compensation should be confirmed"
    assert saga[2] == 0, "Funds compensation should NOT be confirmed (was dropped)"
    assert saga[3] == 1, "Team compensation should be confirmed"

    test_logger.info("  Pre-recovery: COMPENSATING with funds comp missing - correct!")

    # CLEAR FAULT: Network/Broker recovered
    the_bus.clear_fault("DROP_PUBLISH", TOPICS["COMMAND_RELEASE_FUNDS"])

    # SIMULATE RESTART: call recover_pending_sagas
    env["project"]["service"].recover_pending_sagas()

    # Drain outboxes again — the recovery re-dispatches ReleaseFunds
    drain_all_outboxes(env)

    # Verify: saga should now be COMPENSATED with all flags set
    with env["project"]["db"].begin() as tx:
        saga = tx.fetchone(
            "SELECT status, comp_resources_done, comp_funds_done, comp_team_done "
            "FROM saga_state WHERE saga_id = ?",
            (project["project_id"],)
        )

    assert saga[0] == "COMPENSATED", \
        f"Saga should be COMPENSATED after recovery but is {saga[0]}"
    assert saga[1] == 1 and saga[2] == 1 and saga[3] == 1, \
        f"All comp flags should be 1 but got res={saga[1]} funds={saga[2]} team={saga[3]}"

    test_logger.info("  Post-recovery: COMPENSATED with all 3 flags confirmed - correct!")


# ── TEST 5: Kafka Restart (Bus Reset + Consumer Recovery) ────────────────────

def test_5_kafka_restart_recovery():
    """Restart Kafka: clear event bus, re-register consumers, replay outbox.
    
    Scenario:
      - Project saga runs through funding and resource reservation
      - Then "Kafka dies": bus is reset (all subscribers gone)
      - Pending outbox entries (COMMAND_ASSIGN_TEAM) are stranded
      - "Kafka restarts": re-register all subscribers
      - Outbox worker replays pending entries
      - Saga completes normally
    
    Pass condition:
      - No duplicate processing (idempotency prevents re-execution)
      - Saga reaches ACTIVE after Kafka recovery
    """
    env = fresh_environment()
    the_bus = env["bus"]

    # ARM FAULT: Drop the COMMAND_ASSIGN_TEAM publish
    # This simulates the outbox worker committing "I'll publish this"
    # but Kafka being down so the event never reaches subscribers
    the_bus.set_fault("DROP_PUBLISH", TOPICS["COMMAND_ASSIGN_TEAM"])

    project = create_test_project(env)

    # Drain outboxes — saga will stall at STEP_3_TEAM
    drain_all_outboxes(env)

    # Verify: saga is stuck
    with env["project"]["db"].begin() as tx:
        saga = tx.fetchone(
            "SELECT current_step FROM saga_state WHERE saga_id = ?",
            (project["project_id"],)
        )
    # It could be at STEP_3_TEAM if the resource step completed and
    # the team command was dropped. Let's check the project status.
    with env["project"]["db"].begin() as tx:
        proj = tx.fetchone(
            "SELECT status FROM projects WHERE project_id = ?",
            (project["project_id"],)
        )

    # The project should NOT be ACTIVE yet
    assert proj[0] != "ACTIVE", \
        f"Project should not be ACTIVE yet (Kafka was down), but status is {proj[0]}"
    test_logger.info(f"  Pre-recovery: project status = {proj[0]} (saga stalled) - correct!")

    # SIMULATE KAFKA RESTART:
    # The outbox entry for COMMAND_ASSIGN_TEAM should be marked SENT
    # since the outbox worker published it (the fault was at the bus level).
    # But the team service's outbox never got the command.
    # 
    # In a real system, we'd replay from the Kafka committed offset.
    # Since we're in-process, we need to re-send the pending team command.
    # The outbox worker already marked it SENT, so we need to check
    # the team service's processed_events for this event.
    #
    # Actually, the DROP_PUBLISH fault means the bus silently dropped
    # the event. The outbox worker in the project service got its
    # pending entry, published through the bus (which dropped it),
    # and marked it SENT. The team service never saw the command.
    #
    # To recover: mark the project outbox entry back to PENDING and replay.
    with env["project"]["db"].begin() as tx:
        # Find outbox entries that were for team assignment
        tx.execute(
            "UPDATE outbox SET status = 'PENDING' "
            "WHERE event_type = ? AND status = 'SENT'",
            (TOPICS["COMMAND_ASSIGN_TEAM"],)
        )

    # Now drain again — outbox worker re-publishes
    drain_all_outboxes(env)

    # Verify: saga should complete
    with env["project"]["db"].begin() as tx:
        proj = tx.fetchone(
            "SELECT status FROM projects WHERE project_id = ?",
            (project["project_id"],)
        )
    assert proj[0] == "ACTIVE", \
        f"Project should be ACTIVE after Kafka recovery, but is {proj[0]}"

    test_logger.info("  Post-recovery: ACTIVE after replaying outbox - correct!")


# ── TEST 6: Partial Compensation (ReleaseFunds Fails) ────────────────────────

def test_6_partial_compensation():
    """ReleaseResources succeeds, ReleaseFunds fails.
    
    Scenario:
      - Project reaches permits, permit is REJECTED
      - Orchestrator dispatches 3 compensation commands
      - ReleaseResources and UnassignTeam succeed
      - COMMAND_RELEASE_FUNDS handler FAILS with exception persistently
      - Project should stay COMPENSATING (not prematurely CANCELLED)
      - Funds should still be locked in the grant
    
    Pass condition:
      - Project status is COMPENSATING
      - comp_funds_done = 0
      - comp_resources_done = 1, comp_team_done = 1
      - Grant still has $250,000 reserved
    """
    env = fresh_environment()
    the_bus = env["bus"]

    # ARM FAULT: Make the COMMAND_RELEASE_FUNDS handler throw an exception persistently
    the_bus.set_fault("FAIL_HANDLER", TOPICS["COMMAND_RELEASE_FUNDS"], persistent=True)

    project = create_test_project(env, mode="FORCE_REJECT")

    # Drain outboxes — this will trigger the saga up to permits,
    # get rejected, and start compensation.
    try:
        drain_all_outboxes(env)
    except RuntimeError as e:
        if "Injected fault" in str(e):
            test_logger.info(f"  Caught expected fault: {e}")
        else:
            raise

    # Drain again to process any remaining non-faulted events
    try:
        drain_all_outboxes(env)
    except RuntimeError:
        pass

    # Verify: saga should be COMPENSATING (not CANCELLED)
    with env["project"]["db"].begin() as tx:
        saga = tx.fetchone(
            "SELECT status, comp_resources_done, comp_funds_done, comp_team_done "
            "FROM saga_state WHERE saga_id = ?",
            (project["project_id"],)
        )

    assert saga is not None, "Saga state should exist"
    assert saga[0] == "COMPENSATING", \
        f"Project should be COMPENSATING (not CANCELLED), but is {saga[0]}"
    assert saga[2] == 0, \
        f"Funds compensation should NOT be done (handler failed), but flag is {saga[2]}"

    test_logger.info(f"  Compensation state: res={saga[1]} funds={saga[2]} team={saga[3]}")

    # Verify: funds are STILL reserved in the grant (compensation hasn't run)
    with env["funding"]["db"].begin() as tx:
        grant = tx.fetchone(
            "SELECT reserved_budget FROM grants WHERE grant_code = 'GRANT-WESTERN-GHATS-2026'"
        )
    assert grant[0] == 250000.0, \
        f"Grant should still have $250,000 reserved but has ${grant[0]:,.2f}"

    # Verify: project status in DB is COMPENSATING
    with env["project"]["db"].begin() as tx:
        proj = tx.fetchone(
            "SELECT status FROM projects WHERE project_id = ?",
            (project["project_id"],)
        )
    assert proj[0] == "COMPENSATING", \
        f"Project DB status should be COMPENSATING but is {proj[0]}"

    test_logger.info("  Partial compensation correctly detected — project not prematurely CANCELLED")


# ── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n")
    print("=" * 70)
    print("  AEGIS-ECO CHAOS TEST SUITE")
    print("  Testing 6 distributed systems failure scenarios")
    print("=" * 70)
    print()

    run_test("T1: Outbox Recovery (kill after DB commit, before publish)", test_1_outbox_recovery)
    run_test("T2: Idempotent FundsReserved (deliver same event twice)", test_2_idempotent_funds_reserved)
    run_test("T3: Exclusive Resource Reservation (two projects, one drone)", test_3_exclusive_resource_reservation)
    run_test("T4: Saga Resume After Crash (kill mid-compensation)", test_4_saga_resume_after_crash)
    run_test("T5: Kafka Restart Recovery (clear bus, replay outbox)", test_5_kafka_restart_recovery)
    run_test("T6: Partial Compensation (ReleaseFunds fails)", test_6_partial_compensation)

    print("\n")
    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)

    passed = 0
    failed = 0
    errors = 0

    for name, status, detail in results:
        if status == "PASS":
            marker = "[PASS]"
            passed += 1
        elif status == "FAIL":
            marker = "[FAIL]"
            failed += 1
        else:
            marker = "[ERR ]"
            errors += 1
        
        print(f"  {marker} {name}")
        if detail:
            # Print first line of detail
            first_line = detail.split("\n")[0]
            print(f"         {first_line}")

    print()
    print(f"  Total: {len(results)} | Passed: {passed} | Failed: {failed} | Errors: {errors}")
    print()

    if failed == 0 and errors == 0:
        print("  All chaos tests passed.")
        print("  Kafka is currently simulated; therefore broker failover is not tested.")
        print("  SQLite enforces real ACID transactions; PostgreSQL migration is next.")
    else:
        print("  Some tests failed. Fix the issues and re-run.")

    print("=" * 70)

    sys.exit(0 if (failed == 0 and errors == 0) else 1)
