from __future__ import annotations

from collections import deque

import numpy as np

from .utils import _pb_double_bytes, _pb_float32, _pb_float_bytes, _pb_key, _pb_varint_decode, _pb_varint_encode


class PersonEmbeddingCodec:
    @staticmethod
    def encode_embedding_vector(values) -> bytes:
        payload = _pb_float_bytes(values)
        return _pb_key(1, 2) + _pb_varint_encode(len(payload)) + payload

    @staticmethod
    def decode_embedding_vector(data: bytes) -> np.ndarray:
        values = []
        pos = 0
        while pos < len(data):
            key, pos = _pb_varint_decode(data, pos)
            field = key >> 3
            wire = key & 0x7
            if field == 1 and wire == 2:
                length, pos = _pb_varint_decode(data, pos)
                end = pos + length
                while pos < end:
                    values.append(np.frombuffer(data[pos:pos + 4], dtype=np.float32)[0])
                    pos += 4
            else:
                if wire == 0:
                    _, pos = _pb_varint_decode(data, pos)
                elif wire == 1:
                    pos += 8
                elif wire == 2:
                    length, pos = _pb_varint_decode(data, pos)
                    pos += length
                elif wire == 5:
                    pos += 4
                else:
                    raise ValueError(f"unsupported wire type {wire}")
        return np.asarray(values, dtype=np.float32)

    @staticmethod
    def encode_identity(identity) -> bytes:
        parts = []

        def add_varint(field, value):
            parts.append(_pb_key(field, 0) + _pb_varint_encode(int(value)))

        def add_string(field, value):
            raw = str(value).encode("utf-8")
            parts.append(_pb_key(field, 2) + _pb_varint_encode(len(raw)) + raw)

        def add_fixed64(field, value):
            parts.append(_pb_key(field, 1) + _pb_double_bytes(float(value)))

        def add_fixed32(field, value):
            parts.append(_pb_key(field, 5) + _pb_float32(float(value)))

        def add_message(field, values):
            if values is None:
                return
            payload = PersonEmbeddingCodec.encode_embedding_vector(values)
            parts.append(_pb_key(field, 2) + _pb_varint_encode(len(payload)) + payload)

        add_varint(1, identity.global_id)
        add_string(2, identity.birth_camera)
        add_string(3, identity.last_camera)
        add_fixed64(4, identity.last_seen_time)
        add_varint(5, identity.total_detections)
        add_message(6, identity.seed_embedding)
        add_message(7, identity.ema_embedding)

        viewpoint_bank = list(identity.viewpoint_bank)
        viewpoint_cameras = list(getattr(identity, "viewpoint_cameras", []))
        for idx, vp in enumerate(viewpoint_bank):
            cam = viewpoint_cameras[idx] if idx < len(viewpoint_cameras) else identity.last_camera
            payload = PersonEmbeddingCodec.encode_embedding_vector(vp)
            parts.append(_pb_key(8, 2) + _pb_varint_encode(len(payload)) + payload)
            add_string(9, cam)

        add_message(10, identity.color_signature)
        add_varint(11, identity.birth_frame)
        add_varint(12, identity.last_seen_frame)
        add_varint(13, identity.lock_until_frame)
        add_fixed32(14, identity.last_match_score)

        if identity.last_bbox is not None:
            payload = _pb_float_bytes(identity.last_bbox)
            parts.append(_pb_key(15, 2) + _pb_varint_encode(len(payload)) + payload)
        if identity.last_center is not None:
            payload = _pb_float_bytes(identity.last_center)
            parts.append(_pb_key(16, 2) + _pb_varint_encode(len(payload)) + payload)
        if identity.velocity is not None:
            payload = _pb_float_bytes(identity.velocity)
            parts.append(_pb_key(17, 2) + _pb_varint_encode(len(payload)) + payload)

        return b"".join(parts)

    @staticmethod
    def decode_identity(data: bytes, max_viewpoints: int = 25) -> dict:
        out = {
            "global_id": 0,
            "birth_camera": "1",
            "last_camera": "1",
            "last_seen_time": 0.0,
            "total_detections": 0,
            "seed_embedding": None,
            "ema_embedding": None,
            "viewpoint_bank": deque(maxlen=max_viewpoints),
            "viewpoint_cameras": deque(maxlen=max_viewpoints),
            "color_signature": None,
            "birth_frame": -1,
            "last_seen_frame": -1,
            "lock_until_frame": -1,
            "last_match_score": 0.0,
            "last_bbox": (0, 0, 0, 0),
            "last_center": (0.0, 0.0),
            "velocity": (0.0, 0.0),
        }

        pos = 0
        while pos < len(data):
            key, pos = _pb_varint_decode(data, pos)
            field = key >> 3
            wire = key & 0x7

            if field in {1, 5, 11, 12, 13} and wire == 0:
                value, pos = _pb_varint_decode(data, pos)
                out_map = {
                    1: "global_id",
                    5: "total_detections",
                    11: "birth_frame",
                    12: "last_seen_frame",
                    13: "lock_until_frame",
                }
                out[out_map[field]] = int(value)
            elif field in {2, 3} and wire == 2:
                length, pos = _pb_varint_decode(data, pos)
                raw = data[pos:pos + length]
                pos += length
                out_map = {2: "birth_camera", 3: "last_camera"}
                out[out_map[field]] = raw.decode("utf-8", errors="ignore")
            elif field == 4 and wire == 1:
                out["last_seen_time"] = float(np.frombuffer(data[pos:pos + 8], dtype=np.float64)[0])
                pos += 8
            elif field in {6, 7, 10} and wire == 2:
                length, pos = _pb_varint_decode(data, pos)
                raw = data[pos:pos + length]
                pos += length
                vec = PersonEmbeddingCodec.decode_embedding_vector(raw)
                out_map = {6: "seed_embedding", 7: "ema_embedding", 10: "color_signature"}
                out[out_map[field]] = vec if vec.size else None
            elif field == 8 and wire == 2:
                length, pos = _pb_varint_decode(data, pos)
                raw = data[pos:pos + length]
                pos += length
                vec = PersonEmbeddingCodec.decode_embedding_vector(raw)
                if vec.size:
                    out["viewpoint_bank"].append(vec)
            elif field == 9 and wire == 2:
                length, pos = _pb_varint_decode(data, pos)
                raw = data[pos:pos + length]
                pos += length
                out["viewpoint_cameras"].append(raw.decode("utf-8", errors="ignore"))
            elif field == 14 and wire == 5:
                out["last_match_score"] = float(np.frombuffer(data[pos:pos + 4], dtype=np.float32)[0])
                pos += 4
            elif field in {15, 16, 17} and wire == 2:
                length, pos = _pb_varint_decode(data, pos)
                raw = data[pos:pos + length]
                pos += length
                values = list(np.frombuffer(raw, dtype=np.float32)) if raw else []
                out_map = {15: "last_bbox", 16: "last_center", 17: "velocity"}
                if values:
                    out[out_map[field]] = tuple(values)
            else:
                if wire == 0:
                    _, pos = _pb_varint_decode(data, pos)
                elif wire == 1:
                    pos += 8
                elif wire == 2:
                    length, pos = _pb_varint_decode(data, pos)
                    pos += length
                elif wire == 5:
                    pos += 4
                else:
                    raise ValueError(f"unsupported wire type {wire}")

        return out

