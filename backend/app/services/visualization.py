from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image


def color_for_id(track_id: int) -> tuple[int, int, int]:
    # Deterministic vivid-ish BGR color without external dependencies.
    x = int(track_id) * 2654435761 & 0xFFFFFFFF
    return (50 + (x & 0xA0), 50 + ((x >> 8) & 0xA0), 50 + ((x >> 16) & 0xA0))


def draw_frame(
    image: Image.Image,
    objects: list[dict[str, Any]],
    masks: dict[int, np.ndarray] | None = None,
    title: str | None = None,
) -> np.ndarray:
    frame = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR).copy()
    h, w = frame.shape[:2]
    overlay = frame.copy()

    if masks:
        for track_id, mask in masks.items():
            if mask is None:
                continue
            m = np.asarray(mask).astype(bool)
            if m.shape != (h, w):
                m = cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
            color = color_for_id(track_id)
            overlay[m] = (0.65 * overlay[m] + 0.35 * np.asarray(color)).astype(np.uint8)
    frame = cv2.addWeighted(overlay, 0.8, frame, 0.2, 0)

    for obj in objects:
        bbox = obj.get("bbox")
        if not bbox:
            continue
        x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
        x1 = max(0, min(w - 1, x1)); x2 = max(0, min(w - 1, x2))
        y1 = max(0, min(h - 1, y1)); y2 = max(0, min(h - 1, y2))
        track_id = int(obj.get("track_id", obj.get("object_id", obj.get("sam3_object_id", 0))))
        color = color_for_id(track_id)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        score = obj.get("score")
        source = obj.get("source", "")
        label = f"ID {track_id}"
        if score is not None:
            label += f" {float(score):.2f}"
        if source:
            label += f" [{source}"
            label += "]"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        yy = max(0, y1 - th - 6)
        cv2.rectangle(frame, (x1, yy), (min(w - 1, x1 + tw + 6), y1), color, -1)
        cv2.putText(frame, label, (x1 + 3, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

    if title:
        cv2.rectangle(frame, (0, 0), (min(w, 700), 30), (0, 0, 0), -1)
        cv2.putText(frame, title, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1, cv2.LINE_AA)
    return frame


def open_video_writer(path: str | Path, meta: dict[str, Any]) -> cv2.VideoWriter:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fps = float(meta.get("fps") or 30.0)
    width = int(meta["width"])
    height = int(meta["height"])
    for codec in ("mp4v", "avc1"):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), fps, (width, height))
        if writer.isOpened():
            return writer
    raise RuntimeError(f"Could not open video writer: {path}")
