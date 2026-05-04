FROM ghcr.io/insight-platform/savant-deepstream:latest

# Install additional Python deps
RUN pip install redis minio "onnxruntime-gpu<1.20" insightface "numpy<2" && \
    pip uninstall -y opencv-python opencv-python-headless 2>/dev/null; true

WORKDIR /opt/savant/module

COPY module.yml .
COPY custom_pyfunc.py .
COPY redis_sink.py .
COPY face_aligner.py .
COPY event_utils.py .
COPY frame_artifacts.py .
COPY config/ config/

ENV MODULE_CONFIG=/opt/savant/module/module.yml
CMD ["/opt/savant/module/module.yml"]
