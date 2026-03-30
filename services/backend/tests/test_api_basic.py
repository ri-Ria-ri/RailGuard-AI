import sys
from pathlib import Path

from fastapi.testclient import TestClient

# Ensure tests can import app.main in both local and container runs.
for parent in Path(__file__).resolve().parents:
    if (parent / "app" / "main.py").exists():
        sys.path.append(str(parent))
        break

from app.main import app


# Keep tests lightweight by avoiding startup connections to DB/Kafka.
app.router.on_startup.clear()
app.router.on_shutdown.clear()

client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_alerts_endpoint_without_db_returns_empty_list() -> None:
    response = client.get("/alerts")
    assert response.status_code == 200
    assert response.json() == []


def test_crowd_endpoint_without_db_returns_empty_list() -> None:
    response = client.get("/crowd/latest")
    assert response.status_code == 200
    assert response.json() == []


def test_trains_endpoint_without_db_returns_empty_list() -> None:
    response = client.get("/trains/latest")
    assert response.status_code == 200
    assert response.json() == []
