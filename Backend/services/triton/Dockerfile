# Triton inference server with Airco model artifacts baked in.
#
# Models (ONNX sources + any pre-built .plan engines) live at /models
# in the image. Triton rebuilds TRT engines for whatever GPU the pod
# was scheduled on; the resulting .plan files persist in the
# container's writeable overlay, which on RunPod lives on the
# persistent volume across pod stop/start.

FROM nvcr.io/nvidia/tritonserver:24.08-py3

COPY models/ /models/

EXPOSE 8000 8001 8002

CMD ["tritonserver", "--model-repository=/models", "--model-control-mode=explicit", "--load-model=arcface", "--load-model=osnet", "--strict-model-config=false", "--exit-on-error=false"]
