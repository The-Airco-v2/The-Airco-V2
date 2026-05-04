"""Render evidence snapshots with red bounding boxes using Supervision."""

from __future__ import annotations

import cv2
import numpy as np
import supervision as sv


def render_evidence(
    frame: np.ndarray,
    bbox: list[float],
    label: str = "",
    color: tuple = (0, 0, 255),
) -> np.ndarray:
    annotated = frame.copy()

    x1, y1, x2, y2 = [int(v) for v in bbox]

    detections = sv.Detections(
        xyxy=np.array([[x1, y1, x2, y2]]),
        confidence=np.array([1.0]),
        class_id=np.array([0]),
    )

    box_annotator = sv.BoxAnnotator(
        color=sv.ColorPalette.from_hex(["#FF0000"]),
        thickness=3,
    )
    label_annotator = sv.LabelAnnotator(
        color=sv.ColorPalette.from_hex(["#FF0000"]),
        text_color=sv.Color.WHITE,
        text_scale=0.8,
    )

    annotated = box_annotator.annotate(annotated, detections)
    if label:
        annotated = label_annotator.annotate(
            annotated,
            detections,
            labels=[label],
        )

    return annotated


def encode_jpeg(frame: np.ndarray, quality: int = 90) -> bytes:
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()
