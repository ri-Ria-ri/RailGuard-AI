# RailGuard AI Architecture

## System Overview

RailGuard AI is an event-driven, real-time monitoring stack built around Kafka.

- Data producers:
	- Simulator emits synthetic alerts, crowd, and train events.
	- YOLO service (optional) consumes camera events and emits crowd-enriched events.
- Stream processing:
	- AI worker consumes alerts/crowd/trains and publishes per-zone risk scores.
- Serving layer:
	- Backend consumes multiple Kafka topics, stores recent state in memory, optionally persists AI risk snapshots to PostgreSQL, and streams updates via WebSockets.
- Client layer:
	- Frontend subscribes to backend WebSocket endpoints and polls selected REST endpoints.

## Runtime Components

- Kafka + Zookeeper: event bus and broker coordination.
- Backend (FastAPI): API + WebSocket gateway + Kafka consumers.
- Frontend (Vite/React): live dashboard for alerts, crowd, trains, and AI risk.
- Simulator: synthetic telemetry producer.
- AI worker: deterministic weighted risk scoring service.
- YOLO service (optional): camera-to-crowd enrichment pipeline.
- PostgreSQL: optional persistence for AI risk rows.
- Redis: provisioned cache/service dependency for future extensions.

## Kafka Topics

- railguard.alerts
- railguard.crowd
- railguard.trains
- railguard.cameras
- railguard.crowd.enriched
- railguard.ai.risk

## End-to-End Data Flow

1. Simulator publishes events to railguard.alerts, railguard.crowd, and railguard.trains.
2. Optional YOLO service reads railguard.cameras and emits railguard.crowd.enriched.
3. AI worker consumes alerts + trains + crowd (enriched with fallback to raw crowd) and publishes zone risk events to railguard.ai.risk.
4. Backend consumes alerts, crowd, trains, and ai.risk topics, updates in-memory latest views, and writes AI risk snapshots to PostgreSQL when DB is available.
5. Frontend receives live updates from backend WebSockets:
	 - /ws/alerts
	 - /ws/crowd
	 - /ws/trains
	 - /ws/ai-risk
6. Frontend also reads REST endpoints for current snapshots:
	 - /alerts/latest
	 - /crowd/latest
	 - /trains/latest
	 - /ai/risk/latest
	 - /health

## Architecture Diagram (Mermaid)

```mermaid
flowchart LR
		SIM[Simulator]\nalerts crowd trains --> K[(Kafka)]
		CAM[Camera Stream Producer]\noptional --> K
		YOLO[YOLO Service]\noptional
		AI[AI Worker]\nDeterministic Weighted Model
		BE[Backend API]\nFastAPI + WS + Kafka Consumers
		FE[Frontend Dashboard]\nReact + Vite
		PG[(PostgreSQL)]
		RD[(Redis)]

		K -- railguard.cameras --> YOLO
		YOLO -- railguard.crowd.enriched --> K

		K -- railguard.alerts --> AI
		K -- railguard.crowd.enriched / railguard.crowd --> AI
		K -- railguard.trains --> AI
		AI -- railguard.ai.risk --> K

		K -- alerts crowd trains ai.risk --> BE
		BE -- optional ai_risk persistence --> PG

		FE <-- REST + WebSocket --> BE

		BE -. future cache usage .-> RD
```

## Layered Architecture Diagram

```mermaid
flowchart TB
	subgraph L1[Ingestion Layer]
		SIM2[Simulator]
		CAM2[Camera Stream\nOptional]
		YOLO2[YOLO Service\nOptional]
	end

	subgraph L2[Streaming Layer]
		K2[(Kafka)]
		Z2[(Zookeeper)]
	end

	subgraph L3[Intelligence Layer]
		AI2[AI Worker\nRisk Scoring]
	end

	subgraph L4[Application Layer]
		BE2[Backend\nFastAPI + WebSockets]
	end

	subgraph L5[Storage Layer]
		PG2[(PostgreSQL)]
		RD2[(Redis)]
	end

	subgraph L6[Presentation Layer]
		FE2[Frontend Dashboard]
	end

	SIM2 -->|alerts, crowd, trains| K2
	CAM2 -->|cameras| K2
	K2 -->|cameras| YOLO2
	YOLO2 -->|crowd.enriched| K2

	K2 -->|alerts, crowd, trains| AI2
	AI2 -->|ai.risk| K2

	K2 -->|alerts, crowd, trains, ai.risk| BE2
	BE2 -->|optional ai_risk persistence| PG2
	BE2 -.->|future cache| RD2

	FE2 <-->|REST + WebSocket| BE2
	Z2 --- K2
```

## Sequence Diagram (Live Alert Flow)

```mermaid
sequenceDiagram
	autonumber
	participant SIM as Simulator
	participant K as Kafka
	participant AI as AI Worker
	participant BE as Backend (FastAPI)
	participant PG as PostgreSQL
	participant FE as Frontend

	SIM->>K: Publish alert event (railguard.alerts)
	SIM->>K: Publish crowd event (railguard.crowd)
	SIM->>K: Publish train event (railguard.trains)

	K-->>AI: Deliver alert/crowd/train events
	AI->>AI: Compute zone risk score
	AI->>K: Publish risk event (railguard.ai.risk)

	K-->>BE: Deliver alert/crowd/train/ai.risk
	BE->>BE: Update in-memory latest snapshots
	alt DB available
		BE->>PG: Insert ai_risk row
		PG-->>BE: Ack
	else DB unavailable
		BE->>BE: Continue without persistence
	end

	FE->>BE: WebSocket connect (/ws/alerts,/ws/crowd,/ws/trains,/ws/ai-risk)
	BE-->>FE: Push live events over WebSockets

	FE->>BE: GET /alerts/latest,/crowd/latest,/trains/latest,/ai/risk/latest
	BE-->>FE: Return latest snapshots
```

## Deployment View

- Local/dev orchestration uses Docker Compose in infra/docker-compose.yml.
- External access ports:
	- Frontend: 5173
	- Backend API: 8000
	- Kafka host listener: 9092
	- PostgreSQL: 5432
	- Redis: 6379
