import asyncio
import hashlib
import json
import os
import random
import time
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "railguard.alerts")
KAFKA_CROWD_TOPIC = os.getenv("KAFKA_CROWD_TOPIC", "railguard.crowd")
KAFKA_TRAIN_TOPIC = os.getenv("KAFKA_TRAIN_TOPIC", "railguard.trains")
KAFKA_CAMERA_TOPIC = os.getenv("KAFKA_CAMERA_TOPIC", "railguard.cameras")
KAFKA_ALERTS_DLQ_TOPIC = os.getenv("KAFKA_ALERTS_DLQ_TOPIC", "railguard.alerts.dlq")
KAFKA_CROWD_DLQ_TOPIC = os.getenv("KAFKA_CROWD_DLQ_TOPIC", "railguard.crowd.dlq")
KAFKA_TRAINS_DLQ_TOPIC = os.getenv("KAFKA_TRAINS_DLQ_TOPIC", "railguard.trains.dlq")
EVENT_INTERVAL_SECONDS = float(os.getenv("EVENT_INTERVAL_SECONDS", "1"))
PRODUCER_LINGER_MS = int(os.getenv("PRODUCER_LINGER_MS", "10"))
PRODUCER_BATCH_SIZE = int(os.getenv("PRODUCER_BATCH_SIZE", "65536"))
PRODUCER_COMPRESSION_TYPE = os.getenv("PRODUCER_COMPRESSION_TYPE", "lz4")
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/simulator_heartbeat")

SEVERITIES = ["LOW", "MEDIUM", "HIGH"]
ZONES = ["PF-1", "PF-2", "PF-3", "ENTRY-A", "ENTRY-B", "CONCOURSE-1", "CONCOURSE-2"]
SOURCES = ["cctv", "rtis", "iot"]

# Camera configuration
CAMERAS = {
    "PF-1": ["CAM-PF1-001", "CAM-PF1-002"],
    "PF-2": ["CAM-PF2-001"],
    "PF-3": ["CAM-PF3-001", "CAM-PF3-002"],
    "ENTRY-A": ["CAM-ENTRY-A-001"],
    "ENTRY-B": ["CAM-ENTRY-B-001"],
    "CONCOURSE-1": ["CAM-CONC1-001", "CAM-CONC1-002"],
    "CONCOURSE-2": ["CAM-CONC2-001"],
}
CAMERA_FAULT_RATE = float(os.getenv("CAMERA_FAULT_RATE", "0.02"))  # 2% chance of freeze/offline per frame
CAMERA_FREEZE_DURATION = int(os.getenv("CAMERA_FREEZE_DURATION", "20"))  # frames to stay frozen
camera_freeze_state = {}  # { camera_id: { "frozen_until_frame": N, "base_hash": "xxx" } }

TRAINS = [
    {"number": "12951", "name": "Mumbai Rajdhani", "route": "Mumbai -> Delhi"},
    {"number": "12001", "name": "Shatabdi Express", "route": "Delhi -> Chandigarh"},
    {"number": "12627", "name": "Karnataka Express", "route": "Bengaluru -> Delhi"},
    {"number": "12839", "name": "Chennai Mail", "route": "Chennai -> Kolkata"},
]
STATIONS = ["Mumbai CSMT", "Vadodara", "Kota", "New Delhi", "Kanpur", "Bhopal", "Nagpur"]
KAVACH_STATES = ["ACTIVE", "ACTIVE", "ACTIVE", "DEGRADED"]

# Alert taxonomy
CATEGORY_WEIGHTS = [
    ("TRAIN_OPS", 0.30),
    ("PASSENGER", 0.25),
    ("CCTV", 0.20),
    ("ENV", 0.15),
    ("COMMS", 0.10),
]

ALERT_POOLS = {
    "TRAIN_OPS": [
        ("signal_fault", "Signal fault detected", "HIGH"),
        ("speed_violation", "Speed violation detected", "MEDIUM"),
        ("door_fault", "Door fault detected", "MEDIUM"),
    ],
    "PASSENGER": [
        ("passenger_fallen", "Passenger fallen on platform", "HIGH"),
        ("unattended_package", "Unattended package reported", "HIGH"),
        ("help_point_activated", "Emergency help point activated", "MEDIUM"),
    ],
    "CCTV": [
        ("camera_offline", "Camera offline", "MEDIUM"),
        ("restricted_area_intrusion", "Restricted area intrusion", "HIGH"),
    ],
    "ENV": [
        ("smoke_detected", "Smoke detected", "HIGH"),
        ("power_supply_failure", "Power supply failure", "MEDIUM"),
    ],
    "COMMS": [
        ("radio_channel_failure", "Radio channel failure", "MEDIUM"),
        ("control_center_comms_failure", "Control centre comms failure", "HIGH"),
    ],
}

def pick_weighted(weighted_pairs):
    keys, weights = zip(*weighted_pairs)
    return random.choices(keys, weights=weights, k=1)[0]

def generate_alert() -> dict:
    category = pick_weighted(CATEGORY_WEIGHTS)
    subType, message, severity = random.choice(ALERT_POOLS[category])
    risk = round(random.uniform(0.2, 0.98), 3)
    return {
        "id": str(uuid.uuid4()),
        "source": random.choice(SOURCES),
        "zoneId": random.choice(ZONES),  # always set -> no unknowns
        "severity": severity,
        "category": category,
        "subType": subType,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "riskScore": risk,
        "explanation": {
            "topFactors": [
                {"feature": "crowd_density", "impact": round(random.uniform(0.2, 0.9), 3)},
                {"feature": "train_arrival_eta", "impact": round(random.uniform(0.1, 0.6), 3)},
                {"feature": "platform_temperature", "impact": round(random.uniform(0.05, 0.4), 3)},
            ]
        },
    }

def generate_crowd_event() -> dict:
    density = random.randint(5, 95)
    person_count = int((density / 100.0) * random.randint(50, 200))
    return {
        "zoneId": random.choice(ZONES),
        "densityPercent": density,
        "personCount": person_count,
        "crowdClass": "CRITICAL" if density > 80 else "CROWDED" if density > 50 else "NORMAL",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

def generate_train_event() -> dict:
    train = random.choice(TRAINS)
    current_idx = random.randint(0, len(STATIONS) - 2)
    delay = random.randint(0, 25)
    return {
        "trainNumber": train["number"],
        "trainName": train["name"],
        "route": train["route"],
        "currentStation": STATIONS[current_idx],
        "nextStation": STATIONS[current_idx + 1],
        "zoneId": random.choice(ZONES),
        "stationId": STATIONS[current_idx],
        "etaMinutes": random.randint(2, 35),
        "delayMinutes": delay,
        "kavachStatus": random.choice(KAVACH_STATES),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def generate_camera_event() -> dict:
    """Generate synthetic camera frame event with motion detection.
    
    Occasionally injects frame freezes or offline events for watchdog testing.
    """
    zone = random.choice(ZONES)
    camera_id = random.choice(CAMERAS[zone])
    frame_num = camera_freeze_state.get(camera_id, {}).get("frame_num", 0) + 1
    
    # Handle freeze/offline injection (CAMERA_FAULT_RATE chance)
    if random.random() < CAMERA_FAULT_RATE:
        # Start a freeze
        camera_freeze_state[camera_id] = {
            "frame_num": frame_num,
            "frozen_until_frame": frame_num + CAMERA_FREEZE_DURATION,
            "base_hash": hashlib.md5(str(random.random()).encode()).hexdigest(),
        }
    
    # Check if camera is currently frozen
    freeze_info = camera_freeze_state.get(camera_id)
    if freeze_info and frame_num < freeze_info["frozen_until_frame"]:
        # Still frozen: return same hash
        frame_hash = freeze_info["base_hash"]
        motion_level = 0.0
    else:
        # Normal operation: new hash, varying motion
        frame_hash = hashlib.md5(str(time.time() + random.random()).encode()).hexdigest()
        motion_level = random.uniform(0.0, 1.0)
        # Clean up freeze state when complete
        if camera_id in camera_freeze_state:
            del camera_freeze_state[camera_id]
    
    return {
        "camera_id": camera_id,
        "zone_id": zone,
        "frame_hash": frame_hash,
        "motion_level": round(motion_level, 3),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resolution": "1920x1080",
    }


def validate_event(event_type: str, payload: dict) -> tuple[bool, str]:
    if event_type == "camera":
        if not payload.get("camera_id"):
            return False, "missing camera_id"
        if not payload.get("zone_id"):
            return False, "missing zone_id"
        if not payload.get("frame_hash"):
            return False, "missing frame_hash"
        if payload.get("motion_level") is None:
            return False, "missing motion_level"
        return True, ""
    
    zone_id = payload.get("zoneId") or payload.get("stationId")
    if not zone_id:
        return False, "missing zoneId/stationId"
    if event_type == "alert" and not payload.get("severity"):
        return False, "missing severity"
    if event_type == "crowd" and payload.get("densityPercent") is None:
        return False, "missing densityPercent"
    if event_type == "train" and payload.get("delayMinutes") is None:
        return False, "missing delayMinutes"
    return True, ""


def route_for(event_type: str) -> tuple[str, str]:
    if event_type == "alert":
        return KAFKA_TOPIC, KAFKA_ALERTS_DLQ_TOPIC
    if event_type == "crowd":
        return KAFKA_CROWD_TOPIC, KAFKA_CROWD_DLQ_TOPIC
    if event_type == "camera":
        return KAFKA_CAMERA_TOPIC, "railguard.cameras.dlq"
    return KAFKA_TRAIN_TOPIC, KAFKA_TRAINS_DLQ_TOPIC


def build_dlq_payload(event_type: str, payload: dict, reason: str) -> dict:
    return {
        "eventType": event_type,
        "reason": reason,
        "receivedAt": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


def touch_heartbeat() -> None:
    with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


async def send_with_dlq(producer: AIOKafkaProducer, event_type: str, payload: dict):
    topic, dlq_topic = route_for(event_type)
    if event_type == "camera":
        key = payload.get("camera_id", "unknown")
    else:
        key = payload.get("zoneId") or payload.get("stationId") or "unknown"
    is_valid, reason = validate_event(event_type, payload)
    if not is_valid:
        dlq_payload = build_dlq_payload(event_type, payload, reason)
        return await producer.send(dlq_topic, dlq_payload, key=key)
    return await producer.send(topic, payload, key=key)

async def run_producer() -> None:
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda v: str(v).encode("utf-8"),
        linger_ms=PRODUCER_LINGER_MS,
        max_batch_size=PRODUCER_BATCH_SIZE,
        compression_type=PRODUCER_COMPRESSION_TYPE,
    )
    await producer.start()
    try:
        while True:
            alert_event = generate_alert()
            crowd_event = generate_crowd_event()
            train_event = generate_train_event()
            # Generate 2-3 camera events per cycle to simulate multiple cameras
            camera_events = [generate_camera_event() for _ in range(random.randint(2, 3))]

            send_tasks = [
                send_with_dlq(producer, "alert", alert_event),
                send_with_dlq(producer, "crowd", crowd_event),
                send_with_dlq(producer, "train", train_event),
            ] + [send_with_dlq(producer, "camera", cam_event) for cam_event in camera_events]
            
            metadata_futures = await asyncio.gather(*send_tasks)
            await asyncio.gather(*metadata_futures)

            touch_heartbeat()

            print(
                f"Published alert {alert_event['id']} {alert_event['category']}/{alert_event['subType']} "
                f"({alert_event['severity']}) zone {alert_event['zoneId']}"
            )
            print(f"Published crowd density {crowd_event['densityPercent']}% zone {crowd_event['zoneId']}")
            print(
                f"Published train {train_event['trainNumber']} ETA {train_event['etaMinutes']}m "
                f"delay {train_event['delayMinutes']}m"
            )
            print(f"Published {len(camera_events)} camera frames")
            await asyncio.sleep(EVENT_INTERVAL_SECONDS)
    finally:
        await producer.stop()

if __name__ == "__main__":
    asyncio.run(run_producer())