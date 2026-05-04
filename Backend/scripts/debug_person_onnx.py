"""Offline probe for the local person ONNX model on a saved frame."""

from __future__ import annotations

import argparse

import cv2
import numpy as np
import onnxruntime as ort


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"could not load image: {args.image}")

    resized = cv2.resize(img, (640, 640))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    inp = np.transpose(rgb, (2, 0, 1))[None, ...]

    session = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
    output = session.run(None, {session.get_inputs()[0].name: inp})[0]
    preds = output[0].T if output.shape[1] == 84 else output[0]
    scores = preds[:, 4:][:, 0]

    print("img_shape", img.shape)
    print("raw_shape", output.shape)
    print("pred_shape", preds.shape)
    print("person_max", float(scores.max()))
    print("count_gt_0.10", int((scores > 0.10).sum()))
    print("count_gt_0.25", int((scores > 0.25).sum()))

    for idx in np.argsort(scores)[-10:][::-1]:
        row = preds[idx]
        print(
            "top",
            int(idx),
            float(scores[idx]),
            row[:4].tolist(),
        )


if __name__ == "__main__":
    main()
