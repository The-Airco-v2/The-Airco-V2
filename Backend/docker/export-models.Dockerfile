FROM nvcr.io/nvidia/tensorrt:24.08-py3

WORKDIR /workspace

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libglib2.0-0 \
      libgl1 \
      libsm6 \
      libxext6 \
      libxrender1 \
      libxcb1 && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install \
      ultralytics \
      insightface \
      gdown \
      tensorboard \
      torchreid \
      onnx \
      onnxruntime-gpu \
      numpy \
      pillow

CMD ["bash"]
