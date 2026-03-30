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
EVENT_INTERVAL_SECONDS = float(os.getenv("EVENT_INTERVAL_SECONDS", "1"))

SEVERITIES = ["LOW", "MEDIUM", "HIGH"]
ZONES = ["PF-1", "PF-2", "PF-3", "ENTRY-A", "ENTRY-B"]
SOURCES = ["cctv", "rtis", "iot"]


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

            await producer.send_and_wait(KAFKA_TOPIC, alert_event)
            await producer.send_and_wait(KAFKA_CROWD_TOPIC, crowd_event)

            print(
                f"Published alert {alert_event['id']} ({alert_event['severity']}) zone {alert_event['zoneId']}"
            )
            print(
                f"Published crowd density {crowd_event['densityPercent']}% zone {crowd_event['zoneId']}"
            )
            await asyncio.sleep(EVENT_INTERVAL_SECONDS)
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(run_producer())
