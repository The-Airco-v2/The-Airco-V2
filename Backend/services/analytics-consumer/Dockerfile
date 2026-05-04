FROM python:3.11-slim
WORKDIR /app
COPY services/analytics-consumer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
RUN pip install -e /app/shared
COPY services/analytics-consumer/analytics_consumer /app/analytics_consumer
ENV PYTHONPATH=/app
CMD ["python", "-m", "analytics_consumer.main"]
