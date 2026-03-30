# RailGuard AI

RailGuard AI is a demo project for railway safety monitoring.

It shows how live events move from a simulator to a dashboard in real time.

## What This Project Does

This project currently simulates two kinds of live data:

- Safety alerts (low, medium, high)
- Crowd density by zone

The data flow is:

Simulator -> Kafka -> Backend API -> Database + WebSocket -> Frontend Dashboard

## How The Repo Is Organized

Here is what each main folder is for:

- infra: Docker setup for all services (Kafka, Postgres, backend, frontend, simulator)
- services/backend: FastAPI server (reads Kafka, saves data, serves APIs and WebSockets)
- services/frontend: React dashboard (shows live alerts)
- services/ingestion/simulator: Python event generator (publishes alert and crowd events)
- schemas: JSON schema files for event formats
- docs: architecture and planning documents

## What Is Already Working

- Docker-based startup for the full stack
- Backend health endpoint
- Alerts API and alerts WebSocket
- Crowd latest API
- Simulator publishing alert and crowd events
- Data persistence in PostgreSQL

## Quick Start (Recommended)

If you are new to coding, use Docker. It is the easiest path.

1. Install Docker Desktop.
2. Open a terminal in the project root.
3. Run:

```bash
docker compose -f infra/docker-compose.yml up --build
```

4. Open these in your browser:

- Dashboard: http://localhost:5173
- Backend health: http://localhost:8000/health
- Alerts API: http://localhost:8000/alerts
- Crowd API: http://localhost:8000/crowd/latest

## Important Setup Note (Simple Version)

Use Docker unless you specifically need local Python setup.

Reason: local Python 3.14 can fail to install some packages used by this project.

If you run backend locally, use Python 3.11.

## What Still Needs To Be Built

This repo is a strong base, but it is not the final product yet.

Main things still to build:

- Full UI panels (map, train status board, camera feed panel)
- Train status event pipeline (producer + backend endpoint + UI panel)
- Better crowd visualization in frontend
- Real model inference workers (replace random simulator values)
- Authentication and user roles
- Notifications and alert routing
- Tests (unit and integration)
- Production hardening (monitoring, retries, security)

## Simple Development Goal

Think of this repository as a working foundation.

You can already run and demo live data flow.

Next, build feature panels one by one on top of this base.
