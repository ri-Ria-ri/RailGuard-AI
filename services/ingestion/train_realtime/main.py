import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aiokafka import AIOKafkaProducer

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TRAIN_TOPIC = os.getenv("KAFKA_TRAIN_TOPIC", "railguard.trains")
KAFKA_TRAINS_DLQ_TOPIC = os.getenv("KAFKA_TRAINS_DLQ_TOPIC", "railguard.trains.dlq")

PRODUCER_LINGER_MS = int(os.getenv("PRODUCER_LINGER_MS", "10"))
PRODUCER_BATCH_SIZE = int(os.getenv("PRODUCER_BATCH_SIZE", "65536"))
PRODUCER_COMPRESSION_TYPE = os.getenv("PRODUCER_COMPRESSION_TYPE", "lz4")
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/train_realtime_heartbeat")
EVENT_INTERVAL_SECONDS = float(os.getenv("EVENT_INTERVAL_SECONDS", "2"))

STATIONS_MAP_FILE = os.getenv("STATIONS_MAP_FILE", "/app/configs/stations_to_zones.json")

# Synthetic train data
TRAIN_NUMBERS = [
    "12951", "15083", "19056", "11401", "11402",
    "20904", "22455", "14313", "15627", "20905"
]

TRAIN_NAMES = [
    "Mumbai Rajdhani", "Delhi Shatabdi", "Howrah Express",
    "Punjab Mail", "Tamil Nadu Express", "Karnataka Express",
    "Deccan Queen", "Brindavan Express", "Coromandel Express",
    "Fast Passenger", "Local Express"
]

STATIONS = [
    ("Mumbai CSMT", "MMCT"),
    ("Vadodara", "BRC"),
    ("Kota", "KOA"),
    ("New Delhi", "NDLS"),
    ("Kanpur", "CNB"),
    ("Bhopal", "BPL"),
    ("Nagpur", "NGP"),
]

ROUTES = [
    "Mumbai -> Delhi",
    "Delhi -> Mumbai",
    "Mumbai -> Chennai",
    "Bangalore -> Delhi",
    "Kolkata -> Mumbai",
    "Mumbai -> Goa",
]

DIRECTIONS = ["incoming", "outgoing"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_station_map(path: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    p = Path(path)
    if not p.exists():
        print(f"[WARN] Station map not found at {path}. Trains will not be zone-mapped.", flush=True)
        return {}, {}

    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)

    stop_map = {str(k).strip(): str(v).strip() for k, v in data.get("stop_id", {}).items() if v}
    name_map = {str(k).strip().lower(): str(v).strip() for k, v in data.get("station_name", {}).items() if v}
    print(f"[INFO] Loaded station map: {len(stop_map)} stop_id entries, {len(name_map)} station_name entries", flush=True)
    return stop_map, name_map


def map_zone(stop_id: Optional[str], station_name: Optional[str], stop_map: Dict[str, str], name_map: Dict[str, str]) -> Optional[str]:
    if stop_id:
        key = str(stop_id).strip().upper()
        if key in stop_map:
            return stop_map[key]
    if station_name:
        key = str(station_name).strip().lower()
        if key in name_map:
            return name_map[key]
    return None


def touch_heartbeat() -> None:
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception as e:
        print(f"[WARN] Could not write heartbeat: {e}", flush=True)


async def generate_train_events(
    producer: AIOKafkaProducer,
    stop_map: Dict[str, str],
    name_map: Dict[str, str],
) -> None:
    """Generate synthetic train events and publish to Kafka."""
    sequence = 0
    
    while True:
        try:
            # Pick random stations for current and next
            curr_station_name, curr_stop_id = random.choice(STATIONS)
            next_station_name, next_stop_id = random.choice(STATIONS)
            
            # Map to zones
            curr_zone = map_zone(curr_stop_id, curr_station_name, stop_map, name_map)
            
            # Generate train data
            event = {
                "trainNumber": random.choice(TRAIN_NUMBERS),
                "trainName": random.choice(TRAIN_NAMES),
                "route": random.choice(ROUTES),
                "currentStation": curr_station_name,
                "nextStation": next_station_name,
                "stationId": curr_stop_id,
                "zoneId": curr_zone or "ZONE-A",
                "etaMinutes": random.randint(5, 45),
                "delayMinutes": random.randint(0, 25),
                "direction": random.choice(DIRECTIONS),
                "kavachStatus": random.choice(["ACTIVE", "INACTIVE", "DEGRADED"]),
                "sourceType": "synthetic_generator",
                "timestamp": utc_now_iso(),
            }
            
            # Send to Kafka
            key = event.get("zoneId") or event.get("stationId") or event.get("trainNumber") or "unknown"
            await producer.send_and_wait(
                KAFKA_TRAIN_TOPIC,
                json.dumps(event).encode("utf-8"),
                key=key.encode("utf-8") if isinstance(key, str) else key,
            )
            
            sequence += 1
            if sequence % 10 == 0:
                print(f"[INFO] Generated and published {sequence} train events", flush=True)
            
            touch_heartbeat()
            await asyncio.sleep(EVENT_INTERVAL_SECONDS)
            
        except Exception as e:
            print(f"[ERROR] Error generating train event: {e}", flush=True)
            await asyncio.sleep(1)


async def run():
    print("[INFO] Starting synthetic train data generator...", flush=True)
    
    # Load station mappings
    stop_map, name_map = load_station_map(STATIONS_MAP_FILE)
    
    # Create Kafka producer
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        linger_ms=PRODUCER_LINGER_MS,
        max_batch_size=PRODUCER_BATCH_SIZE,
        compression_type=PRODUCER_COMPRESSION_TYPE,
    )
    await producer.start()
    print("[INFO] Kafka producer started", flush=True)
    
    try:
        await generate_train_events(producer, stop_map, name_map)
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(run())
