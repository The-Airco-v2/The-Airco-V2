"""5-point similarity transform for ArcFace face alignment."""
from __future__ import annotations
import cv2
import numpy as np

# Standard ArcFace reference keypoints for 112x112 target (from InsightFace)
ARCFACE_REF_POINTS = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)

def align_face(img: np.ndarray, keypoints: np.ndarray, output_size: tuple[int, int] = (112, 112)) -> np.ndarray | None:
    """Align a face image using 5 keypoints via similarity transform.
    Args: img (BGR), keypoints (5,2), output_size (w,h)
    Returns: aligned 112x112 BGR image, or None if alignment fails.
    """
    keypoints = np.asarray(keypoints, dtype=np.float32)
    if keypoints.shape != (5, 2):
        return None
    # Check for degenerate keypoints
    spread = np.std(keypoints, axis=0).sum()
    if spread < 1.0:
        return None
    # Similarity transform
    tform, _ = cv2.estimateAffinePartial2D(keypoints, ARCFACE_REF_POINTS, method=cv2.LMEDS)
    if tform is None:
        return None
    aligned = cv2.warpAffine(img, tform, output_size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    return aligned
