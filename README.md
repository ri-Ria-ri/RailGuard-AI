# RailGuard AI 

This scaffold gives you a working first milestone:

Simulator -> Kafka -> FastAPI -> WebSocket -> React Dashboard

## Included Services

- Kafka + Zookeeper
- Redis
- PostgreSQL
- FastAPI backend (`/health`, `/alerts`, `/ws/alerts`)
- Event simulator (publishes synthetic railway alerts)
- React dashboard (live alert queue)

## Quick Start

1. Install Docker Desktop.
2. From the repository root, run:

```bash
docker compose -f infra/docker-compose.yml up --build
```

3. Open:
- Dashboard: http://localhost:5173
- API health: http://localhost:8000/health
- Alerts API: http://localhost:8000/alerts

## First Milestone Checklist

- Synthetic alerts stream every second.
- Dashboard receives live alerts over WebSocket.
- Alerts persist in PostgreSQL and are available after refresh.
- End-to-end latency should be near real-time in local setup.

## Next Steps

- Replace simulator with real camera/GPS/sensor producers.
- Add model inference workers (YOLOv8/CSRNet/XGBoost).
- Add authentication and RBAC (Keycloak).
- Add alert routing (Celery + Redis) and notifications.
