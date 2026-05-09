import cv2
import time
import queue
import threading
import numpy as np
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import tritonclient.grpc as grpcclient
import tritonclient.grpc.aio as grpcclient_async
from tritonclient.utils import np_to_triton_dtype

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
TRITON_URL         = "localhost:8001"
PHONE_CONF         = 0.08  # Adjusted for Triton model characteristics
PERSON_CONF        = 0.50  # Back to original PyTorch value
RTSP_URL           = "rtsp://admin:Vijay%405458@airco-office.ddns.net:8554/Streaming/Channels/401"

# Pose keypoint indices (COCO 17-point skeleton)
KP_NOSE        = 0
KP_L_EYE      = 1
KP_R_EYE      = 2
KP_L_EAR      = 3
KP_R_EAR      = 4
KP_L_SHOULDER  = 5
KP_R_SHOULDER  = 6
KP_L_ELBOW     = 7
KP_R_ELBOW     = 8
KP_L_WRIST     = 9
KP_R_WRIST     = 10

# Temporal analysis window
HISTORY_FRAMES = 15   # ~0.5 s at 30 fps

# Scoring thresholds
USAGE_SCORE_THRESHOLD  = 0.45   # above → "USING PHONE"
DETECT_SCORE_THRESHOLD = 0.10   # above → "PHONE DETECTED"

# ─────────────────────────────────────────────
#  DATA STRUCTURES
# ─────────────────────────────────────────────
@dataclass
class Detection:
    box: tuple          # (x1, y1, x2, y2)
    confidence: float
    label: str = ""
    score: float = 0.0  # usage score 0‥1

@dataclass
class PersonState:
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_FRAMES))
    last_label: str = "PHONE DETECTED"

# ─────────────────────────────────────────────
#  TRITON INFERENCE CLIENT
# ─────────────────────────────────────────────
class TritonInferenceClient:
    """Triton inference client for YOLO models."""
    
    def __init__(self, triton_url: str = "localhost:8001"):
        self.triton_url = triton_url
        self.phone_client = None
        self.pose_client = None
        self._init_clients()
    
    def _init_clients(self):
        """Initialize Triton clients."""
        try:
            self.phone_client = grpcclient.InferenceServerClient(
                url=self.triton_url, verbose=False
            )
            self.pose_client = grpcclient.InferenceServerClient(
                url=self.triton_url, verbose=False
            )
            print(f"✓ Connected to Triton at {self.triton_url}")
        except Exception as e:
            print(f"✗ Failed to connect to Triton: {e}")
            raise
    
    def preprocess_yolo_input(self, frame: np.ndarray, input_size: int = 640) -> np.ndarray:
        """Preprocess frame for YOLO model input."""
        # Resize and pad to maintain aspect ratio
        h, w = frame.shape[:2]
        scale = min(input_size / h, input_size / w)
        new_h, new_w = int(h * scale), int(w * scale)
        
        # Resize
        resized = cv2.resize(frame, (new_w, new_h))
        
        # Pad to input_size
        pad_h = input_size - new_h
        pad_w = input_size - new_w
        padded = cv2.copyMakeBorder(
            resized, 0, pad_h, 0, pad_w, 
            cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )
        
        # Convert to NCHW format and normalize
        input_tensor = padded.astype(np.float32) / 255.0
        input_tensor = input_tensor.transpose(2, 0, 1)  # HWC -> CHW
        input_tensor = np.expand_dims(input_tensor, axis=0)  # Add batch dimension
        
        return input_tensor, scale, pad_w, pad_h
    
    def infer_phone_detection(self, frame: np.ndarray) -> list[tuple]:
        """Run phone detection inference via Triton."""
        try:
            # Preprocess
            input_tensor, scale, pad_w, pad_h = self.preprocess_yolo_input(frame)
            
            # Prepare inference request
            inputs = [
                grpcclient.InferInput(
                    "images", input_tensor.shape, np_to_triton_dtype(input_tensor.dtype)
                )
            ]
            inputs[0].set_data_from_numpy(input_tensor)
            
            outputs = [
                grpcclient.InferRequestedOutput("output0")
            ]
            
            # Run inference
            response = self.phone_client.infer(
                model_name="phone_detection",
                inputs=inputs,
                outputs=outputs
            )
            
            # Process output
            output = response.as_numpy("output0")
            output = output.squeeze()  # Remove batch dimension
            
            # Handle different output formats
            if output.ndim == 2:
                if output.shape[0] == 5 and output.shape[1] == 8400:
                    # Format: [5, 8400] - transpose to [8400, 5]
                    output = output.T
                    boxes = []
                    max_conf = 0.0
                    high_conf_count = 0
                    
                    for detection in output:
                        x, y, w, h, conf = detection
                        max_conf = max(max_conf, conf)
                        if conf > 0.05:  # Count low-confidence detections
                            high_conf_count += 1
                            
                        if conf > PHONE_CONF:
                            # Convert center format to corner format and scale back to original frame
                            x1 = (x - w/2 - pad_w) / scale
                            y1 = (y - h/2 - pad_h) / scale
                            x2 = (x + w/2 - pad_w) / scale
                            y2 = (y + h/2 - pad_h) / scale
                            
                            # Clip to frame boundaries
                            x1, y1 = max(0, x1), max(0, y1)
                            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
                            
                            # Additional filtering: ensure reasonable box size
                            box_width = x2 - x1
                            box_height = y2 - y1
                            box_area = box_width * box_height
                            
                            # Filter out very small or very large boxes
                            if 20 < box_width < 300 and 20 < box_height < 300 and box_area > 400:
                                boxes.append((int(x1), int(y1), int(x2), int(y2), float(conf)))
                    
                    # Debug: Show confidence distribution every 30 frames
                    if hasattr(self, '_phone_debug_counter'):
                        self._phone_debug_counter += 1
                    else:
                        self._phone_debug_counter = 1
                        
                    if self._phone_debug_counter % 30 == 0:
                        print(f"📱 Phone: max_conf={max_conf:.3f}, >0.05={high_conf_count}, >{PHONE_CONF}={len(boxes)}")
                    
                    return boxes
                
                elif output.shape[1] == 6:
                    # Format: [x, y, w, h, conf, class]
                    boxes = []
                    for detection in output:
                        x, y, w, h, conf, cls = detection
                        if conf > PHONE_CONF and cls == 0:  # Phone class
                            # Convert center format to corner format and scale back to original frame
                            x1 = (x - w/2 - pad_w) / scale
                            y1 = (y - h/2 - pad_h) / scale
                            x2 = (x + w/2 - pad_w) / scale
                            y2 = (y + h/2 - pad_h) / scale
                            
                            # Clip to frame boundaries
                            x1, y1 = max(0, x1), max(0, y1)
                            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
                            
                            boxes.append((int(x1), int(y1), int(x2), int(y2), float(conf)))
                    
                    return boxes
                
                elif output.shape[1] == 5:
                    # Format: [x, y, w, h, conf] (single class)
                    boxes = []
                    for detection in output:
                        x, y, w, h, conf = detection
                        if conf > PHONE_CONF:
                            # Convert center format to corner format and scale back to original frame
                            x1 = (x - w/2 - pad_w) / scale
                            y1 = (y - h/2 - pad_h) / scale
                            x2 = (x + w/2 - pad_w) / scale
                            y2 = (y + h/2 - pad_h) / scale
                            
                            # Clip to frame boundaries
                            x1, y1 = max(0, x1), max(0, y1)
                            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
                            
                            boxes.append((int(x1), int(y1), int(x2), int(y2), float(conf)))
                    
                    return boxes
                
                elif output.shape[0] == 5 and output.shape[1] == 8400:
                    # Format: [5, 8400] - transpose to [8400, 5]
                    output = output.T
                    boxes = []
                    for detection in output:
                        x, y, w, h, conf = detection
                        if conf > PHONE_CONF:
                            # Convert center format to corner format and scale back to original frame
                            x1 = (x - w/2 - pad_w) / scale
                            y1 = (y - h/2 - pad_h) / scale
                            x2 = (x + w/2 - pad_w) / scale
                            y2 = (y + h/2 - pad_h) / scale
                            
                            # Clip to frame boundaries
                            x1, y1 = max(0, x1), max(0, y1)
                            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
                            
                            boxes.append((int(x1), int(y1), int(x2), int(y2), float(conf)))
                    
                    return boxes
            
            print(f"Unexpected output format: {output.shape}")
            return []
            
        except Exception as e:
            print(f"Phone detection inference error: {e}")
            return []
    
    def infer_pose_detection(self, frame: np.ndarray) -> list[tuple]:
        """Run pose detection inference via Triton."""
        try:
            # Preprocess
            input_tensor, scale, pad_w, pad_h = self.preprocess_yolo_input(frame)
            
            # Prepare inference request
            inputs = [
                grpcclient.InferInput(
                    "images", input_tensor.shape, np_to_triton_dtype(input_tensor.dtype)
                )
            ]
            inputs[0].set_data_from_numpy(input_tensor)
            
            outputs = [
                grpcclient.InferRequestedOutput("output0")
            ]
            
            # Run inference
            response = self.pose_client.infer(
                model_name="yolo26-pose",
                inputs=inputs,
                outputs=outputs
            )
            
            # Process output
            output = response.as_numpy("output0")
            output = output.squeeze()  # Remove batch dimension
            
                        
            # Handle different output formats
            if output.ndim == 2:
                if output.shape[1] == 57:
                    # Format: [x, y, w, h, conf, class, 17*3 keypoints] = 57
                    persons = []
                    for detection in output:
                        x, y, w, h, conf, cls = detection[:6]
                        if conf > PERSON_CONF and cls == 0:  # Person class
                            # Convert center format to corner format and scale back to original frame
                            x1 = (x - w/2 - pad_w) / scale
                            y1 = (y - h/2 - pad_h) / scale
                            x2 = (x + w/2 - pad_w) / scale
                            y2 = (y + h/2 - pad_h) / scale
                            
                            # Clip to frame boundaries
                            x1, y1 = max(0, x1), max(0, y1)
                            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
                            
                            # Extract keypoints (17 keypoints * 3 values = 51)
                            keypoints_raw = detection[6:]  # Skip bbox + conf + class
                            keypoints = keypoints_raw.reshape(17, 3).copy()  # Make writable copy
                            
                            # Scale keypoints back to original frame
                            keypoints[:, 0] = (keypoints[:, 0] - pad_w) / scale
                            keypoints[:, 1] = (keypoints[:, 1] - pad_h) / scale
                            
                            persons.append(((int(x1), int(y1), int(x2), int(y2)), keypoints, float(conf)))
                    
                    return persons
                
                elif output.shape[1] == 6:
                    # Format: [x, y, w, h, conf, class] - no keypoints
                    persons = []
                    for detection in output:
                        x, y, w, h, conf, cls = detection
                        if conf > PERSON_CONF and cls == 0:  # Person class
                            # Convert center format to corner format and scale back to original frame
                            x1 = (x - w/2 - pad_w) / scale
                            y1 = (y - h/2 - pad_h) / scale
                            x2 = (x + w/2 - pad_w) / scale
                            y2 = (y + h/2 - pad_h) / scale
                            
                            # Clip to frame boundaries
                            x1, y1 = max(0, x1), max(0, y1)
                            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
                            
                            persons.append(((int(x1), int(y1), int(x2), int(y2)), None, float(conf)))
                    
                    return persons
            
            print(f"Unexpected pose output format: {output.shape}")
            return []
            
        except Exception as e:
            print(f"Pose detection inference error: {e}")
            return []

# ─────────────────────────────────────────────
#  CORE CLASSIFIER (unchanged from original)
# ─────────────────────────────────────────────
class PhoneUsageClassifier:
    """
    Multi-signal classifier using Triton inference backend.
    
    Signals (each 0‥1, weighted sum → usage_score):
      1. Wrist proximity   – phone near either wrist
      2. Head proximity    – phone near ear / face (call posture)
      3. Elbow angle       – bent elbow = holding pose
      4. Phone orientation – portrait = actively held
      5. Temporal stability – label stays consistent over N frames
    """

    def __init__(self):
        print("Initializing Triton inference client …")
        self.triton_client = TritonInferenceClient(TRITON_URL)
        print("✓ Triton client ready")

        # Per-person temporal state  {person_id: PersonState}
        self._person_states: dict[int, PersonState] = {}

    # ── helpers ──────────────────────────────

    @staticmethod
    def _box_center(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @staticmethod
    def _box_size(box):
        x1, y1, x2, y2 = box
        return (x2 - x1, y2 - y1)

    @staticmethod
    def _distance(p1, p2):
        return np.hypot(p1[0] - p2[0], p1[1] - p2[1])

    def _kp(self, keypoints, idx):
        """Return (x, y, confidence) for a keypoint, or None if not visible."""
        if keypoints is None or idx >= len(keypoints):
            return None
        kp = keypoints[idx]
        if len(kp) < 3 or kp[2] < 0.3:   # low visibility → ignore
            return None
        return (float(kp[0]), float(kp[1]), float(kp[2]))

    # ── signal 1: wrist proximity ─────────────

    def _signal_wrist(self, phone_box, keypoints, ref_size):
        phone_center = self._box_center(phone_box)
        best = 0.0
        for wrist_idx in (KP_L_WRIST, KP_R_WRIST):
            kp = self._kp(keypoints, wrist_idx)
            if kp is None:
                continue
            dist = self._distance(phone_center, kp[:2])
            # Normalise by body ref (shoulder width or phone width)
            score = max(0.0, 1.0 - dist / (ref_size * 1.5))
            best = max(best, score)
        return best

    # ── signal 2: head / ear proximity ───────

    def _signal_head(self, phone_box, keypoints, ref_size):
        phone_center = self._box_center(phone_box)
        best = 0.0
        for head_idx in (KP_NOSE, KP_L_EAR, KP_R_EAR, KP_L_EYE, KP_R_EYE):
            kp = self._kp(keypoints, head_idx)
            if kp is None:
                continue
            dist = self._distance(phone_center, kp[:2])
            score = max(0.0, 1.0 - dist / (ref_size * 2.0))
            best = max(best, score)
        return best

    # ── signal 3: elbow bend ──────────────────

    def _signal_elbow(self, keypoints, ref_size):
        best = 0.0
        for shoulder_idx, elbow_idx, wrist_idx in [
            (KP_L_SHOULDER, KP_L_ELBOW, KP_L_WRIST),
            (KP_R_SHOULDER, KP_R_ELBOW, KP_R_WRIST),
        ]:
            s = self._kp(keypoints, shoulder_idx)
            e = self._kp(keypoints, elbow_idx)
            w = self._kp(keypoints, wrist_idx)
            if None in (s, e, w):
                continue
            # Angle at elbow (vectors shoulder→elbow and wrist→elbow)
            v1 = np.array(s[:2]) - np.array(e[:2])
            v2 = np.array(w[:2]) - np.array(e[:2])
            cos_a = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6)
            angle_deg = np.degrees(np.arccos(np.clip(cos_a, -1, 1)))
            # Ideal holding angle: 60°–120° → score near 1
            score = max(0.0, 1.0 - abs(angle_deg - 90) / 90)
            best = max(best, score)
        return best

    # ── signal 4: phone orientation ──────────

    @staticmethod
    def _signal_orientation(phone_box):
        """Portrait orientation (taller than wide) → more likely held."""
        x1, y1, x2, y2 = phone_box
        w, h = x2 - x1, y2 - y1
        if w == 0:
            return 0.0
        aspect = h / w
        # aspect > 1.3  → portrait (score = 1)
        # aspect < 0.8  → landscape (score = 0.3)
        if aspect >= 1.3:
            return 1.0
        elif aspect >= 0.9:
            return 0.6
        else:
            return 0.3

    # ── signal 5: temporal smoothing ─────────

    def _temporal_smooth(self, person_id: int, raw_score: float) -> float:
        state = self._person_states.setdefault(person_id, PersonState())
        state.history.append(raw_score)
        return float(np.mean(state.history))

    # ── main classify ────────────────────────

    def classify(self, phone_box, person_id: int,
                 keypoints=None, shoulder_width: float = 100) -> Detection:
        """
        Combine all signals into a single usage_score and return a Detection.
        """
        ref_size = max(shoulder_width, 60)   # fallback if shoulders not found

        if keypoints is None:
            # No pose available – rely on orientation only
            raw = self._signal_orientation(phone_box) * 0.4
        else:
            w_wrist   = 0.35 * self._signal_wrist(phone_box, keypoints, ref_size)
            w_head    = 0.25 * self._signal_head(phone_box, keypoints, ref_size)
            w_elbow   = 0.20 * self._signal_elbow(keypoints, ref_size)
            w_orient  = 0.20 * self._signal_orientation(phone_box)
            raw = w_wrist + w_head + w_elbow + w_orient

        score = self._temporal_smooth(person_id, raw)

        if score >= USAGE_SCORE_THRESHOLD:
            label = "USING PHONE"
        elif score >= DETECT_SCORE_THRESHOLD:
            label = "PHONE DETECTED"
        else:
            label = "PHONE"

        return Detection(box=phone_box, confidence=score, label=label, score=score)

    # ── full frame inference ─────────────────

    def infer(self, frame: np.ndarray) -> list[Detection]:
        detections: list[Detection] = []

        try:
            # 1. Detect phones via Triton (PyTorch-style processing)
            phone_boxes = []
            phone_results = self.triton_client.infer_phone_detection(frame)
            
            # Convert Triton results to PyTorch format: [(x1, y1, x2, y2)]
            for phone_data in phone_results:
                if len(phone_data) == 5:
                    x1, y1, x2, y2, phone_conf = phone_data
                    phone_boxes.append((x1, y1, x2, y2))
                elif len(phone_data) == 4:
                    x1, y1, x2, y2 = phone_data
                    phone_boxes.append((x1, y1, x2, y2))

            if not phone_boxes:
                return detections

            # 2. Detect people + pose via Triton (PyTorch-style processing)
            persons = []  # list of (box, keypoints_array, shoulder_width)
            pose_results = self.triton_client.infer_pose_detection(frame)
            
            for pose_result in pose_results:
                if len(pose_result) == 3:
                    pbox, kps, conf = pose_result
                    # PyTorch-style: check if person class (cls == 0)
                    # Triton already filters by PERSON_CONF, so all are persons
                    
                    # Shoulder width as body scale reference (PyTorch-style)
                    sw = 100.0
                    if kps is not None:
                        ls = self._kp(kps, KP_L_SHOULDER)
                        rs = self._kp(kps, KP_R_SHOULDER)
                        if ls and rs:
                            sw = self._distance(ls[:2], rs[:2])

                    persons.append((pbox, kps, sw))

            # 3. Match each phone to nearest person (PyTorch-style logic)
            for phone_box in phone_boxes:
                pc = self._box_center(phone_box)
                best_person_id = -1
                best_dist = float('inf')

                for pid, (pbox, kps, sw) in enumerate(persons):
                    px1, py1, px2, py2 = pbox
                    # PyTorch-style: Check overlap: phone center inside person box (expanded 30%)
                    ex = (px2 - px1) * 0.3
                    ey = (py2 - py1) * 0.3
                    if (px1 - ex <= pc[0] <= px2 + ex and
                            py1 - ey <= pc[1] <= py2 + ey):
                        dist = self._distance(pc, self._box_center(pbox))
                        if dist < best_dist:
                            best_dist = dist
                            best_person_id = pid

                if best_person_id == -1:
                    # Phone not near any person → definitely idle (PyTorch-style)
                    det = Detection(box=phone_box, confidence=0.1,
                                    label="PHONE (no person)", score=0.1)
                else:
                    _, kps, sw = persons[best_person_id]
                    det = self.classify(phone_box, best_person_id, kps, sw)

                detections.append(det)

        except Exception as e:
            print(f"Inference error: {e}")
            import traceback
            traceback.print_exc()
            return []

        return detections


# ─────────────────────────────────────────────
#  DRAWING (unchanged from original)
# ─────────────────────────────────────────────
LABEL_COLORS = {
    "USING PHONE":      (0, 0, 255),    # Red   – alert
    "PHONE DETECTED":   (0, 165, 255),  # Orange – caution
    "PHONE":            (0, 255, 0),    # Green  – safe
    "PHONE (no person)":(180, 180, 180) # Grey   – unknown
}

def draw_detections(frame: np.ndarray, detections: list[Detection]) -> np.ndarray:
    out = frame.copy()
    
    for det in detections:
        x1, y1, x2, y2 = det.box
        color = LABEL_COLORS.get(det.label, (255, 255, 255))

        # Validate bounding box coordinates
        if x1 >= x2 or y1 >= y2:
            continue

        # Ensure coordinates are within frame bounds
        h, w = out.shape[:2]
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(x2)), min(h, int(y2))

        if x1 >= w or y1 >= h or x2 <= 0 or y2 <= 0:
            continue

        # Bounding box
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

        # Label background
        label_text = f"{det.label} ({det.score:.2f})"
        (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(out, (x1, y1 - th - 8), (x1 + tw + 4, y1), color, -1)
        cv2.putText(out, label_text, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        # Score bar (small bar under the box)
        bar_w = x2 - x1
        if bar_w > 0:
            filled = int(bar_w * det.score)
            cv2.rectangle(out, (x1, y2 + 2), (x2, y2 + 8), (50, 50, 50), -1)
            cv2.rectangle(out, (x1, y2 + 2), (x1 + filled, y2 + 8), color, -1)

    return out


# ─────────────────────────────────────────────
#  THREADED PIPELINE (same architecture as original)
# ─────────────────────────────────────────────
frame_queue  = queue.Queue(maxsize=2)
result_queue = queue.Queue(maxsize=2)
processing   = True


def capture_frames():
    global processing
    retry_count = 0
    max_retries = 3
    
    while processing:
        if retry_count >= max_retries:
            print(f"❌ Failed to connect after {max_retries} attempts. Please check RTSP connection.")
            time.sleep(10)
            retry_count = 0
            continue
            
        cap = cv2.VideoCapture(RTSP_URL)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'H264'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        
        # Set timeout for RTSP connection
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)  # 10 seconds
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)   # 5 seconds

        if not cap.isOpened():
            retry_count += 1
            print(f"Could not connect to RTSP. Retry {retry_count}/{max_retries}. Retrying in 5 s …")
            time.sleep(5)
            continue

        print("✓ Connected to RTSP stream")
        retry_count = 0  # Reset retry count on successful connection
        frame_count = 0
        
        while processing:
            ret, frame = cap.read()
            if not ret:
                print("Stream lost. Reconnecting …")
                break
                
            frame_count += 1
            frame = cv2.resize(frame, (640, 384))
            
            # Add frame info every 30 frames
            if frame_count % 30 == 0:
                print(f"📹 Processing frame #{frame_count}")
            
            if frame_queue.full():
                try:
                    frame_queue.get_nowait()
                except queue.Empty:
                    pass
            try:
                frame_queue.put_nowait(frame)
            except queue.Full:
                pass
                
        cap.release()
        time.sleep(2)  # Brief pause before reconnection


def process_frames(classifier: PhoneUsageClassifier):
    global processing
    detection_count = 0
    
    while processing:
        try:
            frame = frame_queue.get(timeout=1.0)
            detections = classifier.infer(frame)
            
            # Debug: Print detection info every 30 frames
            detection_count += 1
            if detection_count % 30 == 0 and len(detections) > 0:
                print(f"🔍 Frame #{detection_count}: Found {len(detections)} detections")
                for i, det in enumerate(detections):
                    print(f"  Detection {i+1}: {det.label} (score: {det.score:.2f}, conf: {det.confidence:.2f})")
            
            display = draw_detections(frame, detections)

            if result_queue.full():
                try:
                    result_queue.get_nowait()
                except queue.Empty:
                    pass
            result_queue.put_nowait(display)

        except queue.Empty:
            continue
        except Exception as e:
            print(f"Processing error: {e}")
            import traceback
            traceback.print_exc()


def main():
    global processing
    classifier = PhoneUsageClassifier()

    capture_thread = threading.Thread(target=capture_frames, daemon=True)
    process_thread = threading.Thread(target=process_frames,
                                      args=(classifier,), daemon=True)
    capture_thread.start()
    process_thread.start()

    fps, frame_count = 0, 0
    last_fps_time = time.time()

    print("\n🚀 TRITON-BASED PIPELINE ACTIVE")
    print("📊 Comparing with PyTorch baseline...")
    print("\nPress 'q' to quit\n")
    try:
        while True:
            try:
                display_frame = result_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            frame_count += 1
            now = time.time()
            if now - last_fps_time >= 1.0:
                fps = frame_count
                frame_count = 0
                last_fps_time = now

            cv2.putText(display_frame, f"Triton FPS: {fps}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.putText(display_frame, "Triton Pipeline", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            cv2.imshow('Triton Phone Usage Classifier', display_frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        processing = False
        capture_thread.join(timeout=3)
        process_thread.join(timeout=3)
        cv2.destroyAllWindows()
        print("Stopped.")


if __name__ == '__main__':
    main()
