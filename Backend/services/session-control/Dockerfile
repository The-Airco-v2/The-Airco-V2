FROM python:3.11-slim
WORKDIR /app
COPY services/session-control/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
RUN pip install -e /app/shared
COPY services/session-control/session_control /app/session_control
ENV PYTHONPATH=/app
CMD ["python", "-m", "session_control.main"]
