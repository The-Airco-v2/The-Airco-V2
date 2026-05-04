from __future__ import annotations

import struct
from typing import Tuple

import cv2
import numpy as np


def l2_normalize(vec: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vec))
    if norm < eps:
        return np.zeros_like(vec, dtype=np.float32)
    return (vec / norm).astype(np.float32)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    a = a.flatten().astype(np.float32)
    b = b.flatten().astype(np.float32)
    min_len = min(len(a), len(b))
    a, b = a[:min_len], b[:min_len]
    a_norm = a / (np.linalg.norm(a) + 1e-6)
    b_norm = b / (np.linalg.norm(b) + 1e-6)
    return float(np.dot(a_norm, b_norm))


def clip_bbox(bbox, width: int, height: int):
    x1, y1, x2, y2 = map(int, bbox)
    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(0, min(width, x2))
    y2 = max(0, min(height, y2))
    return x1, y1, x2, y2


def bbox_iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    iw = max(0, inter_x2 - inter_x1)
    ih = max(0, inter_y2 - inter_y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(1, (ax2 - ax1)) * max(1, (ay2 - ay1))
    area_b = max(1, (bx2 - bx1)) * max(1, (by2 - by1))
    return float(inter / max(area_a + area_b - inter, 1))


def bbox_center(bbox) -> Tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _pb_varint_encode(value: int) -> bytes:
    value = int(value)
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def _pb_varint_decode(data: bytes, pos: int) -> Tuple[int, int]:
    shift = 0
    result = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _pb_key(field_number: int, wire_type: int) -> bytes:
    return _pb_varint_encode((field_number << 3) | wire_type)


def _pb_float_bytes(values) -> bytes:
    arr = np.asarray(values, dtype=np.float32).reshape(-1)
    return b"".join(struct.pack("<f", float(v)) for v in arr)


def _pb_double_bytes(value: float) -> bytes:
    return struct.pack("<d", float(value))


def _pb_float32(value: float) -> bytes:
    return struct.pack("<f", float(value))


def compute_color_histogram(frame, x1, y1, x2, y2) -> np.ndarray:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = clip_bbox((x1, y1, x2, y2), w, h)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return np.zeros(512, dtype=np.float32)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
    hist = cv2.normalize(hist, hist).flatten().astype(np.float32)
    if hist.size < 512:
        hist = np.pad(hist, (0, 512 - hist.size))
    return l2_normalize(hist[:512])

