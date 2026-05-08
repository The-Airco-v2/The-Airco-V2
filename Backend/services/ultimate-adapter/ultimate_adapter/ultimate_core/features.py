from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

try:
    import tritonclient.grpc as grpcclient
except Exception:  # pragma: no cover - Triton is only required in GPU/runtime deployments
    grpcclient = None

from airco.config import settings

from .utils import clip_bbox, compute_color_histogram, l2_normalize


class TritonReIDBackend:
    def __init__(self, model_name: str):
        if grpcclient is None:
            raise RuntimeError("tritonclient is required to use Triton-backed ReID")
        self.model_name = model_name
        self.client = grpcclient.InferenceServerClient(url=settings.triton_url)
        metadata = self.client.get_model_metadata(model_name=self.model_name)
        self.input_name = metadata.inputs[0].name if getattr(metadata, "inputs", None) else "input"
        self.output_name = metadata.outputs[0].name if getattr(metadata, "outputs", None) else "output"

    @staticmethod
    def _prepare_batch(crops: List[np.ndarray]) -> np.ndarray:
        batch = []
        for crop in crops:
            resized = cv2.resize(crop, (128, 256), interpolation=cv2.INTER_LINEAR)
            chw = np.transpose(resized.astype(np.float32) / 255.0, (2, 0, 1))
            batch.append(chw)
        return np.stack(batch, axis=0).astype(np.float32)

    def infer_crops(self, crops: List[np.ndarray]) -> Optional[np.ndarray]:
        if not crops:
            return None
        blob = self._prepare_batch(crops)
        inp = grpcclient.InferInput(self.input_name, blob.shape, "FP32")
        inp.set_data_from_numpy(blob)
        out = grpcclient.InferRequestedOutput(self.output_name)
        response = self.client.infer(self.model_name, [inp], outputs=[out])
        embeddings = response.as_numpy(self.output_name)
        if embeddings is None:
            return None
        return np.asarray(embeddings, dtype=np.float32)

    def get_features(self, xyxys: np.ndarray, img: np.ndarray) -> np.ndarray:
        crops: List[np.ndarray] = []
        valid_indices: List[int] = []
        h, w = img.shape[:2]
        for idx, box in enumerate(np.asarray(xyxys)):
            x1, y1, x2, y2 = [int(v) for v in box[:4]]
            x1, y1, x2, y2 = clip_bbox((x1, y1, x2, y2), w, h)
            if x2 - x1 <= 10 or y2 - y1 <= 10:
                continue
            crop = img[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            crops.append(crop)
            valid_indices.append(idx)

        if not crops:
            return np.empty((0, 512), dtype=np.float32)

        embeddings = self.infer_crops(crops)
        if embeddings is None or len(embeddings) == 0:
            return np.empty((0, 512), dtype=np.float32)

        normalized = np.asarray([l2_normalize(emb) for emb in embeddings], dtype=np.float32)
        return normalized


class RobustFeatureExtractor:
    def __init__(self, model_weights: str, device: str, fp16: bool, cfg: dict):
        self.cfg = cfg
        self.device = device
        self.fp16 = fp16
        self.tracker_reid_model = None
        try:
            model_path = str(model_weights or "")
            if model_path and not model_path.lower().endswith((".pt", ".onnx", ".engine", ".torchscript", ".tflite")):
                self.reid_model = TritonReIDBackend(model_path)
                self.tracker_reid_model = self.reid_model
                self.mode = "triton"
            else:
                from boxmot.reid import ReID

                self.reid_model = ReID(model_weights, device=device, half=fp16)
                self.mode = "osnet"
        except Exception:
            self.mode = "fallback"
            self.reid_model = None

    def extract(self, frame: np.ndarray, x1: int, y1: int, x2: int, y2: int, conf: float = 1.0) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = clip_bbox((x1, y1, x2, y2), w, h)
        bw, bh = x2 - x1, y2 - y1
        if bw <= 10 or bh <= 10:
            return None, None
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None, None
        color_feat = compute_color_histogram(frame, x1, y1, x2, y2)
        deep_feat = None
        if self.mode == "triton" and self.reid_model is not None:
            try:
                feats = self.reid_model.get_features(
                    np.array([[0, 0, max(1, bw - 1), max(1, bh - 1)]], dtype=np.float32),
                    crop,
                )
                if feats is not None and len(feats) > 0:
                    deep_feat = l2_normalize(feats[0])
            except Exception:
                pass
        elif self.mode == "osnet" and self.reid_model is not None:
            try:
                det = np.array([[0, 0, max(1, bw - 1), max(1, bh - 1), conf, 0]], dtype=np.float32)
                feats = self.reid_model(crop, det)
                if feats is not None and len(feats) > 0:
                    deep_feat = l2_normalize(feats[0])
            except Exception:
                pass
        if deep_feat is None:
            deep_feat = color_feat
        return deep_feat, color_feat

    def extract_batch(self, frame: np.ndarray, boxes: List[Tuple[int, int, int, int]], confs: Optional[List[float]] = None) -> List[Tuple[Optional[np.ndarray], Optional[np.ndarray]]]:
        if not boxes:
            return []
        confs = confs or [1.0] * len(boxes)
        outputs: List[Tuple[Optional[np.ndarray], Optional[np.ndarray]]] = [(None, None) for _ in boxes]
        for out_idx, ((x1, y1, x2, y2), conf) in enumerate(zip(boxes, confs)):
            try:
                outputs[out_idx] = self.extract(frame, x1, y1, x2, y2, conf=conf)
            except Exception:
                outputs[out_idx] = (None, None)
        return outputs


class MultiScalePyramidExtractor:
    def __init__(self, base_extractor, cfg: dict):
        self.base = base_extractor
        self.scales = cfg.get("pyramid_scales", [1.0, 0.75, 0.5])
        self.enabled = cfg.get("use_multiscale_reid", True)

    def extract(self, frame: np.ndarray, x1: int, y1: int, x2: int, y2: int, conf: float = 1.0) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if not self.enabled or len(self.scales) <= 1:
            return self.base.extract(frame, x1, y1, x2, y2, conf)
        h_orig, w_orig = frame.shape[:2]
        bw = x2 - x1
        bh = y2 - y1
        if bw <= 10 or bh <= 10:
            return None, None
        deep_parts = []
        for scale in self.scales:
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            new_w = max(16, int(bw * scale))
            new_h = max(32, int(bh * scale))
            sx1 = max(0, int(cx - new_w / 2))
            sy1 = max(0, int(cy - new_h / 2))
            sx2 = min(w_orig, sx1 + new_w)
            sy2 = min(h_orig, sy1 + new_h)
            deep, _ = self.base.extract(frame, sx1, sy1, sx2, sy2, conf)
            if deep is not None:
                deep_parts.append(deep)
        if not deep_parts:
            return self.base.extract(frame, x1, y1, x2, y2, conf)
        fused_deep = l2_normalize(np.mean(np.stack(deep_parts), axis=0))
        _, color_feat = self.base.extract(frame, x1, y1, x2, y2, conf)
        return fused_deep, color_feat

    def extract_batch(self, frame: np.ndarray, boxes: List[Tuple[int, int, int, int]], confs: Optional[List[float]] = None) -> List[Tuple[Optional[np.ndarray], Optional[np.ndarray]]]:
        if not boxes:
            return []
        confs = confs or [1.0] * len(boxes)
        outputs: List[Tuple[Optional[np.ndarray], Optional[np.ndarray]]] = [(None, None) for _ in boxes]
        for idx, (bbox, conf) in enumerate(zip(boxes, confs)):
            try:
                x1, y1, x2, y2 = bbox
                outputs[idx] = self.extract(frame, x1, y1, x2, y2, conf)
            except Exception:
                try:
                    outputs[idx] = self.base.extract(frame, *bbox, conf)
                except Exception:
                    outputs[idx] = (None, None)
        return outputs
