FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/snapshot-consumer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
RUN pip install -e /app/shared
COPY services/snapshot-consumer/snapshot_consumer /app/snapshot_consumer
ENV PYTHONPATH=/app
CMD ["python", "-m", "snapshot_consumer.main"]
