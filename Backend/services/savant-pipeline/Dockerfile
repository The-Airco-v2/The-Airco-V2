FROM ghcr.io/insight-platform/savant-deepstream:latest

# Install additional Python deps
RUN pip install redis minio "onnxruntime-gpu<1.20" insightface "numpy<2" && \
    pip uninstall -y opencv-python opencv-python-headless 2>/dev/null; true

WORKDIR /opt/savant/module

COPY module.yml .
COPY module.local.yml .
COPY custom_pyfunc.py .
COPY redis_sink.py .
COPY face_aligner.py .
COPY event_utils.py .
COPY frame_artifacts.py .
COPY config/ config/

# Bake model artifacts into /models so the image is self-contained
# for GHCR pulls. DeepStream rebuilds TRT engines per-GPU on first
# frame and writes them next to the ONNX sources; on RunPod the
# container filesystem lives on the persistent volume so these
# engines are cached across pod stop/start.
COPY models/ /models/

ENV MODULE_CONFIG=/opt/savant/module/module.yml
CMD ["/opt/savant/module/module.yml"]
