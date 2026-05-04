from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .utils import bbox_center, cosine_sim, l2_normalize

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    HAS_TORCH = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_TORCH = False

try:
    import networkx as nx  # noqa: F401
    HAS_NX = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_NX = False

try:
    from filterpy.kalman import MerweScaledSigmaPoints, UnscentedKalmanFilter
    HAS_UKF = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_UKF = False

try:
    from scipy.optimize import linear_sum_assignment
    HAS_SCIPY = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_SCIPY = False
    linear_sum_assignment = None


def _ukf_fx(x, dt):
    F = np.array(
        [
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ]
    )
    return F @ x


def _ukf_hx(x):
    return x[:2]


def create_ukf(initial_center: Tuple[float, float], cfg: dict):
    if not HAS_UKF or not cfg.get("use_ukf", True):
        kf = cv2.KalmanFilter(4, 2)
        kf.transitionMatrix = np.array(
            [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]],
            dtype=np.float32,
        )
        kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
        kf.processNoiseCov = np.eye(4, dtype=np.float32) * cfg["ukf_process_noise"]
        kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * cfg["ukf_measurement_noise"]
        kf.errorCovPost = np.eye(4, dtype=np.float32)
        kf.statePost = np.array(
            [[initial_center[0]], [initial_center[1]], [0.0], [0.0]], dtype=np.float32
        )
        return ("cv2", kf)

    points = MerweScaledSigmaPoints(
        n=4,
        alpha=cfg.get("ukf_alpha", 1e-3),
        beta=cfg.get("ukf_beta", 2.0),
        kappa=cfg.get("ukf_kappa", 0.0),
    )
    ukf = UnscentedKalmanFilter(dim_x=4, dim_z=2, dt=1.0, fx=_ukf_fx, hx=_ukf_hx, points=points)
    ukf.x = np.array([initial_center[0], initial_center[1], 0.0, 0.0])
    ukf.P *= 1.0
    ukf.R = np.eye(2) * cfg["ukf_measurement_noise"]
    ukf.Q = np.eye(4) * cfg["ukf_process_noise"]
    return ("ukf", ukf)


def ukf_predict(filter_tuple) -> Tuple[float, float]:
    kind, filt = filter_tuple
    if kind == "ukf":
        try:
            filt.predict()
            return float(filt.x[0]), float(filt.x[1])
        except Exception:
            return float(filt.x[0]), float(filt.x[1])
    try:
        state = filt.predict()
        return float(state[0, 0]), float(state[1, 0])
    except Exception:
        return float(filt.statePost[0, 0]), float(filt.statePost[1, 0])


def ukf_correct(filter_tuple, center: Tuple[float, float]):
    kind, filt = filter_tuple
    if kind == "ukf":
        try:
            filt.update(np.array([center[0], center[1]]))
        except Exception:
            pass
        return
    meas = np.array([[np.float32(center[0])], [np.float32(center[1])]])
    try:
        filt.correct(meas)
    except Exception:
        pass


if HAS_TORCH:

    class PositionalEncoding(nn.Module):
        def __init__(self, d_model: int, max_len: int = 512):
            super().__init__()
            pe = torch.zeros(max_len, d_model)
            position = torch.arange(0, max_len).unsqueeze(1).float()
            div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-np.log(10000.0) / d_model))
            pe[:, 0::2] = torch.sin(position * div_term)
            pe[:, 1::2] = torch.cos(position * div_term)
            self.register_buffer("pe", pe.unsqueeze(0))

        def forward(self, x):
            return x + self.pe[:, : x.size(1)]


    class TransformerMotionPredictor(nn.Module):
        def __init__(self, d_model: int = 128, nhead: int = 4, num_layers: int = 2, embed_dim: int = 512):
            super().__init__()
            self.appearance_proj = nn.Linear(embed_dim, d_model // 2)
            self.spatial_proj = nn.Linear(6, d_model // 2)
            self.pos_enc = PositionalEncoding(d_model)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model, nhead, dim_feedforward=256, batch_first=True, dropout=0.0
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers)
            self.bbox_head = nn.Sequential(nn.Linear(d_model, 64), nn.ReLU(), nn.Linear(64, 4))
            self.motion_head = nn.Sequential(nn.Linear(d_model, 64), nn.ReLU(), nn.Linear(64, 2))

        def forward(self, spatial_seq, appearance_seq):
            sp = self.spatial_proj(spatial_seq)
            ap = self.appearance_proj(appearance_seq)
            x = torch.cat([sp, ap], dim=-1)
            x = self.pos_enc(x)
            x = self.encoder(x)
            last = x[:, -1]
            return self.bbox_head(last), self.motion_head(last)


    class TransformerMotionManager:
        def __init__(self, cfg: dict):
            self.cfg = cfg
            self.device_str = cfg.get("device", "cpu")
            self.device = torch.device(self.device_str)
            self.window = cfg.get("temporal_history_len", 8)
            self.model = TransformerMotionPredictor(
                d_model=cfg["transformer_d_model"],
                nhead=cfg["transformer_nhead"],
                num_layers=cfg["transformer_num_layers"],
                embed_dim=512,
            ).to(self.device)
            self.model.eval()
            model_path = cfg.get("transformer_model_path")
            if model_path:
                from pathlib import Path

                if Path(model_path).exists():
                    try:
                        state = torch.load(model_path, map_location=self.device)
                        self.model.load_state_dict(state)
                    except Exception:
                        pass
            self.spatial_history: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.window))
            self.appearance_history: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.window))

        def update(self, gid: int, bbox: Tuple, embedding: Optional[np.ndarray], velocity: Tuple):
            x1, y1, x2, y2 = bbox
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            w = float(x2 - x1)
            h = float(y2 - y1)
            vx, vy = velocity
            spatial = np.array([cx, cy, w, h, vx, vy], dtype=np.float32)
            self.spatial_history[gid].append(spatial)
            emb = embedding if embedding is not None else np.zeros(512, dtype=np.float32)
            self.appearance_history[gid].append(emb.astype(np.float32))

        def predict(self, gid: int, last_bbox: Tuple, last_velocity: Tuple) -> Tuple[Tuple, Tuple]:
            sp_buf = self.spatial_history.get(gid)
            ap_buf = self.appearance_history.get(gid)
            if sp_buf is None or len(sp_buf) < 2:
                x1, y1, x2, y2 = last_bbox
                vx, vy = last_velocity
                return ((int(x1 + vx), int(y1 + vy), int(x2 + vx), int(y2 + vy)), (vx, vy))
            with torch.no_grad():
                sp_arr = np.stack(list(sp_buf))[np.newaxis]
                ap_arr = np.stack(list(ap_buf))[np.newaxis]
                sp_t = torch.tensor(sp_arr, dtype=torch.float32, device=self.device)
                ap_t = torch.tensor(ap_arr, dtype=torch.float32, device=self.device)
                try:
                    bbox_delta, vel = self.model(sp_t, ap_t)
                    bbox_delta = bbox_delta[0].cpu().numpy()
                    vel = vel[0].cpu().numpy()
                except Exception:
                    bbox_delta = np.zeros(4)
                    vel = np.array(last_velocity)
            last_sp = sp_buf[-1]
            cx = last_sp[0] + bbox_delta[0]
            cy = last_sp[1] + bbox_delta[1]
            w = max(10.0, last_sp[2] + bbox_delta[2])
            h = max(10.0, last_sp[3] + bbox_delta[3])
            pred_bbox = (int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2))
            return pred_bbox, (float(vel[0]), float(vel[1]))

        def remove(self, gid: int):
            self.spatial_history.pop(gid, None)
            self.appearance_history.pop(gid, None)


    class TemporalConsistencyModule(nn.Module):
        def __init__(self, feature_dim: int = 512, num_heads: int = 4):
            super().__init__()
            self.proj = nn.Linear(feature_dim, 256)
            self.temporal_attn = nn.MultiheadAttention(256, num_heads, batch_first=True, dropout=0.0)
            self.fusion = nn.Sequential(
                nn.Linear(512, 256), nn.LayerNorm(256), nn.ReLU(), nn.Linear(256, 256)
            )
            self.change_gate = nn.Sequential(nn.Linear(256, 64), nn.ReLU(), nn.Linear(64, 1), nn.Sigmoid())
            self.out_proj = nn.Linear(256, feature_dim)

        def forward(self, current_emb: torch.Tensor, history_embs: torch.Tensor):
            hist_p = self.proj(history_embs)
            curr_p = self.proj(current_emb.unsqueeze(1))
            attn_out, _ = self.temporal_attn(hist_p, hist_p, hist_p)
            temporal_feat = attn_out.mean(dim=1)
            change_score = self.change_gate(temporal_feat)
            fused_in = torch.cat([curr_p.squeeze(1), temporal_feat], dim=-1)
            fused = self.fusion(fused_in)
            consistent = self.out_proj(fused)
            consistent = F.normalize(consistent, p=2, dim=-1)
            return consistent, change_score


    class TemporalConsistencyManager:
        def __init__(self, cfg: dict, feature_dim: int = 512):
            self.cfg = cfg
            self.window = cfg.get("temporal_history_len", 8)
            self.change_thr = cfg.get("temporal_change_threshold", 0.7)
            self.device = torch.device(cfg.get("device", "cpu"))
            self.module = TemporalConsistencyModule(feature_dim).to(self.device)
            self.module.eval()
            self.history: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.window))

        def update_embedding(self, gid: int, new_emb: np.ndarray, current_ema: np.ndarray) -> Tuple[np.ndarray, float]:
            self.history[gid].append(new_emb.astype(np.float32))
            if len(self.history[gid]) < 3:
                alpha = self.cfg["ema_alpha"]
                blended = l2_normalize(alpha * current_ema + (1 - alpha) * new_emb)
                return blended, 0.0
            with torch.no_grad():
                hist_arr = np.stack(list(self.history[gid]))[np.newaxis]
                curr_t = torch.tensor(new_emb[np.newaxis], dtype=torch.float32, device=self.device)
                hist_t = torch.tensor(hist_arr, dtype=torch.float32, device=self.device)
                try:
                    consistent_t, change_t = self.module(curr_t, hist_t)
                    consistent = consistent_t[0].cpu().numpy()
                    change_score = float(change_t[0, 0].item())
                except Exception:
                    consistent = new_emb
                    change_score = 0.0
            return l2_normalize(consistent), change_score

        def remove(self, gid: int):
            self.history.pop(gid, None)


    class GraphConvLayer(nn.Module):
        def __init__(self, in_ch: int, out_ch: int):
            super().__init__()
            self.lin = nn.Linear(in_ch, out_ch)
            self.bn = nn.BatchNorm1d(out_ch)

        def forward(self, x: torch.Tensor, edge_index: torch.Tensor, edge_weight: torch.Tensor) -> torch.Tensor:
            agg = torch.zeros_like(self.lin(x))
            src, dst = edge_index[0], edge_index[1]
            msgs = self.lin(x)[src] * edge_weight.unsqueeze(-1)
            agg.scatter_add_(0, dst.unsqueeze(-1).expand_as(msgs), msgs)
            agg = agg + self.lin(x)
            return F.relu(self.bn(agg))


    class CrossCameraGNN(nn.Module):
        def __init__(self, feat_dim: int = 512, hidden: int = 256, out_dim: int = 128):
            super().__init__()
            self.conv1 = GraphConvLayer(feat_dim, hidden)
            self.conv2 = GraphConvLayer(hidden, out_dim)

        def forward(self, x, edge_index, edge_weight):
            x = self.conv1(x, edge_index, edge_weight)
            x = self.conv2(x, edge_index, edge_weight)
            return F.normalize(x, p=2, dim=-1)


    class CrossCameraGraphMatcher:
        def __init__(self, cfg: dict):
            self.cfg = cfg
            self.num_cams = cfg.get("num_cameras", 6)
            self.sigma = cfg.get("camera_transition_sigma", 30.0)
            self.camera_adjacency = cfg.get("camera_adjacency", {})
            self.min_travel_time = float(cfg.get("min_travel_time", 2.0))
            self.transition_mean = np.zeros((self.num_cams + 1, self.num_cams + 1))
            self.transition_count = np.zeros((self.num_cams + 1, self.num_cams + 1))
            self.enabled = HAS_TORCH and HAS_NX and cfg.get("use_gnn_matching", True)
            if self.enabled:
                self.device = torch.device(cfg.get("device", "cpu"))
                self.gnn = CrossCameraGNN().to(self.device)
                self.gnn.eval()

        def _cam_idx(self, cam_id: str) -> int:
            try:
                return int(cam_id)
            except Exception:
                return 0

        def _adjacency_key(self, from_cam: str, to_cam: str):
            return (str(from_cam), str(to_cam))

        def is_transition_possible(self, from_cam: str, to_cam: str, elapsed_seconds: float) -> bool:
            if str(from_cam) == str(to_cam):
                return True
            if elapsed_seconds < self.min_travel_time:
                return False
            key = self._adjacency_key(from_cam, to_cam)
            if not self.camera_adjacency:
                return True
            if key not in self.camera_adjacency:
                return True
            expected = float(self.camera_adjacency[key])
            return elapsed_seconds <= expected + 3.0 * self.sigma

        def update_transition(self, from_cam: str, to_cam: str, elapsed_seconds: float):
            i, j = self._cam_idx(from_cam), self._cam_idx(to_cam)
            alpha = 0.1
            n = self.transition_count[i, j]
            if n == 0:
                self.transition_mean[i, j] = elapsed_seconds
            else:
                self.transition_mean[i, j] = (1 - alpha) * self.transition_mean[i, j] + alpha * elapsed_seconds
            self.transition_count[i, j] += 1

        def transition_probability(self, from_cam: str, to_cam: str, elapsed_seconds: float) -> float:
            i, j = self._cam_idx(from_cam), self._cam_idx(to_cam)
            if self.transition_count[i, j] < 3:
                return 0.5
            expected = self.transition_mean[i, j]
            return float(np.exp(-((elapsed_seconds - expected) ** 2) / (2.0 * self.sigma ** 2)))

        def boost_score(self, base_score: float, from_cam: str, to_cam: str, elapsed_seconds: float) -> float:
            if from_cam == to_cam:
                return base_score
            if not self.is_transition_possible(from_cam, to_cam, elapsed_seconds):
                return -1.0
            prob = self.transition_probability(from_cam, to_cam, elapsed_seconds)
            return float(base_score * (0.6 + 0.4 * prob))

        def get_cross_camera_threshold(self, from_cam: str, to_cam: str, base_thr: float) -> float:
            if str(from_cam) == str(to_cam):
                return float(base_thr)
            key = self._adjacency_key(from_cam, to_cam)
            if key in self.camera_adjacency:
                return float(max(0.25, base_thr - 0.08))
            return float(min(0.85, base_thr + 0.12))

        def gnn_rerank(self, query_emb: np.ndarray, gallery_embs: List[np.ndarray], gallery_cams: List[str], query_cam: str) -> np.ndarray:
            if not self.enabled or len(gallery_embs) == 0:
                return np.array([cosine_sim(query_emb, g) for g in gallery_embs])
            N = len(gallery_embs) + 1
            all_embs = [query_emb] + gallery_embs
            max_len = max(len(e) for e in all_embs)
            padded = []
            for e in all_embs:
                if len(e) < max_len:
                    e = np.pad(e, (0, max_len - len(e)))
                padded.append(e[:max_len])
            x = torch.tensor(np.stack(padded), dtype=torch.float32, device=self.device)
            edges_src, edges_dst, weights = [], [], []
            all_cams = [query_cam] + gallery_cams
            for i in range(N):
                for j in range(N):
                    if i == j:
                        continue
                    w = 1.0 if all_cams[i] == all_cams[j] else 0.4
                    edges_src.append(i)
                    edges_dst.append(j)
                    weights.append(w)
            if not edges_src:
                return np.array([cosine_sim(query_emb, g) for g in gallery_embs])
            edge_index = torch.tensor([edges_src, edges_dst], dtype=torch.long, device=self.device)
            edge_weight = torch.tensor(weights, dtype=torch.float32, device=self.device)
            with torch.no_grad():
                try:
                    out = self.gnn(x, edge_index, edge_weight)
                    q_out = out[0]
                    g_out = out[1:]
                    sims = F.cosine_similarity(q_out.unsqueeze(0), g_out, dim=1)
                    return sims.cpu().numpy()
                except Exception:
                    return np.array([cosine_sim(query_emb, g) for g in gallery_embs])

else:

    class TransformerMotionManager:
        def __init__(self, cfg): ...
        def update(self, *a, **kw): ...
        def predict(self, gid, last_bbox, last_velocity):
            x1, y1, x2, y2 = last_bbox
            vx, vy = last_velocity
            return (int(x1 + vx), int(y1 + vy), int(x2 + vx), int(y2 + vy)), (vx, vy)
        def remove(self, gid): ...

    class TemporalConsistencyManager:
        def __init__(self, cfg, feature_dim=512):
            self.cfg = cfg
        def update_embedding(self, gid, new_emb, current_ema):
            alpha = self.cfg["ema_alpha"]
            return l2_normalize(alpha * current_ema + (1 - alpha) * new_emb), 0.0
        def remove(self, gid): ...

    class CrossCameraGraphMatcher:
        def __init__(self, cfg: dict):
            self.cfg = cfg
            self.camera_adjacency = cfg.get("camera_adjacency", {})
            self.min_travel_time = float(cfg.get("min_travel_time", 2.0))
        def is_transition_possible(self, from_cam: str, to_cam: str, elapsed_seconds: float) -> bool:
            if str(from_cam) == str(to_cam):
                return True
            return elapsed_seconds >= self.min_travel_time
        def update_transition(self, from_cam: str, to_cam: str, elapsed_seconds: float):
            return None
        def transition_probability(self, from_cam: str, to_cam: str, elapsed_seconds: float) -> float:
            return 0.5
        def boost_score(self, base_score: float, from_cam: str, to_cam: str, elapsed_seconds: float) -> float:
            return base_score if self.is_transition_possible(from_cam, to_cam, elapsed_seconds) else -1.0
        def get_cross_camera_threshold(self, from_cam: str, to_cam: str, base_thr: float) -> float:
            return float(base_thr)
        def gnn_rerank(self, query_emb: np.ndarray, gallery_embs: List[np.ndarray], gallery_cams: List[str], query_cam: str) -> np.ndarray:
            return np.array([cosine_sim(query_emb, g) for g in gallery_embs])
