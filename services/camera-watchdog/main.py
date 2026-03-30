import asyncio
import inspect
import hashlib
import json
import os
import random
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Dict

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_CAMERA_TOPIC = os.getenv("KAFKA_CAMERA_TOPIC", "railguard.cameras")
KAFKA_ALERTS_TOPIC = os.getenv("KAFKA_ALERTS_TOPIC", "railguard.alerts")
KAFKA_CAMERA_DLQ_TOPIC = os.getenv("KAFKA_CAMERA_DLQ_TOPIC", "railguard.cameras.dlq")

PRODUCER_LINGER_MS = int(os.getenv("PRODUCER_LINGER_MS", "10"))
PRODUCER_BATCH_SIZE = int(os.getenv("PRODUCER_BATCH_SIZE", "65536"))
PRODUCER_COMPRESSION_TYPE = os.getenv("PRODUCER_COMPRESSION_TYPE", "lz4")
CONSUMER_MAX_POLL_RECORDS = int(os.getenv("CONSUMER_MAX_POLL_RECORDS", "500"))
CONSUMER_FETCH_MAX_WAIT_MS = int(os.getenv("CONSUMER_FETCH_MAX_WAIT_MS", "100"))

# Watchdog tuning parameters
FRAME_FREEZE_THRESHOLD = int(os.getenv("FRAME_FREEZE_THRESHOLD", "5"))  # 5 consecutive identical hashes
FRAME_STALENESS_SECONDS = int(os.getenv("FRAME_STALENESS_SECONDS", "30"))  # No frame in 30s = offline
MOTION_SENSITIVITY = float(os.getenv("MOTION_SENSITIVITY", "0.7"))  # 0-1 scale for anomaly detection
MOTION_HISTORY_SIZE = int(os.getenv("MOTION_HISTORY_SIZE", "20"))  # Track last 20 motion readings per zone
MOTION_BASELINE_THRESHOLD = float(os.getenv("MOTION_BASELINE_THRESHOLD", "0.15"))  # Std dev for baseline

QUEUE_MAXSIZE = int(os.getenv("QUEUE_MAXSIZE", "256"))
QUEUE_PUT_TIMEOUT_SECONDS = float(os.getenv("QUEUE_PUT_TIMEOUT_SECONDS", "0.05"))

TICK_SECONDS = int(os.getenv("TICK_SECONDS", "5"))
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/camera_watchdog_heartbeat")
HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "5"))

# Station hours (example: closed 23:00-05:00 UTC)
STATION_CLOSED_HOURS = (23, 4)

def is_station_closed() -> bool:
    """Check if station is in closed hours (no expected motion)."""
    now = datetime.now(timezone.utc).hour
    start, end = STATION_CLOSED_HOURS
    if start <= end:
        return start <= now <= end
    else:  # wraps around midnight
        return now >= start or now <= end


class CameraState:
    """Per-camera tracking: frame hash history, timestamps, motion baseline."""

    def __init__(self, camera_id: str, zone_id: str):
        self.camera_id = camera_id
        self.zone_id = zone_id
        self.hash_history: deque = deque(maxlen=10)  # Last 10 frame hashes
        self.last_frame_timestamp = time.time()
        self.motion_history: deque = deque(maxlen=MOTION_HISTORY_SIZE)  # Last motion levels per zone
        self.repeat_count = 0  # Consecutive identical hashes
        self.freeze_alert_sent = False  # Don't spam freeze alerts
        self.offline_alert_sent = False  # Don't spam offline alerts
        self.motion_anomaly_alert_timestamp = 0  # Throttle motion anomaly alerts (send once per 60s)

    def push_frame(self, frame_hash: str, motion_level: float) -> None:
        """Update state with new frame."""
        if self.hash_history and frame_hash == self.hash_history[-1]:
            self.repeat_count += 1
        else:
            self.repeat_count = 1
            self.hash_history.append(frame_hash)

        self.last_frame_timestamp = time.time()
        self.motion_history.append(motion_level)

    def is_frozen(self) -> bool:
        """Frame frozen if last N hashes are identical."""
        return self.repeat_count >= FRAME_FREEZE_THRESHOLD

    def is_offline(self) -> bool:
        """Camera offline if no frame received in N seconds."""
        return time.time() - self.last_frame_timestamp > FRAME_STALENESS_SECONDS

    def get_motion_baseline_std(self) -> float:
        """Compute standard deviation of motion history."""
        if len(self.motion_history) < 3:
            return 0.0
        mean = sum(self.motion_history) / len(self.motion_history)
        variance = sum((x - mean) ** 2 for x in self.motion_history) / len(self.motion_history)
        return variance ** 0.5

    def get_motion_baseline_mean(self) -> float:
        """Compute mean motion level."""
        if not self.motion_history:
            return 0.0
        return sum(self.motion_history) / len(self.motion_history)


def generate_camera_alert(
    alert_type: str, camera_id: str, zone_id: str, severity: str, message: str
) -> dict:
    """Generate an alert for camera health/motion issues."""
    return {
        "id": str(uuid.uuid4()),
        "source": "camera_watchdog",
        "zoneId": zone_id,
        "severity": severity,
        "category": "CAMERA",
        "subType": alert_type,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cameraId": camera_id,
        "riskScore": 0.65 if severity == "HIGH" else 0.45,
        "explanation": {
            "topFactors": [
                {"feature": "camera_health_status", "impact": 0.9},
                {"feature": "motion_anomaly_detection", "impact": 0.8},
            ]
        },
    }


async def send_kafka_message(
    producer: AIOKafkaProducer,
    topic: str,
    payload: dict,
    key: str,
    label: str,
) -> None:
    """Send a Kafka message and await delivery confirmation for this aiokafka version."""
    try:
        send_result = await producer.send(topic, payload, key=key)
        if inspect.isawaitable(send_result):
            metadata = await send_result
        else:
            metadata = send_result

        partition = getattr(metadata, "partition", "?")
        offset = getattr(metadata, "offset", "?")
        print(
            f"[INFO] Sent {label} to {topic} (partition={partition}, offset={offset})",
            flush=True,
        )
    except Exception as e:
        print(
            f"[ERROR] Failed to send {label} to {topic}: {type(e).__name__}: {e}",
            flush=True,
        )


def touch_heartbeat() -> None:
    """Update heartbeat file for Docker health check."""
    with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


async def validate_camera_event(payload: dict) -> tuple[bool, str]:
    """Validate camera event schema."""
    if not payload.get("camera_id"):
        return False, "missing camera_id"
    if not payload.get("zone_id"):
        return False, "missing zone_id"
    if not payload.get("frame_hash"):
        return False, "missing frame_hash"
    if payload.get("motion_level") is None:
        return False, "missing motion_level"
    if not payload.get("timestamp"):
        return False, "missing timestamp"
    return True, ""


async def process_camera_events(
    consumer: AIOKafkaConsumer,
    producer: AIOKafkaProducer,
    event_queue: asyncio.Queue,
) -> None:
    """Consume camera events from Kafka and queue them."""
    message_count = 0
    try:
        print("[INFO] Starting consumer loop...", flush=True)
        sys.stdout.flush()
        async for message in consumer:
            message_count += 1
            try:
                # message.value is already a string because we set value_deserializer
                payload = json.loads(message.value) if isinstance(message.value, str) else json.loads(message.value.decode("utf-8"))
                await asyncio.wait_for(
                    event_queue.put(payload),
                    timeout=QUEUE_PUT_TIMEOUT_SECONDS,
                )
                if message_count % 50 == 0:
                    print(f"[INFO] Processed {message_count} camera frames, queue size: {event_queue.qsize()}", flush=True)
                    sys.stdout.flush()
            except asyncio.TimeoutError:
                print(f"[WARN] Event queue full, dropping camera event", flush=True)
            except json.JSONDecodeError as e:
                print(f"[WARN] Malformed camera event at offset {message.offset}: {e}", flush=True)
            except Exception as e:
                print(f"[ERROR] Error processing camera event: {e}", flush=True)
    except Exception as e:
        print(f"[ERROR] Consumer loop failed: {e}", flush=True)
        import traceback
        traceback.print_exc()


async def process_watchdog_logic(
    producer: AIOKafkaProducer,
    event_queue: asyncio.Queue,
    camera_states: Dict[str, CameraState],
) -> None:
    """Process camera events and emit health/motion alerts."""
    while True:
        try:
            payload = await asyncio.wait_for(event_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        camera_id = payload.get("camera_id", "unknown")
        zone_id = payload.get("zone_id", "unknown")

        # Validate event
        is_valid, reason = await validate_camera_event(payload)
        if not is_valid:
            print(f"[WARN] Invalid camera event: camera_id={camera_id}, reason={reason}")
            dlq_payload = {
                "eventType": "camera",
                "reason": reason,
                "receivedAt": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }
            await send_kafka_message(
                producer,
                KAFKA_CAMERA_DLQ_TOPIC,
                dlq_payload,
                camera_id,
                "camera DLQ payload",
            )
            continue

        # Initialize camera state if new
        if camera_id not in camera_states:
            camera_states[camera_id] = CameraState(camera_id, zone_id)
            print(f"[INFO] Tracking camera {camera_id} in zone {zone_id}")

        state = camera_states[camera_id]
        frame_hash = payload.get("frame_hash", "")
        motion_level = payload.get("motion_level", 0.0)

        # Update state
        state.push_frame(frame_hash, motion_level)

        # ============ FRAME FREEZE DETECTION ============
        if state.is_frozen() and not state.freeze_alert_sent:
            alert = generate_camera_alert(
                "camera_frozen",
                camera_id,
                zone_id,
                "CRITICAL",
                f"Camera {camera_id} frozen (no frame update in {FRAME_FREEZE_THRESHOLD} cycles)",
            )
            await send_kafka_message(
                producer,
                KAFKA_ALERTS_TOPIC,
                alert,
                zone_id,
                "camera_frozen alert",
            )
            state.freeze_alert_sent = True
            print(f"[ALERT] Camera frozen: {camera_id} in zone {zone_id}", flush=True)
            sys.stdout.flush()

        elif not state.is_frozen():
            state.freeze_alert_sent = False  # Reset flag when unfrozen

        # ============ CAMERA OFFLINE DETECTION ============
        if state.is_offline() and not state.offline_alert_sent:
            alert = generate_camera_alert(
                "camera_offline",
                camera_id,
                zone_id,
                "HIGH",
                f"Camera {camera_id} offline (no frame in {FRAME_STALENESS_SECONDS}s)",
            )
            await send_kafka_message(
                producer,
                KAFKA_ALERTS_TOPIC,
                alert,
                zone_id,
                "camera_offline alert",
            )
            state.offline_alert_sent = True
            print(f"[ALERT] Camera offline: {camera_id} in zone {zone_id}", flush=True)
            sys.stdout.flush()

        elif not state.is_offline():
            state.offline_alert_sent = False  # Reset flag when online

        # ============ MOTION ANOMALY DETECTION ============
        now = time.time()

        # Only check if we have enough history
        if len(state.motion_history) >= 5:
            baseline_mean = state.get_motion_baseline_mean()
            baseline_std = state.get_motion_baseline_std()
            current_motion = motion_level

            # Check for unexpected motion during closed hours
            if is_station_closed() and current_motion > MOTION_SENSITIVITY:
                if now - state.motion_anomaly_alert_timestamp > 60:  # Throttle to once per 60s
                    alert = generate_camera_alert(
                        "unexpected_motion_closed_station",
                        camera_id,
                        zone_id,
                        "MEDIUM",
                        f"Unexpected motion detected during closed hours (motion={current_motion:.2f}, camera={camera_id})",
                    )
                    await send_kafka_message(
                        producer,
                        KAFKA_ALERTS_TOPIC,
                        alert,
                        zone_id,
                        "unexpected_motion_closed_station alert",
                    )
                    state.motion_anomaly_alert_timestamp = now
                    print(f"[ALERT] Unexpected motion: {camera_id} (closed station)", flush=True)
                    sys.stdout.flush()

            # Check for motion loss when crowded (possible malfunction)
            elif not is_station_closed() and baseline_mean > 0.5 and current_motion < 0.1:
                if baseline_std > MOTION_BASELINE_THRESHOLD:  # Only if normally has variation
                    if now - state.motion_anomaly_alert_timestamp > 60:  # Throttle
                        alert = generate_camera_alert(
                            "motion_loss_possible_malfunction",
                            camera_id,
                            zone_id,
                            "MEDIUM",
                            f"Motion loss detected during expected activity (baseline={baseline_mean:.2f}, current={current_motion:.2f}, camera={camera_id})",
                        )
                        await send_kafka_message(
                            producer,
                            KAFKA_ALERTS_TOPIC,
                            alert,
                            zone_id,
                            "motion_loss_possible_malfunction alert",
                        )
                        state.motion_anomaly_alert_timestamp = now
                        print(f"[ALERT] Motion loss: {camera_id} (possible malfunction)", flush=True)
                        sys.stdout.flush()


async def run_watchdog() -> None:
    """Main watchdog loop."""
    print("[INFO] Camera watchdog initializing...", flush=True)
    sys.stdout.flush()
    
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda v: str(v).encode("utf-8"),
        linger_ms=PRODUCER_LINGER_MS,
        max_batch_size=PRODUCER_BATCH_SIZE,
        compression_type=PRODUCER_COMPRESSION_TYPE,
    )

    consumer = AIOKafkaConsumer(
        KAFKA_CAMERA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_deserializer=lambda m: m.decode("utf-8"),
        group_id="camera-watchdog",
        auto_offset_reset="earliest",  # Changed from "latest" to catch all messages
        max_poll_records=CONSUMER_MAX_POLL_RECORDS,
        fetch_max_wait_ms=CONSUMER_FETCH_MAX_WAIT_MS,
    )

    # Retry loop with exponential backoff
    max_retries = 10
    retry_count = 0
    retry_delay = 2

    while retry_count < max_retries:
        try:
            print(f"[INFO] Connecting to Kafka (attempt {retry_count + 1}/{max_retries})...", flush=True)
            sys.stdout.flush()
            await asyncio.wait_for(producer.start(), timeout=10)
            await asyncio.wait_for(consumer.start(), timeout=10)
            print("[INFO] Successfully connected to Kafka", flush=True)
            sys.stdout.flush()
            break
        except Exception as e:
            retry_count += 1
            print(f"[WARN] Connection attempt {retry_count}/{max_retries} failed: {e}", flush=True)
            sys.stdout.flush()
            if retry_count < max_retries:
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 10)
            else:
                print(f"[ERROR] Failed to connect after {max_retries} attempts", flush=True)
                raise

    event_queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    camera_states: Dict[str, CameraState] = {}

    try:
        print("[INFO] Camera watchdog starting main loop...", flush=True)
        sys.stdout.flush()
        
        # Send a test message to verify producer is working
        test_alert = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "camera",
            "category": "CAMERA",
            "source": "camera_watchdog",
            "severity": "INFO",
            "camera_id": "TEST-STARTUP",
            "zone_id": "TEST",
            "message": "Camera watchdog started successfully",
            "details": {}
        }
        print("[INFO] Sending startup test message...", flush=True)
        await send_kafka_message(
            producer,
            KAFKA_ALERTS_TOPIC,
            test_alert,
            "TEST",
            "startup test message",
        )
        sys.stdout.flush()

        # Create concurrent tasks
        consumer_task = asyncio.create_task(process_camera_events(consumer, producer, event_queue))
        watchdog_task = asyncio.create_task(process_watchdog_logic(producer, event_queue, camera_states))
        heartbeat_task = asyncio.create_task(heartbeat_loop())

        await asyncio.gather(consumer_task, watchdog_task, heartbeat_task)
    except KeyboardInterrupt:
        print("[INFO] Shutting down...", flush=True)
    finally:
        try:
            await consumer.stop()
        except Exception as e:
            print(f"[WARN] Error stopping consumer: {e}", flush=True)
        try:
            await producer.stop()
        except Exception as e:
            print(f"[WARN] Error stopping producer: {e}", flush=True)


async def heartbeat_loop() -> None:
    """Periodically update heartbeat file for health checks."""
    while True:
        touch_heartbeat()
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def main() -> None:
    await run_watchdog()


if __name__ == "__main__":
    asyncio.run(main())
