"""
AEGIS-ECO Real PostgreSQL + PostGIS Schema Setup & Seeder
===========================================================

Connects to live aegis-postgres container (localhost:5432)
and sets up all 6 microservice databases with PostgreSQL / PostGIS DDLs.
"""

import sys
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config import POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD

DATABASES = [
    "aegis_project_db",
    "aegis_resource_db",
    "aegis_funding_db",
    "aegis_regulatory_db",
    "aegis_team_db",
    "aegis_analytics_db",
]


def get_conn(dbname="postgres"):
    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=dbname
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def setup_databases():
    print("[POSTGRES SETUP] Connecting to PostgreSQL container...")
    root_conn = get_conn("postgres")
    cur = root_conn.cursor()

    for db in DATABASES:
        cur.execute(f"SELECT 1 FROM pg_database WHERE datname = '{db}';")
        if not cur.fetchone():
            cur.execute(f"CREATE DATABASE {db};")
            print(f"[POSTGRES SETUP] Created database '{db}'")
    root_conn.close()

    # 1. Aegis Project DB
    conn = get_conn("aegis_project_db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        project_id VARCHAR(100) PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        description TEXT,
        status VARCHAR(50) DEFAULT 'DRAFT',
        location_name VARCHAR(255),
        site_lat DOUBLE PRECISION,
        site_lon DOUBLE PRECISION,
        priority VARCHAR(20) DEFAULT 'MEDIUM',
        budget_requested NUMERIC(12, 2),
        required_equipment TEXT,
        required_team TEXT,
        created_at VARCHAR(100),
        updated_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS saga_state (
        saga_id VARCHAR(100) PRIMARY KEY,
        project_id VARCHAR(100) NOT NULL,
        current_step VARCHAR(100) NOT NULL,
        status VARCHAR(50) NOT NULL,
        comp_resources_done INT DEFAULT 0,
        comp_funds_done INT DEFAULT 0,
        comp_team_done INT DEFAULT 0,
        data_json TEXT,
        updated_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS saga_log (
        log_id VARCHAR(100) PRIMARY KEY,
        saga_id VARCHAR(100),
        step VARCHAR(100),
        status VARCHAR(50),
        details TEXT,
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS outbox (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(20) DEFAULT 'PENDING',
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS processed_events (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        processed_at VARCHAR(100)
    );
    """)
    conn.close()
    print("[POSTGRES SETUP] aegis_project_db schema initialized")

    # 2. Aegis Resource DB (with PostGIS)
    conn = get_conn("aegis_resource_db")
    c = conn.cursor()
    c.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    c.execute("""
    CREATE TABLE IF NOT EXISTS resources (
        resource_id VARCHAR(100) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        resource_type VARCHAR(100) NOT NULL,
        depot_name VARCHAR(255),
        location GEOGRAPHY(Point, 4326),
        battery_pct INT DEFAULT 100,
        current_workload INT DEFAULT 0,
        status VARCHAR(50) DEFAULT 'AVAILABLE',
        hourly_cost NUMERIC(10, 2) DEFAULT 150.00,
        reserved_by VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS resource_reservations (
        reservation_id VARCHAR(100) PRIMARY KEY,
        resource_id VARCHAR(100) NOT NULL,
        project_id VARCHAR(100) NOT NULL,
        status VARCHAR(50) DEFAULT 'RESERVED',
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS spatial_constraints (
        constraint_id VARCHAR(100) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        constraint_type VARCHAR(100) NOT NULL,
        geom GEOGRAPHY(Polygon, 4326)
    );
    CREATE TABLE IF NOT EXISTS outbox (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(20) DEFAULT 'PENDING',
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS processed_events (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        processed_at VARCHAR(100)
    );
    """)

    # Seed resources into PostGIS
    c.execute("""
    INSERT INTO resources (resource_id, name, resource_type, depot_name, location, battery_pct, current_workload, status, hourly_cost) VALUES
    ('res-drone-001', 'LiDAR Drone Alpha', 'DRONE', 'Pune Depot', ST_MakePoint(73.8567, 18.5204)::geography, 95, 2, 'AVAILABLE', 250.00),
    ('res-drone-002', 'LiDAR Drone Beta', 'DRONE', 'Satara Depot', ST_MakePoint(74.0183, 17.6805)::geography, 88, 1, 'AVAILABLE', 250.00),
    ('res-seq-001', 'eDNA Sequencer Unit-1', 'SEQUENCER', 'IISER Pune Lab', ST_MakePoint(73.8152, 18.5529)::geography, 100, 0, 'AVAILABLE', 400.00),
    ('res-radar-001', 'Ground Radar GPR-7', 'RADAR', 'Kolhapur Station', ST_MakePoint(74.2433, 16.7050)::geography, 75, 4, 'AVAILABLE', 180.00),
    ('res-truck-001', 'All-Terrain 4x4 Eco Truck', 'VEHICLE', 'Western Ghats Outpost', ST_MakePoint(73.7000, 17.9000)::geography, 90, 1, 'AVAILABLE', 120.00)
    ON CONFLICT (resource_id) DO NOTHING;
    """)

    # Seed spatial constraints
    c.execute("""
    INSERT INTO spatial_constraints (constraint_id, name, constraint_type, geom) VALUES
    ('constraint-1', 'Koyna Wildlife Sanctuary Restricted Zone', 'PROTECTED_FOREST', ST_GeogFromText('POLYGON((73.65 17.40, 73.80 17.40, 73.80 17.60, 73.65 17.60, 73.65 17.40))')),
    ('constraint-2', 'Western Ghats Airspace No-Fly Polygon', 'NO_FLY_ZONE', ST_GeogFromText('POLYGON((73.40 18.10, 73.60 18.10, 73.60 18.30, 73.40 18.30, 73.40 18.10))'))
    ON CONFLICT (constraint_id) DO NOTHING;
    """)
    conn.close()
    print("[POSTGRES SETUP] aegis_resource_db schema & PostGIS spatial seed data initialized")

    # 3. Aegis Funding DB
    conn = get_conn("aegis_funding_db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS grants (
        grant_id VARCHAR(100) PRIMARY KEY,
        grant_code VARCHAR(50) UNIQUE NOT NULL,
        title VARCHAR(255) NOT NULL,
        total_budget NUMERIC(14, 2) NOT NULL,
        reserved_budget NUMERIC(14, 2) DEFAULT 0.00,
        spent_budget NUMERIC(14, 2) DEFAULT 0.00
    );
    CREATE TABLE IF NOT EXISTS project_funding (
        allocation_id VARCHAR(100) PRIMARY KEY,
        grant_id VARCHAR(100) NOT NULL,
        project_id VARCHAR(100) NOT NULL,
        reserved_amount NUMERIC(12, 2) NOT NULL,
        status VARCHAR(50) DEFAULT 'RESERVED',
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS funding_ledger (
        ledger_id VARCHAR(100) PRIMARY KEY,
        project_id VARCHAR(100) NOT NULL,
        transaction_type VARCHAR(50) NOT NULL,
        amount NUMERIC(12, 2) NOT NULL,
        balance_after NUMERIC(14, 2) DEFAULT 0.00,
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS outbox (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(20) DEFAULT 'PENDING',
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS processed_events (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        processed_at VARCHAR(100)
    );

    INSERT INTO grants (grant_id, grant_code, title, total_budget) VALUES
    ('g-1', 'GRANT-WESTERN-GHATS-2026', 'Western Ghats Biodiversity & Conservation Fund', 5000000.00),
    ('g-2', 'GRANT-HERITAGE-ASI-2026', 'Archaeological Survey Heritage Protection Fund', 3500000.00)
    ON CONFLICT (grant_code) DO NOTHING;
    """)
    conn.close()
    print("[POSTGRES SETUP] aegis_funding_db schema & grants initialized")

    # 4. Aegis Regulatory DB
    conn = get_conn("aegis_regulatory_db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS permits (
        permit_id VARCHAR(100) PRIMARY KEY,
        project_id VARCHAR(100) NOT NULL,
        permit_type VARCHAR(100) NOT NULL,
        issuing_agency VARCHAR(255) NOT NULL,
        status VARCHAR(50) DEFAULT 'PENDING',
        created_at VARCHAR(100),
        decision_date VARCHAR(100),
        rejection_reason TEXT
    );
    CREATE TABLE IF NOT EXISTS outbox (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(20) DEFAULT 'PENDING',
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS processed_events (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        processed_at VARCHAR(100)
    );
    """)
    conn.close()
    print("[POSTGRES SETUP] aegis_regulatory_db schema initialized")

    # 5. Aegis Team DB
    conn = get_conn("aegis_team_db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS team_members (
        member_id VARCHAR(100) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        role VARCHAR(100) NOT NULL,
        specialty VARCHAR(100),
        location_base VARCHAR(100),
        availability_status VARCHAR(50) DEFAULT 'AVAILABLE'
    );
    CREATE TABLE IF NOT EXISTS team_assignments (
        assignment_id VARCHAR(100) PRIMARY KEY,
        member_id VARCHAR(100) NOT NULL,
        project_id VARCHAR(100) NOT NULL,
        role_assigned VARCHAR(100),
        status VARCHAR(50) DEFAULT 'ASSIGNED',
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS outbox (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(20) DEFAULT 'PENDING',
        created_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS processed_events (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        processed_at VARCHAR(100)
    );

    INSERT INTO team_members (member_id, name, role, specialty, location_base) VALUES
    ('tm-1', 'Dr. Aris Thorne', 'Lead Ecologist', 'eDNA Sequencing', 'Pune'),
    ('tm-2', 'Elena Rostova', 'Drone Operator', 'LiDAR Topography', 'Satara'),
    ('tm-3', 'Siddharth Mehta', 'Botanist', 'Flora Taxonomy', 'Kolhapur'),
    ('tm-4', 'Maya Lin', 'Archaeologist', 'GPR Excavation', 'Mumbai')
    ON CONFLICT (member_id) DO NOTHING;
    """)
    conn.close()
    print("[POSTGRES SETUP] aegis_team_db schema & members initialized")

    # 6. Aegis Analytics DB
    conn = get_conn("aegis_analytics_db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS project_read_models (
        project_id VARCHAR(100) PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        status VARCHAR(50) NOT NULL,
        site_lat DOUBLE PRECISION,
        site_lon DOUBLE PRECISION,
        budget_reserved NUMERIC(12, 2) DEFAULT 0.00,
        allocated_resources TEXT,
        assigned_team TEXT,
        permit_status VARCHAR(50) DEFAULT 'N/A',
        last_event VARCHAR(100),
        updated_at VARCHAR(100)
    );
    CREATE TABLE IF NOT EXISTS processed_events (
        event_id VARCHAR(100) PRIMARY KEY,
        event_type VARCHAR(100) NOT NULL,
        processed_at VARCHAR(100)
    );
    """)
    conn.close()
    print("[POSTGRES SETUP] aegis_analytics_db schema initialized")

    print("\n[POSTGRES SETUP SUCCESS] All 6 microservice databases fully initialized!")


if __name__ == "__main__":
    setup_databases()
