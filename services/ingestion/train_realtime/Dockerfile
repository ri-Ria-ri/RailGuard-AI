FROM python:3.11-slim

WORKDIR /app

COPY services/ingestion/train_realtime/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ingestion/train_realtime/main.py .
COPY configs/stations_to_zones.json ./configs/stations_to_zones.json
COPY configs/train_points_config.json ./configs/train_points_config.json

CMD ["python", "main.py"]
