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
KAFKA_CROWD_TOPIC = os.getenv("KAFKA_CROWD_TOPIC", "railguard.crowd")
KAFKA_TRAIN_TOPIC = os.getenv("KAFKA_TRAIN_TOPIC", "railguard.trains")
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
    crowd_clients: set[WebSocket]
    train_clients: set[WebSocket]
    consumer_task: asyncio.Task | None
    crowd_consumer_task: asyncio.Task | None
    train_consumer_task: asyncio.Task | None

    def __init__(self) -> None:
        self.db_pool = None
        self.websocket_clients = set()
        self.crowd_clients = set()
        self.train_clients = set()
        self.consumer_task = None
        self.crowd_consumer_task = None
        self.train_consumer_task = None


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
    alerts_query = """
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
        await conn.execute(alerts_query)
        await conn.execute(crowd_query)
        await conn.execute(train_query)

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


async def broadcast_crowd(event: dict[str, Any]) -> None:
    disconnected: list[WebSocket] = []
    for client in state.crowd_clients:
        try:
            await client.send_json(event)
        except Exception:
            disconnected.append(client)

    for client in disconnected:
        state.crowd_clients.discard(client)


async def save_crowd_density(pool: asyncpg.Pool, event: dict[str, Any]) -> None:
    query = """
    INSERT INTO crowd_density (zone_id, density_percent, timestamp)
    VALUES ($1, $2, $3);
    """
    async with pool.acquire() as conn:
        await conn.execute(
            query,
            event.get("zoneId", "UNKNOWN"),
            int(event.get("densityPercent", 0)),
            normalize_timestamp(event.get("timestamp")),
        )


def normalize_train_event(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "trainNumber": payload.get("trainNumber", "UNKNOWN"),
        "trainName": payload.get("trainName", "Unknown Train"),
        "route": payload.get("route", "Unknown Route"),
        "currentStation": payload.get("currentStation", "UNKNOWN"),
        "nextStation": payload.get("nextStation", "UNKNOWN"),
        "etaMinutes": int(payload.get("etaMinutes", 0)),
        "delayMinutes": int(payload.get("delayMinutes", 0)),
        "kavachStatus": payload.get("kavachStatus", "ACTIVE"),
        "timestamp": normalize_timestamp(payload.get("timestamp")).isoformat(),
    }


async def save_train_status(pool: asyncpg.Pool, event: dict[str, Any]) -> None:
    query = """
    INSERT INTO train_status (
        train_number,
        train_name,
        route,
        current_station,
        next_station,
        eta_minutes,
        delay_minutes,
        kavach_status,
        timestamp
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9);
    """
    async with pool.acquire() as conn:
        await conn.execute(
            query,
            event["trainNumber"],
            event["trainName"],
            event["route"],
            event["currentStation"],
            event["nextStation"],
            event["etaMinutes"],
            event["delayMinutes"],
            event["kavachStatus"],
            normalize_timestamp(event["timestamp"]),
        )


async def broadcast_train(event: dict[str, Any]) -> None:
    disconnected: list[WebSocket] = []
    for client in state.train_clients:
        try:
            await client.send_json(event)
        except Exception:
            disconnected.append(client)

    for client in disconnected:
        state.train_clients.discard(client)


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


async def consume_crowd_loop() -> None:
    while True:
        consumer: AIOKafkaConsumer | None = None
        try:
            consumer = AIOKafkaConsumer(
                KAFKA_CROWD_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                group_id="railguard-crowd-consumer",
                enable_auto_commit=True,
                auto_offset_reset="latest",
            )
            await consumer.start()
            logger.info("Crowd consumer started on topic '%s'", KAFKA_CROWD_TOPIC)

            async for message in consumer:
                event = message.value

                if state.db_pool is not None:
                    await save_crowd_density(state.db_pool, event)

                await broadcast_crowd(event)
        except asyncio.CancelledError:
            logger.info("Crowd consumer task cancelled")
            break
        except Exception as exc:
            logger.exception("Crowd consume loop failed: %s", exc)
            await asyncio.sleep(3)
        finally:
            if consumer is not None:
                await consumer.stop()


async def consume_train_loop() -> None:
    while True:
        consumer: AIOKafkaConsumer | None = None
        try:
            consumer = AIOKafkaConsumer(
                KAFKA_TRAIN_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                group_id="railguard-train-consumer",
                enable_auto_commit=True,
                auto_offset_reset="latest",
            )
            await consumer.start()
            logger.info("Train consumer started on topic '%s'", KAFKA_TRAIN_TOPIC)

            async for message in consumer:
                event = normalize_train_event(message.value)

                if state.db_pool is not None:
                    await save_train_status(state.db_pool, event)

                await broadcast_train(event)
        except asyncio.CancelledError:
            logger.info("Train consumer task cancelled")
            break
        except Exception as exc:
            logger.exception("Train consume loop failed: %s", exc)
            await asyncio.sleep(3)
        finally:
            if consumer is not None:
                await consumer.stop()


@app.on_event("startup")
async def on_startup() -> None:
    state.db_pool = await create_db_pool_with_retry()
    await init_db(state.db_pool)
    state.consumer_task = asyncio.create_task(consume_loop())
    state.crowd_consumer_task = asyncio.create_task(consume_crowd_loop())
    state.train_consumer_task = asyncio.create_task(consume_train_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if state.consumer_task is not None:
        state.consumer_task.cancel()
        with contextlib.suppress(Exception):
            await state.consumer_task

    if state.crowd_consumer_task is not None:
        state.crowd_consumer_task.cancel()
        with contextlib.suppress(Exception):
            await state.crowd_consumer_task

    if state.train_consumer_task is not None:
        state.train_consumer_task.cancel()
        with contextlib.suppress(Exception):
            await state.train_consumer_task

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
            "explanation": row["explanation"] if isinstance(row["explanation"], dict) else json.loads(row["explanation"]),
        }
        for row in rows
    ]
    return alerts


@app.get("/crowd/latest")
async def get_latest_crowd() -> list[dict[str, Any]]:
    query = """
    SELECT DISTINCT ON (zone_id)
        zone_id,
        density_percent,
        timestamp
    FROM crowd_density
    ORDER BY zone_id, timestamp DESC;
    """

    if state.db_pool is None:
        return []

    async with state.db_pool.acquire() as conn:
        rows = await conn.fetch(query)

    return [
        {
            "zoneId": row["zone_id"],
            "densityPercent": row["density_percent"],
            "timestamp": row["timestamp"].isoformat(),
            "status": "NORMAL"
            if row["density_percent"] < 50
            else "CROWDED"
            if row["density_percent"] < 75
            else "CRITICAL",
        }
        for row in rows
    ]


@app.get("/trains/latest")
async def get_latest_trains() -> list[dict[str, Any]]:
    query = """
    SELECT DISTINCT ON (train_number)
        train_number,
        train_name,
        route,
        current_station,
        next_station,
        eta_minutes,
        delay_minutes,
        kavach_status,
        timestamp
    FROM train_status
    ORDER BY train_number, timestamp DESC;
    """

    if state.db_pool is None:
        return []

    async with state.db_pool.acquire() as conn:
        rows = await conn.fetch(query)

    return [
        {
            "trainNumber": row["train_number"],
            "trainName": row["train_name"],
            "route": row["route"],
            "currentStation": row["current_station"],
            "nextStation": row["next_station"],
            "etaMinutes": row["eta_minutes"],
            "delayMinutes": row["delay_minutes"],
            "kavachStatus": row["kavach_status"],
            "timestamp": row["timestamp"].isoformat(),
        }
        for row in rows
    ]


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


@app.websocket("/ws/crowd")
async def websocket_crowd(websocket: WebSocket) -> None:
    await websocket.accept()
    state.crowd_clients.add(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.crowd_clients.discard(websocket)
    except Exception:
        state.crowd_clients.discard(websocket)


@app.websocket("/ws/trains")
async def websocket_trains(websocket: WebSocket) -> None:
    await websocket.accept()
    state.train_clients.add(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.train_clients.discard(websocket)
    except Exception:
        state.train_clients.discard(websocket)
