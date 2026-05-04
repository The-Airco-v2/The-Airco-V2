FROM python:3.11-slim
WORKDIR /app
COPY services/ws-publisher/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
RUN pip install -e /app/shared
COPY services/ws-publisher/ws_publisher /app/ws_publisher
ENV PYTHONPATH=/app
CMD ["python", "-m", "ws_publisher.main"]
