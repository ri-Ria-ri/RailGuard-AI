# RailGuard AI Starter Architecture

## First Vertical Slice

- Event simulator produces synthetic alerts.
- Kafka stores and streams events.
- FastAPI consumes events, persists alerts, and broadcasts via WebSocket.
- React dashboard renders live alert queue.

## Data Flow

1. Simulator publishes JSON alert to `railguard.alerts` topic.
2. Backend consumer reads event from Kafka.
3. Backend persists event in PostgreSQL.
4. Backend pushes normalized event to dashboard clients over WebSocket.
5. Dashboard updates alert queue in real time.

## Why This Slice

This validates the most critical integration path before adding CV/ML complexity.
