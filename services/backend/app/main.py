import asyncio
import contextlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("railguard-backend")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "railguard.alerts")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://railguard:railguard@localhost:5432/railguard")

app = FastAPI(title="RailGuard AI Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AppState:
    db_pool: asyncpg.Pool | None = None
    websocket_clients: set[WebSocket]
    consumer_task: asyncio.Task | None

    def __init__(self) -> None:
        self.db_pool = None
        self.websocket_clients = set()
        self.consumer_task = None


state = AppState()


async def create_db_pool_with_retry(max_retries: int = 30, delay_seconds: float = 2.0) -> asyncpg.Pool:
    for attempt in range(1, max_retries + 1):
        try:
            pool = await asyncpg.create_pool(dsn=POSTGRES_DSN, min_size=1, max_size=10)
            logger.info("Connected to PostgreSQL")
            return pool
        except Exception as exc:
            logger.warning("PostgreSQL connection attempt %s/%s failed: %s", attempt, max_retries, exc)
            await asyncio.sleep(delay_seconds)
    raise RuntimeError("Unable to connect to PostgreSQL after retries")


async def init_db(pool: asyncpg.Pool) -> None:
    query = """
    CREATE TABLE IF NOT EXISTS alerts (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        zone_id TEXT NOT NULL,
        severity TEXT NOT NULL,
        event_ts TIMESTAMPTZ NOT NULL,
        risk_score DOUBLE PRECISION NOT NULL,
        explanation JSONB NOT NULL,
        raw_payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """ 
    crowd_query = """
    CREATE TABLE IF NOT EXISTS crowd_density (
        id SERIAL PRIMARY KEY,
        zone_id TEXT NOT NULL,
        density_percent INTEGER NOT NULL,
        timestamp TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_crowd_zone_time 
    ON crowd_density(zone_id, timestamp DESC);
    """
    
    # NEW: Train status table
    train_query = """
    CREATE TABLE IF NOT EXISTS train_status (
        id SERIAL PRIMARY KEY,
        train_number TEXT NOT NULL,
        train_name TEXT NOT NULL,
        route TEXT NOT NULL,
        current_station TEXT,
        next_station TEXT,
        eta_minutes INTEGER,
        delay_minutes INTEGER DEFAULT 0,
        kavach_status TEXT DEFAULT 'ACTIVE',
        timestamp TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_train_number_time 
    ON train_status(train_number, timestamp DESC);
    """
    
    async with pool.acquire() as conn:
        await conn.execute(query)
        await conn.execute(crowd_query)    # NEW
        await conn.execute(train_query)    # NEW
    
    logger.info("Database tables initialized")


def normalize_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def normalize_alert(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload.get("id") or str(uuid.uuid4()),
        "source": payload.get("source", "simulator"),
        "zoneId": payload.get("zoneId", "UNKNOWN"),
        "severity": payload.get("severity", "LOW"),
        "timestamp": normalize_timestamp(payload.get("timestamp")).isoformat(),
        "riskScore": float(payload.get("riskScore", 0.0)),
        "explanation": payload.get("explanation", {}),
    }


async def save_alert(pool: asyncpg.Pool, alert: dict[str, Any], raw_payload: dict[str, Any]) -> None:
    query = """
    INSERT INTO alerts (id, source, zone_id, severity, event_ts, risk_score, explanation, raw_payload)
    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
    ON CONFLICT (id) DO NOTHING;
    """
    async with pool.acquire() as conn:
        await conn.execute(
            query,
            alert["id"],
            alert["source"],
            alert["zoneId"],
            alert["severity"],
            normalize_timestamp(alert["timestamp"]),
            alert["riskScore"],
            json.dumps(alert["explanation"]),
            json.dumps(raw_payload),
        )


async def broadcast_alert(alert: dict[str, Any]) -> None:
    disconnected: list[WebSocket] = []
    for client in state.websocket_clients:
        try:
            await client.send_json(alert)
        except Exception:
            disconnected.append(client)

    for client in disconnected:
        state.websocket_clients.discard(client)


async def consume_loop() -> None:
    while True:
        consumer: AIOKafkaConsumer | None = None
        try:
            consumer = AIOKafkaConsumer(
                KAFKA_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                group_id="railguard-backend-consumer",
                enable_auto_commit=True,
                auto_offset_reset="latest",
            )
            await consumer.start()
            logger.info("Kafka consumer started on topic '%s'", KAFKA_TOPIC)

            async for message in consumer:
                raw_payload = message.value
                alert = normalize_alert(raw_payload)

                if state.db_pool is not None:
                    await save_alert(state.db_pool, alert, raw_payload)

                await broadcast_alert(alert)
        except asyncio.CancelledError:
            logger.info("Kafka consumer task cancelled")
            break
        except Exception as exc:
            logger.exception("Kafka consume loop failed: %s", exc)
            await asyncio.sleep(3)
        finally:
            if consumer is not None:
                await consumer.stop()


@app.on_event("startup")
async def on_startup() -> None:
    state.db_pool = await create_db_pool_with_retry()
    await init_db(state.db_pool)
    state.consumer_task = asyncio.create_task(consume_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if state.consumer_task is not None:
        state.consumer_task.cancel()
        with contextlib.suppress(Exception):
            await state.consumer_task

    if state.db_pool is not None:
        await state.db_pool.close()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/alerts")
async def get_alerts(limit: int = 50) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 200))
    query = """
    SELECT id, source, zone_id, severity, event_ts, risk_score, explanation
    FROM alerts
    ORDER BY event_ts DESC
    LIMIT $1;
    """
    if state.db_pool is None:
        return []

    async with state.db_pool.acquire() as conn:
        rows = await conn.fetch(query, safe_limit)

    alerts = [
        {
            "id": row["id"],
            "source": row["source"],
            "zoneId": row["zone_id"],
            "severity": row["severity"],
            "timestamp": row["event_ts"].isoformat(),
            "riskScore": row["risk_score"],
            "explanation": row["explanation"],
        }
        for row in rows
    ]
    return alerts


@app.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket) -> None:
    await websocket.accept()
    state.websocket_clients.add(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.websocket_clients.discard(websocket)
    except Exception:
        state.websocket_clients.discard(websocket)
