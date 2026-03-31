import asyncio
import json
import os
from typing import List, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiokafka import AIOKafkaConsumer
import asyncpg
import tracemalloc   # <-- added for heap profiling

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
ALERT_TOPIC = os.getenv("KAFKA_TOPIC", "railguard.alerts")
CROWD_TOPIC = os.getenv("KAFKA_CROWD_TOPIC", "railguard.crowd")
TRAIN_TOPIC = os.getenv("KAFKA_TRAIN_TOPIC", "railguard.trains")
AI_RISK_TOPIC = os.getenv("KAFKA_AI_RISK_TOPIC", "railguard.ai.risk")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://railguard:railguard@postgres:5432/railguard")

app = FastAPI(title="RailGuard Backend", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory stores
latest_alerts: List[Dict[str, Any]] = []
latest_crowd: Dict[str, Any] = {}
latest_trains: Dict[str, Any] = {}
latest_ai_risk: Dict[str, Any] = {}

class RiskResponse(BaseModel):
    zoneId: str
    riskScore: float
    riskLevel: str
    confidence: float
    topFactors: List[Dict[str, Any]]
    modelName: str
    modelVersion: str
    timestamp: int

# WebSocket managers
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: str):
        stale = []
        for ws in self.active:
            try:
                await ws.send_text(data)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)

alerts_ws = ConnectionManager()
crowd_ws = ConnectionManager()
trains_ws = ConnectionManager()
ai_ws = ConnectionManager()

# DB pool (optional persistence)
db_pool: asyncpg.Pool | None = None

@app.on_event("startup")
async def startup_event():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(dsn=POSTGRES_DSN)
    except Exception as e:
        print("DB pool not started (running without persistence):", e)
    asyncio.create_task(consume_topic(ALERT_TOPIC, handle_alert))
    asyncio.create_task(consume_topic(CROWD_TOPIC, handle_crowd))
    asyncio.create_task(consume_topic(TRAIN_TOPIC, handle_train))
    asyncio.create_task(consume_topic(AI_RISK_TOPIC, handle_ai_risk))

async def consume_topic(topic: str, handler):
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        enable_auto_commit=True,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )
    await consumer.start()
    try:
        async for msg in consumer:
            await handler(msg.value)
    finally:
        await consumer.stop()

# Handlers
async def handle_alert(val: Dict[str, Any]):
    latest_alerts.append(val)
    if len(latest_alerts) > 200:
        latest_alerts.pop(0)
    await alerts_ws.broadcast(json.dumps(val))

async def handle_crowd(val: Dict[str, Any]):
    zone = val.get("zoneId") or "unknown"
    latest_crowd[zone] = val
    await crowd_ws.broadcast(json.dumps(val))

async def handle_train(val: Dict[str, Any]):
    zone = val.get("zoneId") or val.get("stationId") or "unknown"
    latest_trains[zone] = val
    await trains_ws.broadcast(json.dumps(val))

async def handle_ai_risk(val: Dict[str, Any]):
    zone = val.get("zoneId") or "unknown"
    latest_ai_risk[zone] = val
    await ai_ws.broadcast(json.dumps(val))
    if db_pool:
        try:
            await db_pool.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_risk (
                    zone_id text,
                    risk_score float,
                    risk_level text,
                    confidence float,
                    top_factors jsonb,
                    model_name text,
                    model_version text,
                    ts bigint
                );
                """
            )
            await db_pool.execute(
                """
                INSERT INTO ai_risk (zone_id, risk_score, risk_level, confidence, top_factors, model_name, model_version, ts)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                """,
                zone,
                val.get("riskScore"),
                val.get("riskLevel"),
                val.get("confidence"),
                json.dumps(val.get("topFactors", [])),
                val.get("modelName"),
                val.get("modelVersion"),
                val.get("timestamp"),
            )
        except Exception as e:
            print("AI risk DB insert error:", e)

# REST endpoints
@app.get("/ai/risk/latest", response_model=List[RiskResponse])
async def get_ai_risk_latest():
    return list(latest_ai_risk.values())[:200]

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "alerts": len(latest_alerts),
        "crowd_zones": len(latest_crowd),
        "train_zones": len(latest_trains),
        "ai_zones": len(latest_ai_risk),
    }

# NEW: Heap metrics endpoint
tracemalloc.start()

@app.get("/metrics/heap")
async def get_heap():
    snapshot = tracemalloc.take_snapshot()
    stats = snapshot.statistics("lineno")

    points = []
    max_val = 0
    width, height = 800, 600

    for i, stat in enumerate(stats[:50]):
        x = (i * 15) % width
        y = (i * 30) % height
        value = stat.size // 1024  # KB
        max_val = max(max_val, value)
        points.append({"x": x, "y": y, "value": value})

    return {"max": max_val, "data": points}

# WebSockets
@app.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket):
    await alerts_ws.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        alerts_ws.disconnect(ws)

@app.websocket("/ws/crowd")
async def ws_crowd(ws: WebSocket):
    await crowd_ws.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        crowd_ws.disconnect(ws)

@app.websocket("/ws/trains")
async def ws_trains(ws: WebSocket):
    await trains_ws.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        trains_ws.disconnect(ws)

@app.websocket("/ws/ai-risk")
async def ws_ai_risk(ws: WebSocket):
    await ai_ws.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ai_ws.disconnect(ws) 

import tracemalloc
from fastapi import WebSocket, WebSocketDisconnect

# start tracemalloc once at startup
tracemalloc.start()

# WebSocket manager for heap metrics
class HeapConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        stale = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)

heap_ws = HeapConnectionManager()

@app.websocket("/ws/heap")
async def ws_heap(ws: WebSocket):
    await heap_ws.connect(ws)
    try:
        while True:
            # take snapshot every 5 seconds
            snapshot = tracemalloc.take_snapshot()
            stats = snapshot.statistics("lineno")

            points = []
            max_val = 0
            width, height = 800, 600

            for i, stat in enumerate(stats[:50]):
                x = (i * 15) % width
                y = (i * 30) % height
                value = stat.size // 1024  # KB
                max_val = max(max_val, value)
                points.append({"x": x, "y": y, "value": value})

            data = {"max": max_val, "data": points}
            await heap_ws.broadcast(data)

            await ws.receive_text()  # keep connection alive
    except WebSocketDisconnect:
        heap_ws.disconnect(ws)