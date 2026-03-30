
import asyncio
import json
import os
import tempfile
from io import BytesIO

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from ultralytics import YOLO
import requests
from PIL import Image

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
CAMERA_TOPIC = os.getenv("KAFKA_CAMERA_TOPIC", "railguard.cameras")
CROWD_TOPIC = os.getenv("KAFKA_CROWD_ENRICHED_TOPIC", "railguard.crowd.enriched")
YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
PERSON_CONF = float(os.getenv("PERSON_CONF_THRESHOLD", "0.45"))

# preload model
model = YOLO(YOLO_MODEL)

async def run():
    producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP)
    await producer.start()

    consumer = AIOKafkaConsumer(
        CAMERA_TOPIC,
        bootstrap_servers=BOOTSTRAP,
        enable_auto_commit=True,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )
    await consumer.start()

    try:
        async for msg in consumer:
            payload = msg.value
            zone_id = payload.get("zoneId") or payload.get("stationId") or "unknown"
            image_url = payload.get("imageUrl")
            if not image_url:
                continue
            try:
                resp = requests.get(image_url, timeout=5)
                resp.raise_for_status()
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
                    img.save(tmp.name)
                    results = model(tmp.name, verbose=False)
                persons = [
                    det for det in results[0].boxes.data.tolist()
                    if int(det[5]) == 0 and det[4] >= PERSON_CONF
                ]
                count = len(persons)
                density_percent = min(100, count * 5)  # heuristic: 20 ppl -> 100%
                enriched = {
                    "zoneId": zone_id,
                    "personCount": count,
                    "densityPercent": density_percent,
                    "crowdClass": (
                        "CRITICAL" if density_percent >= 75 else
                        "CROWDED" if density_percent >= 50 else
                        "NORMAL"
                    ),
                    "timestamp": payload.get("timestamp"),
                    "source": "yolo-headcount",
                }
                await producer.send_and_wait(
                    CROWD_TOPIC, json.dumps(enriched).encode("utf-8")
                )
            except Exception as e:
                print("yolo-service error:", e)
    finally:
        await consumer.stop()
        await producer.stop()

if __name__ == "__main__":
    asyncio.run(run())
