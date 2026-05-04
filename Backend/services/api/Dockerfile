FROM python:3.11-slim
WORKDIR /app
COPY services/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY migrations /app/migrations
COPY shared /app/shared
RUN pip install -e /app/shared
COPY services/api/api/ /app/api/
ENV PYTHONPATH=/app
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
