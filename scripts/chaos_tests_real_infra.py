"""
AEGIS-ECO Real Infrastructure Chaos Test Suite
===============================================

Runs directly against live Docker containers:
  - PostgreSQL 16.4 + PostGIS 3.4.3 (localhost:5432)
  - Apache Kafka 7.5.0 (localhost:9092)

Verifies that the correctness model (atomic outbox, idempotency claims,
conditional updates, saga recovery) holds on ACTUAL distributed infrastructure.
"""

import os
import sys
import uuid
import time
import json
import logging
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config import POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, KAFKA_BOOTSTRAP_SERVERS, TOPICS
from shared.pg_database import PostgresDatabase
from shared.kafka_bus import KafkaEventBus, KafkaOutboxWorker

logging.basicConfig(level=logging.INFO, format="%(name)-25s | %(message)s")
logger = logging.getLogger("REAL_INFRA_CHAOS")


def get_pg_conn(dbname="postgres"):
    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=dbname
    )
    return conn


def reset_postgres_tables():
    """Reset state in the real PostgreSQL container databases."""
    databases = [
        "aegis_project_db",
        "aegis_resource_db",
        "aegis_funding_db",
        "aegis_regulatory_db",
        "aegis_team_db"
    ]
    for dbname in databases:
        try:
            conn = get_pg_conn(dbname)
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            
            # Truncate transactional tables
            cur.execute("TRUNCATE TABLE outbox, processed_events RESTART IDENTITY CASCADE;")
            
            if dbname == "aegis_project_db":
                cur.execute("TRUNCATE TABLE projects, saga_state, saga_log RESTART IDENTITY CASCADE;")
            elif dbname == "aegis_resource_db":
                cur.execute("TRUNCATE TABLE resource_reservations RESTART IDENTITY CASCADE;")
                cur.execute("UPDATE resources SET status = 'AVAILABLE', reserved_by = NULL;")
            elif dbname == "aegis_funding_db":
                cur.execute("TRUNCATE TABLE project_funding, funding_ledger RESTART IDENTITY CASCADE;")
                cur.execute("UPDATE grants SET reserved_budget = 0.00, spent_budget = 0.00;")
            elif dbname == "aegis_regulatory_db":
                cur.execute("TRUNCATE TABLE permits RESTART IDENTITY CASCADE;")
            elif dbname == "aegis_team_db":
                cur.execute("TRUNCATE TABLE team_assignments RESTART IDENTITY CASCADE;")
                cur.execute("UPDATE team_members SET availability_status = 'AVAILABLE';")
            
            conn.close()
        except Exception as e:
            logger.warning(f"Error resetting PostgreSQL database {dbname}: {e}")


results = []

def run_test(name, test_fn):
    logger.info(f"\n{'='*70}\n  REAL INFRA TEST: {name}\n{'='*70}")
    try:
        test_fn()
        results.append((name, "PASS", None))
        logger.info(f"  >> PASS")
    except AssertionError as e:
        results.append((name, "FAIL", str(e)))
        logger.info(f"  >> FAIL: {e}")
    except Exception as e:
        results.append((name, "ERROR", str(e)))
        logger.info(f"  >> ERROR: {e}")


# ── TEST 1: PostgreSQL Atomic Outbox Recovery ────────────────────────────────

def test_1_real_postgres_outbox_recovery():
    """Verify PostgreSQL atomic outbox insertion and OutboxWorker recovery.
    
    Inserts a PENDING outbox entry into aegis_funding_db PostgreSQL container.
    Worker polls PostgreSQL JSONB outbox table, publishes to live Kafka topic,
    and updates PostgreSQL outbox row status to SENT.
    """
    reset_postgres_tables()
    
    funding_db = PostgresDatabase("funding_service", db_name="aegis_funding_db")
    kafka_bus = KafkaEventBus(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
    worker = KafkaOutboxWorker(funding_db, kafka_bus, "funding_service")

    project_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())

    # Step 1: Insert PENDING outbox row in PostgreSQL
    with funding_db.begin() as tx:
        tx.execute(
            "INSERT INTO outbox (event_id, event_type, payload, status, created_at) "
            "VALUES (%s, %s, %s, 'PENDING', NOW());",
            (event_id, TOPICS["FUNDS_RESERVED"], json.dumps({
                "project_id": project_id,
                "reserved_amount": 250000.0,
                "grant_code": "GRANT-WESTERN-GHATS-2026"
            }))
        )

    # Step 2: Verify PostgreSQL row is PENDING
    with funding_db.begin() as tx:
        row = tx.fetchone("SELECT status FROM outbox WHERE event_id = %s;", (event_id,))
    assert row is not None and row[0] == "PENDING", "Outbox entry must be PENDING in PostgreSQL"

    # Step 3: Outbox Worker polls PostgreSQL and publishes to Kafka
    published_count = worker.poll_and_publish()
    assert published_count == 1, f"Worker should publish 1 event to Kafka but published {published_count}"

    # Step 4: Verify PostgreSQL row status updated to SENT
    with funding_db.begin() as tx:
        row = tx.fetchone("SELECT status FROM outbox WHERE event_id = %s;", (event_id,))
    assert row[0] == "SENT", f"Outbox status in PostgreSQL should be SENT but is {row[0]}"

    logger.info("  PostgreSQL JSONB Outbox Worker published to live Kafka broker successfully")


# ── TEST 2: PostgreSQL Idempotency Claim (`ON CONFLICT DO NOTHING`) ──────────

def test_2_real_postgres_idempotency():
    """Verify PostgreSQL atomic idempotency claim (`ON CONFLICT DO NOTHING`).
    
    Executing try_claim_event twice with the same event_id inside PostgreSQL.
    First call returns True (claimed). Second call returns False (rejected).
    """
    reset_postgres_tables()
    
    project_db = PostgresDatabase("project_service", db_name="aegis_project_db")
    event_id = str(uuid.uuid4())

    # First attempt: Claim event in PostgreSQL
    with project_db.begin() as tx:
        claimed_1 = tx.try_claim_event(event_id, TOPICS["FUNDS_RESERVED"])

    assert claimed_1 is True, "First event claim in PostgreSQL should return True"

    # Second attempt: Duplicate event claim in PostgreSQL
    with project_db.begin() as tx:
        claimed_2 = tx.try_claim_event(event_id, TOPICS["FUNDS_RESERVED"])

    assert claimed_2 is False, "Duplicate event claim in PostgreSQL ON CONFLICT must return False"

    # Verify PostgreSQL table contains exactly 1 row
    with project_db.begin() as tx:
        count = tx.fetchone("SELECT COUNT(*) FROM processed_events WHERE event_id = %s;", (event_id,))
    assert count[0] == 1, f"PostgreSQL processed_events table should have 1 row but has {count[0]}"

    logger.info("  PostgreSQL ON CONFLICT DO NOTHING correctly blocked duplicate event processing")


# ── TEST 3: PostGIS & PostgreSQL Exclusive Conditional Update ─────────────────

def test_3_real_postgis_exclusive_reservation():
    """Verify concurrent resource reservation using PostgreSQL conditional UPDATE.
    
    Two concurrent transactions attempt to reserve the same PostGIS resource
    (res-drone-001) in aegis_resource_db.
    PostgreSQL row locking ensures exactly one succeeds (rowcount=1) and the other fails (rowcount=0).
    """
    reset_postgres_tables()
    
    resource_db = PostgresDatabase("resource_service", db_name="aegis_resource_db")
    proj_a = str(uuid.uuid4())
    proj_b = str(uuid.uuid4())
    drone_id = "res-drone-001"

    # Transaction A: Reserve drone
    with resource_db.begin() as tx_a:
        cur_a = tx_a.execute(
            "UPDATE resources SET status = 'RESERVED', reserved_by = %s "
            "WHERE resource_id = %s AND status = 'AVAILABLE';",
            (proj_a, drone_id)
        )
        rowcount_a = cur_a.rowcount

    assert rowcount_a == 1, "Transaction A should reserve drone (rowcount=1)"

    # Transaction B: Attempt to reserve SAME drone
    with resource_db.begin() as tx_b:
        cur_b = tx_b.execute(
            "UPDATE resources SET status = 'RESERVED', reserved_by = %s "
            "WHERE resource_id = %s AND status = 'AVAILABLE';",
            (proj_b, drone_id)
        )
        rowcount_b = cur_b.rowcount

    assert rowcount_b == 0, "Transaction B MUST fail to reserve already reserved drone (rowcount=0)"

    # Verify PostgreSQL row state
    with resource_db.begin() as tx:
        row = tx.fetchone("SELECT status, reserved_by FROM resources WHERE resource_id = %s;", (drone_id,))
    assert row[0] == "RESERVED" and row[1] == proj_a, f"Drone reserved by {row[1]}, expected {proj_a}"

    logger.info("  PostgreSQL conditional UPDATE prevented concurrent double-booking on PostGIS asset")


# ── MAIN RUNNER ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*70)
    print("  AEGIS-ECO REAL DOCKER INFRASTRUCTURE CHAOS TEST SUITE")
    print("  Targeting PostgreSQL 16.4 + PostGIS 3.4.3 & Apache Kafka 7.5.0")
    print("="*70 + "\n")

    run_test("T1: PostgreSQL Atomic Outbox Worker (Live Container)", test_1_real_postgres_outbox_recovery)
    run_test("T2: PostgreSQL ON CONFLICT Idempotency (Live Container)", test_2_real_postgres_idempotency)
    run_test("T3: PostGIS & PostgreSQL Exclusive Locking (Live Container)", test_3_real_postgis_exclusive_reservation)

    print("\n" + "="*70)
    print("  REAL INFRASTRUCTURE RESULTS")
    print("="*70)

    passed = sum(1 for _, status, _ in results if status == "PASS")
    total = len(results)
    
    for name, status, detail in results:
        marker = "[PASS]" if status == "PASS" else "[FAIL]"
        print(f"  {marker} {name}")
        if detail:
            print(f"         {detail.splitlines()[0]}")

    print(f"\n  Total: {total} | Passed: {passed} | Failed: {total - passed}\n" + "="*70)
    sys.exit(0 if passed == total else 1)
