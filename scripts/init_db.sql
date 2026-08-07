-- AEGIS-ECO Multi-Service Database Setup Script
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Project Database Schema
CREATE DATABASE aegis_project_db;
\c aegis_project_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE projects (
    project_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'DRAFT',
    location_name VARCHAR(255),
    site_lat DOUBLE PRECISION,
    site_lon DOUBLE PRECISION,
    priority VARCHAR(20) DEFAULT 'MEDIUM',
    budget_requested NUMERIC(12, 2),
    required_equipment TEXT[],
    required_team TEXT[],
    saga_state VARCHAR(50) DEFAULT 'INITIALIZED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE saga_logs (
    saga_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL,
    current_step VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL,
    log_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE outbox (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE processed_events (
    event_id UUID PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Resource Database Schema (With PostGIS)
\c postgres;
CREATE DATABASE aegis_resource_db;
\c aegis_resource_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE resources (
    resource_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    depot_name VARCHAR(255),
    location GEOGRAPHY(Point, 4326),
    battery_pct INT DEFAULT 100,
    current_workload INT DEFAULT 0,
    status VARCHAR(50) DEFAULT 'AVAILABLE',
    hourly_cost NUMERIC(10, 2) DEFAULT 150.00
);

CREATE TABLE resource_reservations (
    reservation_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    resource_id UUID NOT NULL,
    project_id UUID NOT NULL,
    status VARCHAR(50) DEFAULT 'RESERVED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE spatial_constraints (
    constraint_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    constraint_type VARCHAR(100) NOT NULL,
    description TEXT,
    geom GEOGRAPHY(Polygon, 4326)
);

CREATE TABLE outbox (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE processed_events (
    event_id UUID PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed Sample Equipment & Spatial Constraints in Western Ghats & Satara/Pune Region
INSERT INTO resources (name, resource_type, depot_name, location, battery_pct, current_workload, status, hourly_cost) VALUES
('LiDAR Drone Alpha', 'DRONE', 'Pune Depot', ST_MakePoint(73.8567, 18.5204)::geography, 95, 2, 'AVAILABLE', 250.00),
('LiDAR Drone Beta', 'DRONE', 'Satara Depot', ST_MakePoint(74.0183, 17.6805)::geography, 88, 1, 'AVAILABLE', 250.00),
('eDNA Sequencer Unit-1', 'SEQUENCER', 'IISER Pune Lab', ST_MakePoint(73.8152, 18.5529)::geography, 100, 0, 'AVAILABLE', 400.00),
('Ground Radar GPR-7', 'RADAR', 'Kolhapur Station', ST_MakePoint(74.2433, 16.7050)::geography, 75, 4, 'AVAILABLE', 180.00),
('All-Terrain 4x4 Eco Truck', 'VEHICLE', 'Western Ghats Outpost', ST_MakePoint(73.7000, 17.9000)::geography, 90, 1, 'AVAILABLE', 120.00);

-- Insert Spatial Constraint (Protected Forest Polygon near Mahabaleshwar/Satara)
INSERT INTO spatial_constraints (name, constraint_type, description, geom) VALUES
('Koyna Wildlife Sanctuary Restricted Zone', 'PROTECTED_FOREST', 'Strict drone flight prohibition zone', 
 ST_GeogFromText('POLYGON((73.65 17.40, 73.80 17.40, 73.80 17.60, 73.65 17.60, 73.65 17.40))')),
('Western Ghats Airspace No-Fly Polygon', 'NO_FLY_ZONE', 'Defense and high sensitivity airspace polygon',
 ST_GeogFromText('POLYGON((73.40 18.10, 73.60 18.10, 73.60 18.30, 73.40 18.30, 73.40 18.10))'));

-- 3. Funding Database Schema
\c postgres;
CREATE DATABASE aegis_funding_db;
\c aegis_funding_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE grants (
    grant_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    grant_code VARCHAR(50) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    total_budget NUMERIC(14, 2) NOT NULL,
    reserved_budget NUMERIC(14, 2) DEFAULT 0.00,
    spent_budget NUMERIC(14, 2) DEFAULT 0.00
);

CREATE TABLE project_funding (
    allocation_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    grant_id UUID NOT NULL,
    project_id UUID NOT NULL,
    reserved_amount NUMERIC(12, 2) NOT NULL,
    status VARCHAR(50) DEFAULT 'RESERVED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE funding_ledger (
    ledger_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL,
    transaction_type VARCHAR(50) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE outbox (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE processed_events (
    event_id UUID PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO grants (grant_code, title, total_budget, reserved_budget, spent_budget) VALUES
('GRANT-WESTERN-GHATS-2026', 'Western Ghats Biodiversity & Conservation Fund', 5000000.00, 0.00, 0.00),
('GRANT-HERITAGE-ASI-2026', 'Archaeological Survey Heritage Protection Fund', 3500000.00, 0.00, 0.00);

-- 4. Regulatory Gateway Database Schema
\c postgres;
CREATE DATABASE aegis_regulatory_db;
\c aegis_regulatory_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE permits (
    permit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL,
    permit_type VARCHAR(100) NOT NULL,
    issuing_agency VARCHAR(255) NOT NULL,
    status VARCHAR(50) DEFAULT 'PENDING',
    submission_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approval_date TIMESTAMP,
    rejection_reason TEXT
);

CREATE TABLE outbox (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE processed_events (
    event_id UUID PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Team Database Schema
\c postgres;
CREATE DATABASE aegis_team_db;
\c aegis_team_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE team_members (
    member_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    role VARCHAR(100) NOT NULL,
    specialty VARCHAR(100),
    location_base VARCHAR(100),
    availability_status VARCHAR(50) DEFAULT 'AVAILABLE'
);

CREATE TABLE team_assignments (
    assignment_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    member_id UUID NOT NULL,
    project_id UUID NOT NULL,
    role_assigned VARCHAR(100),
    status VARCHAR(50) DEFAULT 'ASSIGNED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE outbox (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE processed_events (
    event_id UUID PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO team_members (name, role, specialty, location_base) VALUES
('Dr. Aris Thorne', 'Lead Ecologist', 'eDNA Sequencing', 'Pune'),
('Elena Rostova', 'Drone Operator', 'LiDAR Topography', 'Satara'),
('Siddharth Mehta', 'Botanist', 'Flora Taxonomy', 'Kolhapur'),
('Maya Lin', 'Archaeologist', 'GPR Excavation', 'Mumbai');

-- 6. Analytics Read Model Database Schema
\c postgres;
CREATE DATABASE aegis_analytics_db;
\c aegis_analytics_db;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE project_read_models (
    project_id UUID PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL,
    site_lat DOUBLE PRECISION,
    site_lon DOUBLE PRECISION,
    budget_reserved NUMERIC(12, 2) DEFAULT 0.00,
    allocated_resources TEXT[],
    assigned_team TEXT[],
    permit_status VARCHAR(50) DEFAULT 'N/A',
    last_event VARCHAR(100),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE processed_events (
    event_id UUID PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
