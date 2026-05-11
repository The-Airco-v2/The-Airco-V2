FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libxcb1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cu124 \
    torch==2.5.1 \
    torchvision==0.20.1

RUN pip install --no-cache-dir \
    redis \
    numpy \
    opencv-python-headless \
    pillow \
    scipy \
    ultralytics \
    boxmot

COPY shared /app/shared
RUN pip install -e /app/shared

COPY services/ultimate-adapter/ultimate_adapter /app/ultimate_adapter

# Bake Ultimate-Tracker model weights into the image. The CI workflow
# copies the sibling Ultimate-Tracker/ directory to
# services/ultimate-adapter/vendor/ultimate-poc/ before building, so
# the build context (Backend/) sees it at a stable path. Use the
# ULTIMATE_DET_MODEL_PATH / ULTIMATE_REID_MODEL_PATH env vars at
# runtime to point at these.
COPY services/ultimate-adapter/vendor/ultimate-poc/ /ultimate-poc/

ENV PYTHONPATH=/app
ENV ULTIMATE_DET_MODEL_PATH=/ultimate-poc/yolo11s.pt
ENV ULTIMATE_REID_MODEL_PATH=/ultimate-poc/osnet_x1_0_msmt17.pth

CMD ["python", "-m", "ultimate_adapter.main"]
