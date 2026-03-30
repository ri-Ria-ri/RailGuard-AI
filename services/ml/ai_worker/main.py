import asyncio
import json
import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
ALERT_TOPIC = os.getenv("ALERT_TOPIC", "railguard.alerts")
CROWD_TOPIC = os.getenv("CROWD_TOPIC", "railguard.crowd.enriched")
TRAIN_TOPIC = os.getenv("TRAIN_TOPIC", "railguard.trains")
AI_RISK_TOPIC = os.getenv("AI_RISK_TOPIC", "railguard.ai.risk")
TICK_SECONDS = float(os.getenv("TICK_SECONDS", "5"))

# Sliding windows (keep last 5 minutes)
WINDOW_SECONDS = 300
MAX_ALERTS = 2000
MAX_TRAINS = 2000

alert_window: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
crowd_state: Dict[str, Dict[str, Any]] = {}
train_state: Dict[str, Dict[str, Any]] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


def severity_to_norm(sev: str) -> float:
    sev = (sev or "").upper()
    if sev == "HIGH":
        return 1.0
    if sev == "MEDIUM":
        return 0.6
    if sev == "LOW":
        return 0.2
    return 0.0


def risk_score(features: Dict[str, float]) -> float:
    score = (
        0.45 * features["crowdNorm"]
        + 0.25 * features["alertNorm"]
        + 0.20 * features["delayNorm"]
        + 0.10 * features["alertRateNorm"]
    )
    return max(0.0, min(1.0, score))


def risk_level(score: float) -> str:
    if score < 0.40:
        return "LOW"
    if score < 0.75:
        return "MEDIUM"
    return "HIGH"


def top_factors(features: Dict[str, float]) -> list:
    contribs = {
        "crowd": 0.45 * features["crowdNorm"],
        "alert": 0.25 * features["alertNorm"],
        "delay": 0.20 * features["delayNorm"],
        "alertRate": 0.10 * features["alertRateNorm"],
    }
    return [
        {"factor": k, "contribution": round(v, 3)}
        for k, v in sorted(contribs.items(), key=lambda kv: kv[1], reverse=True)
    ][:3]


async def run():
    producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP)
    await producer.start()

    consumer = AIOKafkaConsumer(
        ALERT_TOPIC, CROWD_TOPIC, TRAIN_TOPIC,
        bootstrap_servers=BOOTSTRAP,
        enable_auto_commit=True,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )
    await consumer.start()

    try:
        last_tick = time.time()
        async for msg in consumer:
            topic = msg.topic
            payload = msg.value
            now_ts = time.time()

            if topic == ALERT_TOPIC:
                zone = payload.get("zoneId") or payload.get("stationId") or "unknown"
                dq = alert_window[zone]
                dq.append({"ts": now_ts, "severity": payload.get("severity", "LOW")})
                while dq and now_ts - dq[0]["ts"] > WINDOW_SECONDS:
                    dq.popleft()
                if len(dq) > MAX_ALERTS:
                    dq.popleft()

            elif topic == CROWD_TOPIC:
                zone = payload.get("zoneId") or "unknown"
                crowd_state[zone] = payload

            elif topic == TRAIN_TOPIC:
                zone = payload.get("zoneId") or payload.get("stationId") or "unknown"
                train_state[zone] = payload
                if len(train_state) > MAX_TRAINS:
                    train_state.pop(next(iter(train_state)))

            # Periodic tick
            if now_ts - last_tick >= TICK_SECONDS:
                last_tick = now_ts
                await publish_scores(producer)
    finally:
        await consumer.stop()
        await producer.stop()


async def publish_scores(producer: AIOKafkaProducer):
    zones = set(crowd_state.keys()) | set(alert_window.keys()) | set(train_state.keys())
    for zone in zones:
        alerts = alert_window.get(zone, deque())
        crowd = crowd_state.get(zone, {})
        train = train_state.get(zone, {})

        crowd_norm = min(1.0, (crowd.get("densityPercent") or 0) / 100.0)
        alert_norm = severity_to_norm(alerts[-1]["severity"]) if alerts else 0.0
        delay_norm = min(1.0, min(train.get("delayMinutes", 0) or 0, 30) / 30.0)
        alert_rate_norm = min(1.0, len(alerts) / 10.0)

        features = dict(
            crowdNorm=crowd_norm,
            alertNorm=alert_norm,
            delayNorm=delay_norm,
            alertRateNorm=alert_rate_norm,
        )

        score = risk_score(features)
        level = risk_level(score)
        factors = top_factors(features)
        confidence = 0.60 + 0.40 * (
            sum(1 for v in features.values() if v is not None) / 4.0
        )

        event = {
            "zoneId": zone,
            "riskScore": round(score, 3),
            "riskLevel": level,
            "confidence": round(confidence, 3),
            "topFactors": factors,
            "modelName": "deterministic-weighted",
            "modelVersion": "1.0.0",
            "timestamp": now_ms(),
        }
        await producer.send_and_wait(AI_RISK_TOPIC, json.dumps(event).encode("utf-8"))


if __name__ == "__main__":
    asyncio.run(run())