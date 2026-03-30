import asyncio
import json
import os
import random
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "railguard.alerts")
KAFKA_CROWD_TOPIC = os.getenv("KAFKA_CROWD_TOPIC", "railguard.crowd")
KAFKA_TRAIN_TOPIC = os.getenv("KAFKA_TRAIN_TOPIC", "railguard.trains")
EVENT_INTERVAL_SECONDS = float(os.getenv("EVENT_INTERVAL_SECONDS", "1"))

SEVERITIES = ["LOW", "MEDIUM", "HIGH"]
ZONES = ["PF-1", "PF-2", "PF-3", "ENTRY-A", "ENTRY-B"]
SOURCES = ["cctv", "rtis", "iot"]
TRAINS = [
    {"number": "12951", "name": "Mumbai Rajdhani", "route": "Mumbai -> Delhi"},
    {"number": "12001", "name": "Shatabdi Express", "route": "Delhi -> Chandigarh"},
    {"number": "12627", "name": "Karnataka Express", "route": "Bengaluru -> Delhi"},
    {"number": "12839", "name": "Chennai Mail", "route": "Chennai -> Kolkata"},
]
STATIONS = ["Mumbai CSMT", "Vadodara", "Kota", "New Delhi", "Kanpur", "Bhopal", "Nagpur"]
KAVACH_STATES = ["ACTIVE", "ACTIVE", "ACTIVE", "DEGRADED"]


def generate_alert() -> dict:
    severity = random.choices(SEVERITIES, weights=[0.6, 0.3, 0.1], k=1)[0]
    risk = round(random.uniform(0.2, 0.98), 3)

    return {
        "id": str(uuid.uuid4()),
        "source": random.choice(SOURCES),
        "zoneId": random.choice(ZONES),
        "severity": severity,
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
    return {
        "zoneId": random.choice(ZONES),
        "densityPercent": density,
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
        "etaMinutes": random.randint(2, 35),
        "delayMinutes": delay,
        "kavachStatus": random.choice(KAVACH_STATES),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def run_producer() -> None:
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()

    try:
        while True:
            alert_event = generate_alert()
            crowd_event = generate_crowd_event()
            train_event = generate_train_event()

            await producer.send_and_wait(KAFKA_TOPIC, alert_event)
            await producer.send_and_wait(KAFKA_CROWD_TOPIC, crowd_event)
            await producer.send_and_wait(KAFKA_TRAIN_TOPIC, train_event)

            print(
                f"Published alert {alert_event['id']} ({alert_event['severity']}) zone {alert_event['zoneId']}"
            )
            print(
                f"Published crowd density {crowd_event['densityPercent']}% zone {crowd_event['zoneId']}"
            )
            print(
                f"Published train {train_event['trainNumber']} ETA {train_event['etaMinutes']}m delay {train_event['delayMinutes']}m"
            )
            await asyncio.sleep(EVENT_INTERVAL_SECONDS)
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(run_producer())
