```
       _______ _____ ____ _____ ____  _xE
      | ____/ ____/ __ \_   _/ __ \| \ | |  _____
      |  _| | |   | |  | || | | |  | |  \| | / ____|
      | |___| |___| |__| || | | |__| | |\  || |____
      |______\_____\____/ |_|  \____/|_| \_| \_____|
   GEOSPATIAL FIELD OPERATIONS & DISTRIBUTED SAGA ENGINE
```

# 🌿 ECOTONE Atlas

> **Resilient Geospatial Operations, Spatial Resource Intelligence & Distributed Saga Orchestration for High-Risk Ecological Corridors**

[![Python Version](https://img.shields.io/badge/python-3.14+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16.4-336791?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![PostGIS](https://img.shields.io/badge/PostGIS-3.4.3-00599C?style=for-the-badge&logo=leaflet&logoColor=white)](https://postgis.net)
[![Apache Kafka](https://img.shields.io/badge/Apache_Kafka-7.5.0-231F20?style=for-the-badge&logo=apachekafka&logoColor=white)](https://kafka.apache.org)
[![Leaflet.js](https://img.shields.io/badge/Leaflet-1.9.4-199900?style=for-the-badge&logo=leaflet&logoColor=white)](https://leafletjs.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

---

## 🧭 Overview

**ECOTONE** is an enterprise-grade geospatial field operations platform designed to plan, resource, authorize, and observe multi-disciplinary scientific expeditions across sensitive ecological transition zones (starting with the Western Ghats / Sahyadri mountain range in Maharashtra, India).

Unlike standard management tools that fail when field conditions change, ECOTONE uses an **Event-Driven Orchestrated Saga Pattern** coupled with **Transactional Outbox Processing** to guarantee consistency across grant funding, specialized equipment dispatch, field expert allocation, and government environmental permit clearance.

If an operation encounters a late-stage failure—such as a drone flight permit denial over a protected tiger corridor—ECOTONE automatically triggers a **multi-service compensation cascade**, rolling back locked grants, unassigning personnel, and returning equipment to depot availability with zero resource leaks.

---

## ✨ Key Features

- 🗺️ **Geospatial Terrain Intelligence**
  - Composite suitability scoring algorithm evaluating asset distance, transport hours, battery levels, workload, and mission priority.
  - Real-time spatial constraint checking using **PostGIS 3.4.3** (`ST_Intersects`) and bounding-box polygon matching over protected forest reserves and no-fly zones.

- 🔄 **Transactional Saga Orchestrator**
  - 4-stage forward execution (`PROJECT CREATED` → `FUNDS RESERVED` → `ASSET RESERVED` → `TEAM ASSIGNED` → `PERMIT APPROVED` → `ACTIVATED`).
  - Automated 3-service compensation rollback (`COMPENSATING` → parallel release of funds, equipment, and team members → `CANCELLED`).
  - Idempotency deduplication using transactional event claims (`INSERT OR IGNORE INTO processed_events`).

- 📦 **Guaranteed Outbox Delivery Pattern**
  - Domain updates and event payloads are committed atomically in single database transactions (`with db.begin() as tx:`).
  - Background outbox workers poll un-sent entries and publish over **Apache Kafka** or the in-process event bus, providing robust fault recovery across process crashes.

- 📊 **Unified System Console & Observability**
  - **Saga Flow Visualizer:** Interactive 6-stage timeline with terminal state emphasis.
  - **Service Trace:** Service-level execution log with explicit 8-character Operation IDs (`OP_ID: <short_id>`).
  - **Live Event Stream:** Filterable event audit log tracking domain event emissions in real time.

- ⚡ **Dual Engine Architecture**
  - **Local Zero-Config:** Runs out of the box with zero external dependencies using Python's standard library and per-service **SQLite WAL** databases.
  - **Enterprise Scale-Out:** Automatic, transparent upgrade to **PostgreSQL 16.4 + PostGIS** and **Apache Kafka** containers when available.

---

## 🏗️ System Architecture

```
User (Web UI / Leaflet.js)
  │
  │  HTTP REST API (Port 8080)
  ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        AegisServer (server.py)                         │
└───────┬───────────────────┬───────────────────┬───────────────────┬────┘
        │                   │                   │                   │
        ▼                   ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ProjectService │   │FundingService │   │ResourceService│   │ TeamService   │
│(Saga Engine)  │   │(Grant Budgets)│   │(Spatial Scoring) │(Field Experts) │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                   │                   │                   │
        │      ACID Transactions & Transactional Outbox Writes      │
        ▼                   ▼                   ▼                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│      ServiceDatabase Layer (SQLite WAL / PostgreSQL + PostGIS)         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    │ Poll PENDING Outbox Entries
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    OutboxWorker (poll_and_publish)                     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    │ bus.publish() / Kafka
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│             EventBus / Kafka Broker ────► AnalyticsService              │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quickstart

### Prerequisites
- **Python 3.10+** (Python 3.14 recommended)
- **Node.js** (optional, for syntax verification)
- **Docker & Docker Compose** (optional, for PostgreSQL + PostGIS + Kafka deployment)

### 1. Standalone Local Run (Zero External Dependencies)

Clone the repository and start the server immediately using Python's built-in libraries:

```bash
# Clone the repository
git clone https://github.com/your-org/ecotone.git
cd ecotone

# Seed sample field operations
python scripts/seed_operations.py

# Start the ECOTONE server
python server.py 8080
```

Open your browser and navigate to:
👉 **`http://localhost:8080`**

---

### 2. Full Containerized Deployment (PostgreSQL + PostGIS + Kafka)

To launch the real production infrastructure stack via Docker Compose:

```bash
# Start PostgreSQL 16.4 + PostGIS 3.4.3 & Kafka 7.5.0
docker-compose up -d

# Initialize spatial database schemas
python scripts/setup_postgres_db.py

# Run the integration test suite against live containers
python scripts/integration_chaos_tests.py
```

---

## 🧪 Chaos Engineering & Fault Simulation

ECOTONE includes a dedicated fault-injection chaos testing suite designed to prove distributed systems resilience under hostile conditions:

```bash
python scripts/chaos_tests.py
```

### Chaos Scenarios Verified:
| ID | Scenario | Injected Fault | Expected Resilience Behavior |
|:---|:---|:---|:---|
| **T1** | **Outbox Recovery** | Kill service after DB commit, before Kafka publish | Outbox worker recovers `PENDING` entry on restart; saga completes |
| **T2** | **Duplicate Delivery** | Deliver `FundsReserved` twice with same `event_id` | `INSERT OR IGNORE INTO processed_events` prevents double reservation |
| **T3** | **Concurrent Booking** | Two operations request single drone concurrently | Conditional SQL UPDATE (`WHERE status = 'AVAILABLE'`) prevents double booking |
| **T4** | **Orchestrator Crash** | Kill orchestrator mid-compensation cascade | `recover_pending_sagas()` re-dispatches missing rollback commands |
| **T5** | **Broker Downtime** | Reset event bus during active saga | Outbox worker replays un-sent events upon broker reconnection |
| **T6** | **Partial Compensation** | ReleaseFunds fails while ReleaseResources succeeds | Saga remains in `COMPENSATING` status until all compensations acknowledge |

---

## 📡 REST API Reference

| Endpoint | Method | Description |
|:---|:---|:---|
| `GET /api/analytics` | `GET` | Dashboard telemetry, event counters, and read-model metrics |
| `GET /api/projects` | `GET` | List all operations with risk flags, days waiting, and nearest asset distance |
| `GET /api/projects/:id` | `GET` | Detailed operation view with full saga timeline log & step statuses |
| `POST /api/projects/analyze` | `POST` | Pre-deployment spatial scoring & PostGIS restriction checking |
| `POST /api/projects/initiate` | `POST` | Initiate new operation and execute saga orchestrator loop |
| `DELETE /api/projects/:id` | `DELETE` | Cancel operation and release all reserved grant funds, equipment, & personnel |
| `GET /api/map_context` | `GET` | Terrain map overlay layers (depot hubs & restricted polygon zones) |
| `GET /api/resources` | `GET` | Equipment inventory & field specialist roster |
| `GET /api/system/health` | `GET` | System health checks (PostgreSQL, Kafka, Redis, PostGIS) |

---

## 🗺️ Project Structure

```
ecotone/
├── services/                   # Microservice Domain Modules
│   ├── project_service.py      # Saga Orchestrator & Operation Lifecycle
│   ├── funding_service.py      # Grant Budget Allocations & Ledger
│   ├── resource_service.py     # Spatial Scoring & Equipment Reservations
│   ├── team_service.py         # Field Specialist Roster & Assignments
│   ├── regulatory_service.py   # Environmental Permit Gateway Simulation
│   └── analytics_service.py    # Read-Model Event Projector & Telemetry
├── shared/                     # Infrastructure & Core Utilities
│   ├── database.py             # SQLite WAL Manager & Transaction Context
│   ├── pg_database.py          # PostgreSQL + PostGIS Adapter
│   ├── event_bus.py            # In-Process Event Bus with Fault Injection
│   ├── outbox.py               # Transactional Outbox Worker
│   ├── kafka_bus.py            # Kafka Event Bus Wrapper
│   └── config.py               # Configuration & Topic Definitions
├── web/                        # Web SPA Client
│   ├── index.html              # HTML Shell & Navigation Layout
│   ├── styles.css              # Dark Forest Design System Tokens
│   └── app.js                  # Frontend Router, Leaflet Engine & Views
├── scripts/                    # Utilities & Chaos Test Harnesses
│   ├── seed_operations.py      # Operations Data Seeder
│   ├── simulate_saga.py        # Saga Simulation & Integration Harness
│   ├── chaos_tests.py          # In-Process Chaos Test Suite (6 Scenarios)
│   └── integration_chaos_tests.py # Real Container Infrastructure Chaos Suite
├── docker-compose.yml          # Container Stack Manifest
└── server.py                   # HTTP Web Server & REST API Gateway
```

---

## 📜 License

Distributed under the **MIT License**. See `LICENSE` for more information.

---

<p align="center">
  <sub>Built with 🌲 by the ECOTONE Systems Engineering Team. Dedicated to resilient environmental conservation software.</sub>
</p>
