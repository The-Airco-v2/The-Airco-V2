# V2 Savant Detector Model Artifacts

This directory holds detector source artifacts owned by the Savant/DeepStream pipeline.

Current detector assets:

1. `yolo26/1/yolo26s.onnx`
2. `phone_detection/1/best.onnx`
3. `yolo26-pose/1/yolo26s-pose.onnx`

These artifacts are intentionally separate from `v2/services/triton/models` because detector runtime
compatibility is governed by the TensorRT version bundled with the Savant/DeepStream image, not the
TensorRT version bundled with Triton.
