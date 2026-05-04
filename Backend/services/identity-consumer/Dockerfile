FROM python:3.11-slim

WORKDIR /app
COPY services/identity-consumer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
RUN pip install -e /app/shared

COPY services/identity-consumer/identity_consumer/ /app/identity_consumer/
ENV PYTHONPATH=/app

CMD ["python", "-m", "identity_consumer.main"]
