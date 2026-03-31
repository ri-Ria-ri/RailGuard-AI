import asyncio
import json
import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Any, Optional, Tuple

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
ALERT_TOPIC = os.getenv("ALERT_TOPIC", "railguard.alerts")
CROWD_TOPIC = os.getenv("CROWD_TOPIC", "railguard.crowd.enriched")
CROWD_FALLBACK_TOPIC = os.getenv("CROWD_FALLBACK_TOPIC", "railguard.crowd")
TRAIN_TOPIC = os.getenv("TRAIN_TOPIC", "railguard.trains")
AI_RISK_TOPIC = os.getenv("AI_RISK_TOPIC", "railguard.ai.risk")
ALERT_DLQ_TOPIC = os.getenv("ALERT_DLQ_TOPIC", "railguard.alerts.dlq")
CROWD_DLQ_TOPIC = os.getenv("CROWD_DLQ_TOPIC", "railguard.crowd.dlq")
TRAIN_DLQ_TOPIC = os.getenv("TRAIN_DLQ_TOPIC", "railguard.trains.dlq")
TICK_SECONDS = float(os.getenv("TICK_SECONDS", "5"))
MODEL_WARMUP = os.getenv("MODEL_WARMUP", "true").lower() == "true"
QUEUE_MAXSIZE = int(os.getenv("RISK_QUEUE_MAXSIZE", "256"))
QUEUE_PUT_TIMEOUT_SECONDS = float(os.getenv("QUEUE_PUT_TIMEOUT_SECONDS", "0.05"))
DEDUPE_WINDOW_SECONDS = float(os.getenv("DEDUPE_WINDOW_SECONDS", "2"))
PRODUCER_LINGER_MS = int(os.getenv("PRODUCER_LINGER_MS", "10"))
PRODUCER_BATCH_SIZE = int(os.getenv("PRODUCER_BATCH_SIZE", "65536"))
PRODUCER_COMPRESSION_TYPE = os.getenv("PRODUCER_COMPRESSION_TYPE", "lz4")
CONSUMER_MAX_POLL_RECORDS = int(os.getenv("CONSUMER_MAX_POLL_RECORDS", "500"))
CONSUMER_FETCH_MAX_WAIT_MS = int(os.getenv("CONSUMER_FETCH_MAX_WAIT_MS", "100"))
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/ai_worker_heartbeat")
HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "5"))

# Sliding windows (keep last 5 minutes)
WINDOW_SECONDS = 300
MAX_ALERTS = 2000
MAX_TRAINS = 2000

alert_window: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
crowd_state: Dict[str, Dict[str, Any]] = {}
train_state: Dict[str, Dict[str, Any]] = {}
recent_input_cache: Dict[str, Tuple[str, float]] = {}


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


def warm_model() -> None:
    # Warm the scoring path once so first live inference avoids cold-start jitter.
    warm_features = {
        "crowdNorm": 0.0,
        "alertNorm": 0.0,
        "delayNorm": 0.0,
        "alertRateNorm": 0.0,
    }
    score = risk_score(warm_features)
    _ = risk_level(score)
    _ = top_factors(warm_features)


def zone_for_topic(topic: str, payload: Dict[str, Any]) -> Optional[str]:
    if topic == ALERT_TOPIC:
        return payload.get("zoneId") or payload.get("stationId")
    if topic in (CROWD_TOPIC, CROWD_FALLBACK_TOPIC):
        return payload.get("zoneId") or payload.get("stationId")
    if topic == TRAIN_TOPIC:
        return payload.get("zoneId") or payload.get("stationId")
    return None


def payload_signature(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def should_short_circuit(topic: str, payload: Dict[str, Any], now_ts: float) -> bool:
    zone = zone_for_topic(topic, payload)
    if not zone:
        return False

    key = f"{topic}:{zone}"
    sig = payload_signature(payload)
    cached = recent_input_cache.get(key)
    if cached and cached[0] == sig and (now_ts - cached[1]) <= DEDUPE_WINDOW_SECONDS:
        return True

    recent_input_cache[key] = (sig, now_ts)

    # Keep cache bounded and remove stale entries opportunistically.
    if len(recent_input_cache) > 4000:
        cutoff = now_ts - (DEDUPE_WINDOW_SECONDS * 2)
        stale_keys = [k for k, (_, ts) in recent_input_cache.items() if ts < cutoff]
        for k in stale_keys:
            recent_input_cache.pop(k, None)
    return False


async def enqueue_with_backpressure(
    queue: asyncio.Queue,
    item: Tuple[str, Dict[str, Any], float],
) -> bool:
    try:
        await asyncio.wait_for(queue.put(item), timeout=QUEUE_PUT_TIMEOUT_SECONDS)
        return True
    except asyncio.TimeoutError:
        return False


def dlq_topic_for(topic: str) -> str:
    if topic == ALERT_TOPIC:
        return ALERT_DLQ_TOPIC
    if topic in (CROWD_TOPIC, CROWD_FALLBACK_TOPIC):
        return CROWD_DLQ_TOPIC
    return TRAIN_DLQ_TOPIC


def build_dlq_event(topic: str, payload: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {
        "sourceTopic": topic,
        "reason": reason,
        "timestamp": now_ms(),
        "payload": payload,
    }


def touch_heartbeat() -> None:
    with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


async def heartbeat_loop() -> None:
    while True:
        touch_heartbeat()
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def consumer_loop(
    consumer: AIOKafkaConsumer,
    queue: asyncio.Queue,
    producer: AIOKafkaProducer,
) -> None:
    dropped_due_backpressure = 0
    deduped_inputs = 0
    malformed_inputs = 0

    async for msg in consumer:
        topic = msg.topic
        payload = msg.value
        now_ts = time.time()

        zone = zone_for_topic(topic, payload)
        if not zone:
            malformed_inputs += 1
            await producer.send_and_wait(
                dlq_topic_for(topic),
                json.dumps(build_dlq_event(topic, payload, "missing zoneId/stationId")).encode("utf-8"),
            )
            if malformed_inputs % 50 == 0:
                print(f"ai-worker malformed inputs sent to DLQ: {malformed_inputs}")
            continue

        if should_short_circuit(topic, payload, now_ts):
            deduped_inputs += 1
            if deduped_inputs % 200 == 0:
                print(f"ai-worker deduped inputs: {deduped_inputs}")
            continue

        enqueued = await enqueue_with_backpressure(queue, (topic, payload, now_ts))
        if not enqueued:
            dropped_due_backpressure += 1
            if dropped_due_backpressure % 100 == 0:
                print(f"ai-worker dropped inputs due to backpressure: {dropped_due_backpressure}")


def apply_event(topic: str, payload: Dict[str, Any], now_ts: float) -> None:
    if topic == ALERT_TOPIC:
        zone = payload.get("zoneId") or payload.get("stationId") or "unknown"
        dq = alert_window[zone]
        dq.append({"ts": now_ts, "severity": payload.get("severity", "LOW")})
        while dq and now_ts - dq[0]["ts"] > WINDOW_SECONDS:
            dq.popleft()
        if len(dq) > MAX_ALERTS:
            dq.popleft()

    elif topic in (CROWD_TOPIC, CROWD_FALLBACK_TOPIC):
        zone = payload.get("zoneId") or payload.get("stationId") or "unknown"
        crowd_state[zone] = payload

    elif topic == TRAIN_TOPIC:
        zone = payload.get("zoneId") or payload.get("stationId") or "unknown"
        train_state[zone] = payload
        if len(train_state) > MAX_TRAINS:
            train_state.pop(next(iter(train_state)))


async def processor_loop(queue: asyncio.Queue, producer: AIOKafkaProducer) -> None:
    last_tick = time.time()
    while True:
        topic, payload, event_ts = await queue.get()
        apply_event(topic, payload, event_ts)

        now_ts = time.time()
        if now_ts - last_tick >= TICK_SECONDS:
            last_tick = now_ts
            await publish_scores(producer)


async def run():
    if MODEL_WARMUP:
        warm_model()

    producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        linger_ms=PRODUCER_LINGER_MS,
        max_batch_size=PRODUCER_BATCH_SIZE,
        compression_type=PRODUCER_COMPRESSION_TYPE,
    )
    await producer.start()

    topics = [ALERT_TOPIC, CROWD_TOPIC, TRAIN_TOPIC]
    if CROWD_FALLBACK_TOPIC and CROWD_FALLBACK_TOPIC not in topics:
        topics.append(CROWD_FALLBACK_TOPIC)

    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=BOOTSTRAP,
        enable_auto_commit=True,
        max_poll_records=CONSUMER_MAX_POLL_RECORDS,
        fetch_max_wait_ms=CONSUMER_FETCH_MAX_WAIT_MS,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )
    await consumer.start()

    queue: asyncio.Queue[Tuple[str, Dict[str, Any], float]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    consume_task = asyncio.create_task(consumer_loop(consumer, queue, producer))
    process_task = asyncio.create_task(processor_loop(queue, producer))
    hb_task = asyncio.create_task(heartbeat_loop())

    try:
        await asyncio.gather(consume_task, process_task, hb_task)
    finally:
        for task in (consume_task, process_task, hb_task):
            task.cancel()
        await asyncio.gather(consume_task, process_task, hb_task, return_exceptions=True)
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