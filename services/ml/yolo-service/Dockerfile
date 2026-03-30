FROM ultralytics/ultralytics:latest
WORKDIR /app
COPY main.py .
ENV KAFKA_BOOTSTRAP_SERVERS=kafka:29092
CMD ["python", "main.py"]