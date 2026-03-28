"""
RailGuard AI — Kafka Topic + Avro Schema Setup
Run via docker-compose kafka-init service, or manually:
  python create-topic.py
"""

import json
import time
import requests
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError

# ── Config ────────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP = "kafka:29092"         # use 'localhost:9092' if running locally
SCHEMA_REGISTRY  = "http://schema-registry:8081"  # adjust host if needed
RETRIES          = 10                   # retry attempts while Kafka boots
RETRY_DELAY      = 5                    # seconds between retries

# ── Topic definitions ─────────────────────────────────────────────────────────
# (name, partitions, replication_factor)
# 3 partitions = parallelism across 3 consumer instances
# replication_factor=1 is fine for local dev; use 3 in production
TOPICS = [
    NewTopic(name="railguard.alerts",  num_partitions=3, replication_factor=1),
    NewTopic(name="railguard.crowd",   num_partitions=3, replication_factor=1),
    NewTopic(name="railguard.trains",  num_partitions=3, replication_factor=1),
    NewTopic(name="railguard.cameras", num_partitions=3, replication_factor=1),
 ]

# ── Avro schemas ──────────────────────────────────────────────────────────────
# Each schema is registered under the subject "<topic-name>-value"
# in Confluent Schema Registry (the standard naming convention).

SCHEMAS = {

    "railguard.cameras-value": {
        "type": "record",
        "name": "CCTVEvent",
        "namespace": "ai.railguard",
        "doc": "Metadata emitted by a CCTV camera on each processed frame",
        "fields": [
            {"name": "camera_id",    "type": "string",  "doc": "Unique camera identifier (IR CCTV standard)"},
            {"name": "station_code", "type": "string",  "doc": "Indian Railways station code e.g. NDLS"},
            {"name": "zone_id",      "type": "string",  "doc": "Railway zone for DPDPA scoped access"},
            {"name": "platform_no",  "type": "int",     "doc": "Platform number, 0 = concourse"},
            {"name": "timestamp_ms", "type": "long",    "doc": "Event time as Unix epoch milliseconds"},
            {"name": "frame_ref",    "type": "string",  "doc": "S3/MinIO object key for the raw frame"},
            {"name": "resolution",   "type": "string",  "doc": "WxH e.g. 1920x1080"},
            {"name": "threat_class", "type": ["null", "string"], "default": None,
             "doc": "YOLOv8 label if a threat was detected, else null"},
            {"name": "confidence",   "type": ["null", "float"],  "default": None,
             "doc": "Detection confidence 0.0–1.0"}
        ]
    },

    "railguard.trains-value": {
        "type": "record",
        "name": "RTISGPSEvent",
        "namespace": "ai.railguard",
        "doc": "Real-time train position from RTIS / Pravah API",
        "fields": [
            {"name": "train_no",     "type": "string", "doc": "Indian Railways train number"},
            {"name": "train_name",   "type": "string"},
            {"name": "zone_id",      "type": "string"},
            {"name": "timestamp_ms", "type": "long"},
            {"name": "latitude",     "type": "double", "doc": "WGS-84 latitude"},
            {"name": "longitude",    "type": "double", "doc": "WGS-84 longitude"},
            {"name": "speed_kmh",    "type": "float"},
            {"name": "delay_mins",   "type": "int",    "doc": "Positive = late, negative = early"},
            {"name": "next_station", "type": "string"},
            {"name": "kavach_active","type": "boolean","doc": "Whether Kavach ATP is engaged"}
        ]
    },

    "railguard.crowd-value": {
        "type": "record",
        "name": "CrowdEvent",
        "namespace": "ai.railguard",
        "doc": "Crowd density event from a station",
        "fields": [
            {"name": "station_code", "type": "string"},
            {"name": "zone_id",      "type": "string"},
            {"name": "platform_no",  "type": "int"},
            {"name": "timestamp_ms", "type": "long"},
            {"name": "crowd_count",  "type": "int"}
        ]
    },

    "railguard.alerts-value": {
        "type": "record",
        "name": "RailGuardAlert",
        "namespace": "ai.railguard",
        "doc": "Processed alert output from any AI/ML model — consumed by dashboard and Celery",
        "fields": [
            {"name": "alert_id",      "type": "string",  "doc": "UUID v4"},
            {"name": "source_topic",  "type": "string",  "doc": "Which topic triggered this alert"},
            {"name": "source_event_id","type": "string", "doc": "camera_id / sensor_id / train_no"},
            {"name": "station_code",  "type": "string"},
            {"name": "zone_id",       "type": "string"},
            {"name": "timestamp_ms",  "type": "long"},
            {"name": "severity",      "type": {
                "type": "enum",
                "name": "Severity",
                "symbols": ["L1_INFO", "L2_WARNING", "L3_CRITICAL"]
            }},
            {"name": "alert_type",    "type": "string",  "doc": "e.g. CROWD_SURGE, TRESPASSER, SMOKE"},
            {"name": "risk_score",    "type": "float",   "doc": "XGBoost risk score 0.0–1.0"},
            {"name": "shap_summary",  "type": "string",  "doc": "JSON-encoded top-5 SHAP features"},
            {"name": "model_version", "type": "string",  "doc": "MLflow model version tag"},
            {"name": "acknowledged",  "type": "boolean", "default": False},
            {"name": "ack_by",        "type": ["null", "string"], "default": None}
        ]
    } 
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def validate_topic_schema_consistency(topics: list[NewTopic], schemas: dict[str, dict]) -> None:
    """Fail fast when schema subject names diverge from configured Kafka topics."""
    topic_names = {topic.name for topic in topics}
    expected_subjects = {f"{topic_name}-value" for topic_name in topic_names}
    actual_subjects = set(schemas.keys())

    missing_subjects = sorted(expected_subjects - actual_subjects)
    extra_subjects = sorted(actual_subjects - expected_subjects)

    if missing_subjects or extra_subjects:
        errors = []
        if missing_subjects:
            errors.append(f"missing schema subjects: {', '.join(missing_subjects)}")
        if extra_subjects:
            errors.append(f"unexpected schema subjects: {', '.join(extra_subjects)}")
        raise ValueError("Topic/schema consistency check failed: " + " | ".join(errors))


def wait_for_kafka(bootstrap: str, retries: int, delay: int) -> KafkaAdminClient:
    """Retry connecting to Kafka until it's ready (it takes a few seconds to boot)."""
    for attempt in range(1, retries + 1):
        try:
            client = KafkaAdminClient(
                bootstrap_servers=bootstrap,
                client_id="railguard-init"
            )
            print(f"✅ Connected to Kafka at {bootstrap}")
            return client
        except Exception as e:
            print(f"⏳ Kafka not ready (attempt {attempt}/{retries}): {e}")
            time.sleep(delay)
    raise RuntimeError(f"Could not connect to Kafka after {retries} attempts.")


def create_topics(admin: KafkaAdminClient, topics: list[NewTopic]) -> None:
    """Create topics, skip gracefully if they already exist."""
    try:
        admin.create_topics(new_topics=topics, validate_only=False)
        for t in topics:
            print(f"✅ Created topic: {t.name}  "
                  f"(partitions={t.num_partitions}, rf={t.replication_factor})")
    except TopicAlreadyExistsError:
        print("ℹ️  Some topics already exist — skipping duplicates.")
    except Exception as e:
        print(f"❌ Error creating topics: {e}")
        raise


def wait_for_schema_registry(url: str, retries: int, delay: int) -> None:
    """Retry until Schema Registry responds."""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(f"{url}/subjects", timeout=5)
            r.raise_for_status()
            print(f"✅ Schema Registry ready at {url}")
            return
        except Exception as e:
            print(f"⏳ Schema Registry not ready (attempt {attempt}/{retries}): {e}")
            time.sleep(delay)
    raise RuntimeError(f"Could not reach Schema Registry after {retries} attempts.")


def register_schema(registry_url: str, subject: str, schema: dict) -> int:
    """Register an Avro schema under <subject> and return the schema ID."""
    payload = {"schema": json.dumps(schema)}
    url = f"{registry_url}/subjects/{subject}/versions"
    r = requests.post(url, json=payload,
                      headers={"Content-Type": "application/vnd.schemaregistry.v1+json"})
    if r.status_code in (200, 201):
        schema_id = r.json()["id"]
        print(f"✅ Registered schema: {subject}  (id={schema_id})")
        return schema_id
    else:
        print(f"❌ Failed to register {subject}: {r.status_code} {r.text}")
        raise RuntimeError(r.text)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n━━━ RailGuard AI — Kafka Init ━━━\n")

    # 0. Ensure topic names and schema subjects stay aligned.
    validate_topic_schema_consistency(TOPICS, SCHEMAS)
    print("✅ Topic/schema consistency check passed")

    # 1. Connect to Kafka and create topics
    admin = wait_for_kafka(KAFKA_BOOTSTRAP, RETRIES, RETRY_DELAY)
    create_topics(admin, TOPICS)
    admin.close()

    print()

    # 2. Register Avro schemas in Schema Registry
    wait_for_schema_registry(SCHEMA_REGISTRY, RETRIES, RETRY_DELAY)
    for subject, schema in SCHEMAS.items():
        register_schema(SCHEMA_REGISTRY, subject, schema)

    print("\n🚀 Kafka setup complete — all 4 topics and schemas are ready.\n")


if __name__ == "__main__":
    main()