import asyncio
import hashlib
import json
import os
import random
import time
import uuid
from pathlib import Path
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
SCENARIO_FILE = os.getenv("SCENARIO_FILE", "/app/scenarios/morning_surge.json")

SEVERITIES = ["LOW", "MEDIUM", "HIGH"]
ZONES = ["ZONE-A", "ZONE-B", "ZONE-C"]
SOURCES = ["cctv", "rtis", "iot"]

# Camera configuration
CAMERAS = {
    "ZONE-A": ["CAM-A-001", "CAM-A-002"],
    "ZONE-B": ["CAM-B-001", "CAM-B-002", "CAM-B-003"],
    "ZONE-C": ["CAM-C-001", "CAM-C-002"],
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def crowd_class_from_density(density: int) -> str:
    if density > 80:
        return "CRITICAL"
    if density > 50:
        return "CROWDED"
    return "NORMAL"


class ScenarioEngine:
    """Deterministic scenario timeline for reproducible demo narratives."""

    def __init__(self, scenario_path: str):
        with open(scenario_path, "r", encoding="utf-8") as f:
            self.spec = json.load(f)

        self.scenario_name = self.spec.get("scenarioName", "Deterministic Scenario")
        self.time_of_day = self.spec.get("timeOfDay", "day")
        self.duration_seconds = int(self.spec.get("durationSeconds", 240))
        self.base_crowd_shift = int(self.spec.get("baseCrowdShift", 0))
        self.events = sorted(self.spec.get("events", []), key=lambda e: int(e.get("t", 0)))
        self.base_density_by_zone = self.spec.get("baseDensityByZone", {})

        self.cycle_start = time.time()
        self.cycle_index = 0
        self.next_event_idx = 0

    def _roll_cycle_if_needed(self, now: float) -> int:
        elapsed = int(now - self.cycle_start)
        while elapsed >= self.duration_seconds:
            self.cycle_index += 1
            self.cycle_start += self.duration_seconds
            self.next_event_idx = 0
            elapsed = int(now - self.cycle_start)
        return elapsed

    def due_events(self, now: float) -> tuple[list[dict], int]:
        elapsed = self._roll_cycle_if_needed(now)
        due: list[dict] = []

        while self.next_event_idx < len(self.events):
            event = self.events[self.next_event_idx]
            if int(event.get("t", 0)) > elapsed:
                break
            due.append(event)
            self.next_event_idx += 1

        return due, elapsed

    def pick_variant_message(self, event: dict, default_message: str) -> str:
        variants = event.get("messageVariants", [])
        if not variants:
            return default_message
        idx = (self.cycle_index + int(event.get("t", 0))) % len(variants)
        return variants[idx]

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
        "timestamp": utc_now_iso(),
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
        "timestamp": utc_now_iso(),
    }


def generate_location_crowd_event() -> dict:
    """Generate crowd data for specific locations: entry points, exit points, rest points, stations, platforms."""
    location_types = []
    
    # Add entry points
    for zone in ZONES:
        location_types.append({"locationId": f"{zone}-ENTRY", "type": "entry_point", "zone": zone})
    
    # Add exit points
    for zone in ZONES:
        location_types.append({"locationId": f"{zone}-EXIT", "type": "exit_point", "zone": zone})
    
    # Add rest/middle points (platforms)
    for zone in ZONES:
        for i in range(1, 4):
            location_types.append({"locationId": f"{zone}-PLATFORM-{i}", "type": "platform", "zone": zone})
    
    # Add stations
    stations_list = ["Mumbai CSMT", "Vadodara", "Kota", "New Delhi", "Kanpur", "Bhopal", "Nagpur"]
    for station in stations_list:
        location_types.append({"locationId": station, "type": "station", "zone": None})
    
    location = random.choice(location_types)
    density = random.randint(5, 95)
    person_count = int((density / 100.0) * random.randint(50, 200))
    
    event = {
        "densityPercent": density,
        "personCount": person_count,
        "crowdClass": "CRITICAL" if density > 80 else "CROWDED" if density > 50 else "NORMAL",
        "timestamp": utc_now_iso(),
        "locationType": location["type"],
    }
    
    # Set appropriate identifier based on location type
    if location["type"] == "station":
        event["stationId"] = location["locationId"]
        event["location"] = f"Station: {location['locationId']}"
    elif location["type"] == "platform":
        event["platformId"] = location["locationId"]
        event["zoneId"] = location["zone"]
        event["location"] = f"Platform: {location['locationId']}"
    elif location["type"] == "entry_point":
        event["zoneId"] = location["zone"]
        event["location"] = f"Entry: {location['locationId']}"
    elif location["type"] == "exit_point":
        event["zoneId"] = location["zone"]
        event["location"] = f"Exit: {location['locationId']}"
    
    return event

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
        "timestamp": utc_now_iso(),
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
        "timestamp": utc_now_iso(),
        "resolution": "1920x1080",
    }


def build_scripted_alert(event: dict, scenario: ScenarioEngine) -> dict:
    zone = event.get("zoneId", "ZONE-A")
    severity = event.get("severity", "MEDIUM")
    default_message = event.get("message", "Operational alert")
    message = scenario.pick_variant_message(event, default_message)

    return {
        "id": str(uuid.uuid4()),
        "source": event.get("source", "rtis"),
        "zoneId": zone,
        "severity": severity,
        "category": event.get("category", "TRAIN_OPS"),
        "subType": event.get("subType", "schedule_disruption"),
        "message": message,
        "timestamp": utc_now_iso(),
        "riskScore": float(event.get("riskScore", 0.72)),
        "scenarioName": scenario.scenario_name,
        "timeOfDay": scenario.time_of_day,
        "explanation": {
            "topFactors": event.get(
                "topFactors",
                [
                    {"feature": "crowd_density", "impact": 0.71},
                    {"feature": "train_arrival_eta", "impact": 0.63},
                    {"feature": "camera_health_status", "impact": 0.52},
                ],
            )
        },
    }


def build_scripted_crowd(event: dict, scenario: ScenarioEngine) -> dict:
    zone = event.get("zoneId", "ZONE-A")
    base = int(scenario.base_density_by_zone.get(zone, 30))
    scripted = int(event.get("densityPercent", base))
    density = clamp(scripted + scenario.base_crowd_shift, 0, 99)
    person_count = int(event.get("personCount", max(12, density * 2)))

    return {
        "zoneId": zone,
        "densityPercent": density,
        "personCount": person_count,
        "crowdClass": event.get("crowdClass", crowd_class_from_density(density)),
        "timestamp": utc_now_iso(),
        "scenarioName": scenario.scenario_name,
        "timeOfDay": scenario.time_of_day,
    }


def build_scripted_train(event: dict, scenario: ScenarioEngine) -> dict:
    current_station = event.get("currentStation", "New Delhi")
    next_station = event.get("nextStation", "Kanpur")
    train_number = event.get("trainNumber", "12951")
    train_name = event.get("trainName", "Mumbai Rajdhani")

    return {
        "trainNumber": train_number,
        "trainName": train_name,
        "route": event.get("route", "Mumbai -> Delhi"),
        "currentStation": current_station,
        "nextStation": next_station,
        "zoneId": event.get("zoneId", "ZONE-A"),
        "stationId": current_station,
        "etaMinutes": int(event.get("etaMinutes", 8)),
        "delayMinutes": int(event.get("delayMinutes", 6)),
        "direction": event.get("direction", "incoming"),
        "kavachStatus": event.get("kavachStatus", "ACTIVE"),
        "timestamp": utc_now_iso(),
        "scenarioName": scenario.scenario_name,
        "timeOfDay": scenario.time_of_day,
    }


def apply_camera_control_event(event: dict, scenario: ScenarioEngine) -> None:
    action = event.get("action", "freeze")
    if action != "freeze":
        return

    camera_id = event.get("cameraId")
    zone_id = event.get("zoneId")
    if not camera_id and zone_id in CAMERAS:
        camera_id = CAMERAS[zone_id][0]
    if not camera_id:
        return

    duration_frames = int(event.get("durationFrames", CAMERA_FREEZE_DURATION))
    frame_num = camera_freeze_state.get(camera_id, {}).get("frame_num", 0) + 1
    freeze_token = f"{scenario.scenario_name}|{scenario.cycle_index}|{camera_id}|{event.get('t', 0)}"
    camera_freeze_state[camera_id] = {
        "frame_num": frame_num,
        "frozen_until_frame": frame_num + duration_frames,
        "base_hash": hashlib.md5(freeze_token.encode("utf-8")).hexdigest(),
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
        "receivedAt": utc_now_iso(),
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

    scenario_path = Path(SCENARIO_FILE)
    if not scenario_path.exists():
        raise FileNotFoundError(f"Scenario file not found: {scenario_path}")
    scenario = ScenarioEngine(str(scenario_path))

    await producer.start()
    try:
        print(
            f"[SCENARIO] Loaded '{scenario.scenario_name}' ({scenario.time_of_day}), "
            f"duration={scenario.duration_seconds}s from {scenario_path}",
            flush=True,
        )
        while True:
            now = time.time()
            due_events, elapsed = scenario.due_events(now)

            # Keep watchdog fed with camera metadata every second.
            camera_events = [generate_camera_event() for _ in range(2)]
            send_tasks = [send_with_dlq(producer, "camera", cam_event) for cam_event in camera_events]

            # Generate crowd data for all location types continuously
            location_crowd_events = [generate_location_crowd_event()]
            send_tasks.extend([
                send_with_dlq(producer, "crowd", crowd_event) 
                for crowd_event in location_crowd_events
            ])

            emitted_alerts = 0
            emitted_crowd = len(location_crowd_events)
            emitted_trains = 0
            emitted_controls = 0

            for event in due_events:
                event_type = event.get("type")
                if event_type == "camera_control":
                    apply_camera_control_event(event, scenario)
                    emitted_controls += 1
                    continue
                if event_type == "crowd":
                    send_tasks.append(send_with_dlq(producer, "crowd", build_scripted_crowd(event, scenario)))
                    emitted_crowd += 1
                    continue
                if event_type == "alert":
                    send_tasks.append(send_with_dlq(producer, "alert", build_scripted_alert(event, scenario)))
                    emitted_alerts += 1
                    continue
                if event_type == "train":
                    send_tasks.append(send_with_dlq(producer, "train", build_scripted_train(event, scenario)))
                    emitted_trains += 1
                    continue

            metadata_futures = await asyncio.gather(*send_tasks)
            await asyncio.gather(*metadata_futures)

            touch_heartbeat()

            print(
                f"[SCENARIO] t={elapsed:03d}s cycle={scenario.cycle_index} "
                f"events: alerts={emitted_alerts}, crowd={emitted_crowd}, "
                f"trains={emitted_trains}, camera_controls={emitted_controls}, "
                f"camera_frames={len(camera_events)}, location_crowd={len(location_crowd_events)}",
                flush=True,
            )
            await asyncio.sleep(EVENT_INTERVAL_SECONDS)
    finally:
        await producer.stop()

if __name__ == "__main__":
    asyncio.run(run_producer())