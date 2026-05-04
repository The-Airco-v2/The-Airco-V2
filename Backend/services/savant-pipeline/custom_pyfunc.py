"""Savant PyFunc element for post-processing detections.

Extracts face and body crops from tracked persons.
Computes snapshot quality scores.
Prepares data for Redis Streams publishing.

This runs inside the DeepStream pipeline on every frame.
"""

from __future__ import annotations

import base64
import logging

import cv2
import numpy as np
import pyds
from savant.deepstream.pyfunc import NvDsPyFuncPlugin
from savant.deepstream.utils.iterator import (
    nvds_frame_user_meta_iterator,
    nvds_obj_meta_iterator,
    nvds_tensor_output_iterator,
)
from savant.deepstream.utils.surface import get_nvds_buf_surface
from savant.deepstream.utils.tensor import nvds_infer_tensor_meta_to_outputs

from event_utils import is_full_frame_detection, normalize_track_id
from frame_artifacts import store_track_artifacts

logger = logging.getLogger(__name__)


class CropExtractor(NvDsPyFuncPlugin):
    """Extract face/body crops from tracked person detections.

    Attached to frame metadata for downstream Redis sink to publish.
    """

    def __init__(self, face_min_size: int = 20, body_min_size: int = 80,
                 snapshot_interval_frames: int = 30,
                 triton_url: str = "triton:8001",
                 use_scrfd: bool = True):
        super().__init__()
        self.face_min_size = face_min_size
        self.body_min_size = body_min_size
        self.snapshot_interval = snapshot_interval_frames
        self._frame_counter: dict[str, int] = {}
        self._best_scores: dict[str, float] = {}
        self._debug_every = 30
        self.triton_url = triton_url
        self.use_scrfd = str(use_scrfd).lower() in ("true", "1", "yes")
        self._triton_client = None
        self._scrfd_session = None

    def process_frame(self, buffer, frame_meta):
        """Called by Savant for each frame. Annotates metadata with crop data."""
        source_id = frame_meta.source_id if hasattr(frame_meta, 'source_id') else "unknown"
        self._frame_counter[source_id] = self._frame_counter.get(source_id, 0) + 1
        frame_num = self._frame_counter[source_id]

        if frame_num % self._debug_every == 0:
            self._debug_detector_outputs(frame_meta, source_id, frame_num)

        with get_nvds_buf_surface(buffer, frame_meta.frame_meta) as frame_np_rgba:
            frame_np = cv2.cvtColor(frame_np_rgba, cv2.COLOR_RGBA2BGR)
            raw_frame_num = int(getattr(frame_meta, "frame_num", frame_num))

            for obj_meta in self._get_objects(frame_meta):
                if getattr(obj_meta, "is_primary", False):
                    continue
                track_id = normalize_track_id(getattr(obj_meta, "track_id", -1))
                if track_id is None:
                    continue

                bbox = self._get_bbox(obj_meta)
                if bbox is None:
                    continue
                if is_full_frame_detection(bbox, frame_meta):
                    continue

                x1, y1, x2, y2 = bbox
                h, w = y2 - y1, x2 - x1

                # Skip tiny detections
                if h < self.body_min_size or w < self.body_min_size // 2:
                    continue

                # Body crop (full person bbox)
                body_crop = frame_np[int(y1):int(y2), int(x1):int(x2)]
                track_crops: list[dict] = []
                track_snapshot: dict | None = None

                if body_crop.size > 0:
                    body_quality = self._compute_quality(body_crop)
                    body_b64 = self._encode_crop(body_crop, target_size=(128, 256))
                    crop_payload = {
                        "type": "body_crop",
                        "b64": body_b64,
                        "bbox": bbox,
                        "quality": body_quality,
                    }
                    track_crops.append(crop_payload)
                    self._attach_crop(obj_meta, crop_payload)

                # Face detection — use SCRFD if available, fall back to heuristic
                if self.use_scrfd:
                    face_results = self._detect_face_scrfd(body_crop)
                    if face_results is not None:
                        face_crop_aligned, face_bbox_local, face_quality, face_conf, kpts = face_results
                        face_bbox = [
                            x1 + face_bbox_local[0], y1 + face_bbox_local[1],
                            x1 + face_bbox_local[2], y1 + face_bbox_local[3],
                        ]
                        face_b64 = self._encode_crop(face_crop_aligned)
                        crop_payload = {
                            "type": "face_crop",
                            "b64": face_b64,
                            "bbox": face_bbox,
                            "quality": face_quality,
                            "face_confidence": face_conf,
                            "keypoints": kpts.tolist() if kpts is not None else None,
                            "aligned": True,
                        }
                        track_crops.append(crop_payload)
                        self._attach_crop(obj_meta, crop_payload)
                else:
                    # Fallback: upper 40% heuristic
                    face_y2 = y1 + h * 0.4
                    face_crop = frame_np[int(y1):int(face_y2), int(x1):int(x2)]
                    if face_crop.size > 0 and face_crop.shape[0] >= self.face_min_size:
                        face_quality = self._compute_quality(face_crop)
                        face_b64 = self._encode_crop(face_crop, target_size=(112, 112))
                        face_bbox = [x1, y1, x2, face_y2]
                        crop_payload = {
                            "type": "face_crop",
                            "b64": face_b64,
                            "bbox": face_bbox,
                            "quality": face_quality,
                        }
                        track_crops.append(crop_payload)
                        self._attach_crop(obj_meta, crop_payload)

                # Snapshot selection (periodic best-frame)
                if frame_num % self.snapshot_interval == 0:
                    quality = self._compute_quality(body_crop if body_crop.size > 0 else frame_np)
                    track_key = f"{source_id}:{track_id}"
                    if quality > self._best_scores.get(track_key, 0):
                        self._best_scores[track_key] = quality
                        full_b64 = self._encode_crop(frame_np)
                        track_snapshot = {
                            "full_b64": full_b64,
                            "bbox": bbox,
                            "quality": quality,
                        }
                        self._attach_snapshot(obj_meta, track_snapshot)

                if track_crops or track_snapshot:
                    store_track_artifacts(
                        source_id=str(source_id),
                        frame_num=raw_frame_num,
                        track_id=int(track_id),
                        crops=track_crops,
                        snapshot=track_snapshot,
                    )

    def _encode_crop(self, img: np.ndarray, target_size: tuple | None = None) -> str:
        if target_size:
            img = cv2.resize(img, target_size)
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return base64.b64encode(buf.tobytes()).decode()

    def _compute_quality(self, crop: np.ndarray) -> float:
        """Quality score based on size, sharpness (Laplacian variance)."""
        h, w = crop.shape[:2]
        size_score = min(1.0, (h * w) / (200 * 200))
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
        sharp_score = min(1.0, sharpness / 500.0)
        return 0.4 * size_score + 0.6 * sharp_score

    def _ensure_scrfd_session(self):
        """Lazily load SCRFD ONNX model via onnxruntime."""
        if self._scrfd_session is not None:
            return True
        try:
            import onnxruntime as ort
            import os

            model_path = os.environ.get(
                "SCRFD_ONNX_PATH",
                os.path.expanduser("~/.insightface/models/buffalo_l/det_10g.onnx"),
            )
            if not os.path.exists(model_path):
                logger.warning("SCRFD ONNX model not found at %s", model_path)
                return False
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            self._scrfd_session = ort.InferenceSession(model_path, providers=providers)
            logger.info("SCRFD model loaded from %s", model_path)
            return True
        except Exception as e:
            logger.warning("Failed to load SCRFD model: %s", e)
            return False

    @staticmethod
    def _decode_scrfd_outputs(outputs, det_size=640, score_thresh=0.5):
        """Decode raw SCRFD multi-stride outputs into scores, bboxes, keypoints.

        SCRFD outputs 9 tensors: 3 strides x (scores, bboxes, keypoints).
        Bboxes are distance-from-anchor offsets; keypoints are offsets from anchor.
        Must be decoded with anchor grids per stride.
        """
        fmc = 3
        strides = [8, 16, 32]
        num_anchors = 2  # SCRFD det_10g uses 2 anchors per grid position
        scores_list, bboxes_list, kps_list = [], [], []

        for i in range(fmc):
            stride = strides[i]
            cls = outputs[i].flatten()
            bbox_raw = outputs[fmc + i]
            kps_raw = outputs[2 * fmc + i]

            mask = cls > score_thresh
            if not mask.any():
                continue

            # Build anchor grid for this stride (repeated for num_anchors)
            fh = det_size // stride
            fw = det_size // stride
            anchor_y, anchor_x = np.mgrid[:fh, :fw]
            anchors_single = np.stack([anchor_x.ravel(), anchor_y.ravel()], axis=1).astype(np.float32)
            # Repeat each anchor position for num_anchors
            anchors = np.repeat(anchors_single, num_anchors, axis=0)
            # anchors shape: (fh*fw*num_anchors, 2)

            sel_anchors = anchors[mask]  # (N, 2)
            sel_bbox = bbox_raw[mask]    # (N, 4) — left/top/right/bottom distances
            sel_kps = kps_raw[mask]      # (N, 10) — 5 pairs of x/y offsets

            # Decode bboxes: anchor_center ± distance * stride
            cx = (sel_anchors[:, 0] + 0.5) * stride
            cy = (sel_anchors[:, 1] + 0.5) * stride
            x1 = cx - sel_bbox[:, 0] * stride
            y1 = cy - sel_bbox[:, 1] * stride
            x2 = cx + sel_bbox[:, 2] * stride
            y2 = cy + sel_bbox[:, 3] * stride
            decoded_bbox = np.stack([x1, y1, x2, y2], axis=1)

            # Decode keypoints: anchor_center + offset * stride
            decoded_kps = sel_kps.copy()
            for k in range(5):
                decoded_kps[:, 2*k] = cx + sel_kps[:, 2*k] * stride
                decoded_kps[:, 2*k+1] = cy + sel_kps[:, 2*k+1] * stride

            scores_list.append(cls[mask])
            bboxes_list.append(decoded_bbox)
            kps_list.append(decoded_kps)

        if not scores_list:
            return np.array([]), np.empty((0, 4)), np.empty((0, 10))

        return (
            np.concatenate(scores_list),
            np.concatenate(bboxes_list),
            np.concatenate(kps_list),
        )

    def _detect_face_scrfd(self, person_crop: np.ndarray):
        """Run SCRFD face detection on a person crop via ONNX Runtime.
        Returns (aligned_face_112x112, face_bbox, quality, confidence, keypoints) or None.
        """
        from face_aligner import align_face

        if not self._ensure_scrfd_session():
            return None

        h, w = person_crop.shape[:2]
        if h < self.face_min_size or w < self.face_min_size:
            return None

        det_size = 640
        resized = cv2.resize(person_crop, (det_size, det_size))
        inp_np = resized.astype(np.float32).transpose(2, 0, 1)[np.newaxis]

        try:
            input_name = self._scrfd_session.get_inputs()[0].name
            outputs = self._scrfd_session.run(None, {input_name: inp_np})
            scores, bboxes, keypoints = self._decode_scrfd_outputs(outputs, det_size=det_size, score_thresh=0.5)
        except Exception as e:
            logger.warning("SCRFD inference failed: %s", e)
            return None

        if len(scores) == 0:
            return None

        logger.debug("SCRFD found %d faces, best=%.3f", len(scores), scores.max())

        # NMS to suppress overlapping detections across strides
        nms_thresh = 0.4
        order = scores.argsort()[::-1]
        keep = []
        while len(order) > 0:
            i = order[0]
            keep.append(i)
            if len(order) == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(bboxes[i, 0], bboxes[rest, 0])
            yy1 = np.maximum(bboxes[i, 1], bboxes[rest, 1])
            xx2 = np.minimum(bboxes[i, 2], bboxes[rest, 2])
            yy2 = np.minimum(bboxes[i, 3], bboxes[rest, 3])
            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            area_i = (bboxes[i, 2] - bboxes[i, 0]) * (bboxes[i, 3] - bboxes[i, 1])
            area_rest = (bboxes[rest, 2] - bboxes[rest, 0]) * (bboxes[rest, 3] - bboxes[rest, 1])
            iou = inter / (area_i + area_rest - inter + 1e-6)
            order = rest[iou < nms_thresh]
        best_idx = keep[0]

        scale_x, scale_y = w / float(det_size), h / float(det_size)
        bbox = bboxes[best_idx]
        fx1, fy1, fx2, fy2 = bbox[0]*scale_x, bbox[1]*scale_y, bbox[2]*scale_x, bbox[3]*scale_y
        face_bbox = [fx1, fy1, fx2, fy2]

        face_w = fx2 - fx1
        if face_w < self.face_min_size:
            return None

        kps = keypoints[best_idx].reshape(5, 2)
        kps[:, 0] *= scale_x
        kps[:, 1] *= scale_y

        try:
            aligned = align_face(person_crop, kps)
        except Exception as e:
            logger.warning("SCRFD align_face failed: %s", e)
            return None
        if aligned is None:
            return None

        quality = self._compute_quality(aligned)
        return aligned, face_bbox, quality, float(scores[best_idx]), kps

    def _get_objects(self, frame_meta):
        """Extract tracked objects from frame metadata. Adapter for Savant API."""
        if hasattr(frame_meta, 'objects'):
            return frame_meta.objects
        return []

    def _get_bbox(self, obj_meta) -> list[float] | None:
        if hasattr(obj_meta, 'bbox') and obj_meta.bbox is not None:
            left, top, right, bottom = obj_meta.bbox.as_ltrb()
            return [left, top, right, bottom]
        return None

    def _attach_crop(self, obj_meta, crop_payload):
        """Attach crop data to object metadata for downstream sink."""
        if not hasattr(obj_meta, '_airco_crops'):
            obj_meta._airco_crops = []
        obj_meta._airco_crops.append(crop_payload)

    def _attach_snapshot(self, obj_meta, snapshot_payload):
        if not hasattr(obj_meta, '_airco_snapshot'):
            obj_meta._airco_snapshot = snapshot_payload

    def _debug_detector_outputs(self, frame_meta, source_id, frame_num):
        objects = list(self._get_objects(frame_meta))
        non_primary = [
            obj for obj in objects
            if not getattr(obj, "is_primary", False)
        ]
        raw_objects = list(nvds_obj_meta_iterator(frame_meta.frame_meta))

        for raw_obj in raw_objects:
            tensor_metas = list(nvds_tensor_output_iterator(raw_obj, gie_uid=1))
            if not tensor_metas:
                continue
            for tensor_meta in tensor_metas:
                outputs = nvds_infer_tensor_meta_to_outputs(tensor_meta, ["output0"])
                output = outputs[0]
                if output is None:
                    logger.warning(
                        "debug source=%s frame=%s obj_label=%s component=%s class=%s tensor_none objects=%s raw_objects=%s",
                        source_id,
                        frame_num,
                        getattr(raw_obj, "obj_label", ""),
                        getattr(raw_obj, "unique_component_id", ""),
                        getattr(raw_obj, "class_id", ""),
                        len(non_primary),
                        len(raw_objects),
                    )
                    continue
                preds = output.T if output.shape[0] == 84 else output
                if preds.ndim != 2 or preds.shape[1] < 5:
                    logger.warning(
                        "debug source=%s frame=%s obj_label=%s component=%s class=%s unexpected_obj_tensor_shape=%s objects=%s raw_objects=%s",
                        source_id,
                        frame_num,
                        getattr(raw_obj, "obj_label", ""),
                        getattr(raw_obj, "unique_component_id", ""),
                        getattr(raw_obj, "class_id", ""),
                        tuple(output.shape),
                        len(non_primary),
                        len(raw_objects),
                    )
                    continue
                scores = preds[:, 4:]
                if scores.size == 0:
                    logger.warning(
                        "debug source=%s frame=%s obj_label=%s component=%s class=%s empty_obj_scores objects=%s raw_objects=%s",
                        source_id,
                        frame_num,
                        getattr(raw_obj, "obj_label", ""),
                        getattr(raw_obj, "unique_component_id", ""),
                        getattr(raw_obj, "class_id", ""),
                        len(non_primary),
                        len(raw_objects),
                    )
                    continue
                person_scores = scores[:, 0]
                logger.warning(
                    "debug source=%s frame=%s obj_label=%s component=%s class=%s obj_tensor_max=%.4f gt_010=%s gt_025=%s objects=%s raw_objects=%s",
                    source_id,
                    frame_num,
                    getattr(raw_obj, "obj_label", ""),
                    getattr(raw_obj, "unique_component_id", ""),
                    getattr(raw_obj, "class_id", ""),
                    float(person_scores.max()),
                    int((person_scores > 0.10).sum()),
                    int((person_scores > 0.25).sum()),
                    len(non_primary),
                    len(raw_objects),
                )
                return

        for user_meta in nvds_frame_user_meta_iterator(frame_meta.frame_meta):
            if user_meta.base_meta.meta_type != pyds.NVDSINFER_TENSOR_OUTPUT_META:
                continue
            tensor_meta = pyds.NvDsInferTensorMeta.cast(user_meta.user_meta_data)
            if tensor_meta.unique_id != 1:
                continue
            outputs = nvds_infer_tensor_meta_to_outputs(tensor_meta, ["output0"])
            output = outputs[0]
            if output is None:
                continue
            preds = output.T if output.shape[0] == 84 else output
            if preds.ndim != 2 or preds.shape[1] < 5:
                logger.warning(
                    "debug source=%s frame=%s unexpected_tensor_shape=%s objects=%s",
                    source_id,
                    frame_num,
                    tuple(output.shape),
                    len(non_primary),
                )
                continue
            scores = preds[:, 4:]
            if scores.size == 0:
                logger.warning(
                    "debug source=%s frame=%s empty_scores objects=%s",
                    source_id,
                    frame_num,
                    len(non_primary),
                )
                continue
            person_scores = scores[:, 0]
            logger.warning(
                "debug source=%s frame=%s tensor_max=%.4f gt_010=%s gt_025=%s objects=%s",
                source_id,
                frame_num,
                float(person_scores.max()),
                int((person_scores > 0.10).sum()),
                int((person_scores > 0.25).sum()),
                len(non_primary),
            )
            return

        logger.warning(
            "debug source=%s frame=%s no_tensor_meta objects=%s raw_objects=%s",
            source_id,
            frame_num,
            len(non_primary),
            len(raw_objects),
        )
