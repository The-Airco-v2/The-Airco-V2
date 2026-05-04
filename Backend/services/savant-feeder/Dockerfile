FROM ghcr.io/insight-platform/savant-deepstream:latest

WORKDIR /app
COPY services/savant-feeder/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/savant-feeder/savant_feeder /app/savant_feeder
ENV PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "savant_feeder.main"]
