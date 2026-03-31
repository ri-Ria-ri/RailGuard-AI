# RailGuard AI
AI-Powered Railway Surveillance for Safety & Security Monitoring

## What It Does
- Streams live safety alerts, crowd density, train status, and camera health events through Kafka to a FastAPI backend and a React dashboard.
- Includes a simulator for alerts/crowd/train/camera events and optional deterministic scenarios.
- Persists data in PostgreSQL and broadcasts live updates via WebSocket.

## Architecture (high level)
Simulator/Ingress → Kafka → FastAPI backend → Postgres + WebSocket → React frontend

## Services
- `services/ingestion/simulator`: Generates alert, crowd, train, and camera events (aiokafka).
- `services/backend`: FastAPI API + Kafka consumers + WebSocket broadcaster + Postgres persistence.
- `services/frontend`: React dashboard for live alerts and metrics.
- `infra`: Docker Compose stack (Kafka, Zookeeper, Postgres, backend, frontend, simulator).
- `schemas`: JSON schemas (alerts; extendable for crowd/train/camera).
- `docs`: Planning notes.

## Getting Started (Docker)
1) Install Docker Desktop.
2) From repo root:
   ```bash
   docker compose -f infra/docker-compose.yml up --build
   ```
3) Open:
   - Dashboard: http://localhost:5173
   - Backend health: http://localhost:8000/health
   - Alerts API: http://localhost:8000/alerts
   - Crowd latest: http://localhost:8000/crowd/latest

## Simulator
- Default: emits random alerts/crowd/train/camera events every `EVENT_INTERVAL_SECONDS`.
- Env vars (partial):
  - `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`)
  - Topics: `KAFKA_TOPIC` (alerts), `KAFKA_CROWD_TOPIC`, `KAFKA_TRAIN_TOPIC`, `KAFKA_CAMERA_TOPIC`
  - Camera fault injection: `CAMERA_FAULT_RATE`, `CAMERA_FREEZE_DURATION`
- To add deterministic scenarios:
  - Create `configs/scenarios/*.json`.
  - Run with `SCENARIO_FILE=configs/scenarios/demo.json python services/ingestion/simulator/main.py`.
- Camera events: publish hashed frames/motion metadata; pair with a watchdog that consumes frames and emits health alerts.

## GTFS-RT Ingestion (optional live data)
- Add a small service `services/ingestion/train_realtime/` to poll TripUpdates/VehiclePositions/Alerts, normalize to the train event shape, and publish to `railguard.trains`.
- Map GTFS `stop_id` → `zoneId` via `configs/stations_meta.json`.
- Keep the existing Kafka schema to avoid backend changes, or add fields (`role`, `label`) and update backend DTO/DB accordingly.

## Backend
- FastAPI service; consumes Kafka topics, stores to Postgres, and streams via WebSocket.
- Health: `/health`
- Alerts: `/alerts`
- Crowd: `/crowd/latest`
- Extend consumers/DTOs if you add new fields (e.g., train roles, camera health).

## Frontend
- React dashboard at port 5173.
- Renders live alerts; can be extended with train boards, crowd heatmaps, and CCTV health badges.
- If adding new alert categories (e.g., `CCTV` health), update alert rendering to color/badge them.

## Data Contracts
- Alert schema lives in `schemas/alert.schema.json` (draft 2020-12). It currently disallows additional properties; if you emit fields like `category`/`subType`, update the schema or relax `additionalProperties`.
- Add schemas for `crowd`, `train`, and `camera` events to keep contracts explicit.

flowchart LR
    subgraph Ingestion
        SIM[Simulator\n(alerts/crowd/train/camera)]
        GTFS[GTFS-RT Ingest\n(trip updates/vehicles/alerts)\n(optional)]
    end

    subgraph Streaming
        KFK[(Kafka Broker)]
        DLQ[(DLQ Topics)]
    end

    subgraph Backend
        API[FastAPI Service\nKafka consumers + REST + WebSocket]
        DB[(PostgreSQL)]
    end

    subgraph Frontend
        UI[React Dashboard\nAlerts | Crowd | Trains | CCTV Health]
    end

    SIM -->|alerts/crowd/train/camera| KFK
    GTFS -->|train live data| KFK
    KFK -->|consume| API
    API --> DB
    API -->|WebSocket + REST| UI
    KFK --> DLQ

## Development Notes
- Python 3.11 recommended locally; some packages may fail on 3.14 (see README note).
- Kafka producers use lz4 compression and DLQ topics (`railguard.*.dlq`).
- Heartbeat file for simulator: `/tmp/simulator_heartbeat`.

## Roadmap / Next Steps
- UI panels: train status board, crowd heatmap, CCTV health overlay.
- Real model inference workers (replace random simulator values).
- Authentication/roles, notifications/alert routing, monitoring/metrics.
- Add schemas for crowd/train/camera, and align casing (`zoneId` vs `zone_id`).
- Production hardening: retries, backpressure, observability.
