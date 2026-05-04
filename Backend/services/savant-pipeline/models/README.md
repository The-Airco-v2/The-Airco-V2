# V2 Savant Detector Model Artifacts

This directory holds detector source artifacts owned by the Savant/DeepStream pipeline.

Current detector assets:

1. `yolov8_person/1/yolov8n.onnx`
2. `yolov8_phone/1/best.onnx`

These artifacts are intentionally separate from `v2/services/triton/models` because detector runtime
compatibility is governed by the TensorRT version bundled with the Savant/DeepStream image, not the
TensorRT version bundled with Triton.
