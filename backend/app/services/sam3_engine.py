from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

import cv2
import numpy as np
import torch
from PIL import Image


@dataclass
class TrackDetection:
    object_id: int
    bbox: list[float]
    score: float | None
    source: str = "sam3"
    mask_area: int | None = None
    sam3_object_id: int | None = None


def _as_numpy(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    try:
        if hasattr(value, "detach"):
            value = value.detach().cpu()
        if hasattr(value, "numpy"):
            value = value.numpy()
        return np.asarray(value)
    except Exception:
        return None


def read_video(
    video_path: str | Path,
    max_frames: int | None = None,
    target_fps: float | None = None,
) -> tuple[list[Image.Image], dict[str, Any]]:
    """Read RGB PIL frames using the same global sampling idea as 01_test.

    When target_fps is lower than source FPS, frames are sampled at a fixed
    source-frame interval. source_frame_indices maps each sampled frame back
    to the original video frame number.
    """
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {path}")

    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    requested_fps = float(target_fps or 0.0)
    if requested_fps > 0 and source_fps > 0 and requested_fps < source_fps:
        sample_interval = max(1, int(round(source_fps / requested_fps)))
    else:
        sample_interval = 1

    process_fps = source_fps / sample_interval if source_fps > 0 else requested_fps
    frames: list[Image.Image] = []
    source_frame_indices: list[int] = []

    source_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if source_idx % sample_interval == 0:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames.append(Image.fromarray(rgb))
                source_frame_indices.append(source_idx)
                if max_frames is not None and len(frames) >= max_frames:
                    break
            source_idx += 1
    finally:
        cap.release()

    if not frames:
        raise ValueError(f"Video contains no readable frames: {path}")

    return frames, {
        "name": path.name,
        "width": width,
        "height": height,
        "fps": process_fps,
        "source_fps": source_fps,
        "frameCount": frame_count or source_idx + 1,
        "sample_interval": sample_interval,
        "source_frame_indices": source_frame_indices,
    }


class Sam3Engine:
    """The SAM3 engine used by the validated 01_test pipeline.

    The tracker model and processor are loaded only once per FastAPI process.
    Every tracking request gets a fresh inference session, just like 01_test.
    """

    def __init__(self, model_id: str, device: str, dtype: str):
        self.model_id = self.resolve_model_id(model_id)
        self.device = self._resolve_device(device)
        self.torch_dtype = self._resolve_dtype(dtype)
        self.tracker_model = None
        self.tracker_processor = None
        self._load_lock = Lock()

    @staticmethod
    def resolve_model_id(model_id: str | None) -> str:
        requested = (model_id or os.getenv("SAM3_MODEL_ID") or "").strip()
        project_root = Path(__file__).resolve().parents[2]
        candidates: list[Path] = []
        if requested and Path(requested).is_dir():
            candidates.append(Path(requested).expanduser())
        candidates.extend(
            [
                project_root / "track_modul" / "facebook--sam3" / "snapshots" / "master",
                project_root / "track_modul" / "facebook--sam3",
                project_root / "models" / "sam3",
            ]
        )
        for candidate in candidates:
            if candidate.is_dir() and (candidate / "config.json").exists():
                return str(candidate.resolve())
        return requested or "facebook/sam3"

    @staticmethod
    def _resolve_device(device: str) -> torch.device:
        value = str(device).lower().strip()
        if value in {"gpu", "cuda", "cuda:0"}:
            if not torch.cuda.is_available():
                raise RuntimeError("SAM3_DEVICE=cuda, but CUDA is not available")
            return torch.device("cuda")
        return torch.device(value)

    @staticmethod
    def _resolve_dtype(dtype: str) -> torch.dtype:
        value = str(dtype).lower().replace("torch.", "")
        if value in {"bfloat16", "bf16"}:
            if torch.cuda.is_available() and hasattr(torch.cuda, "is_bf16_supported"):
                if not torch.cuda.is_bf16_supported():
                    print("[sam3] bfloat16 not supported; falling back to float16")
                    return torch.float16
            return torch.bfloat16
        if value in {"float16", "fp16", "half"}:
            return torch.float16
        return torch.float32

    def _pretrained_kwargs(self) -> dict[str, Any]:
        if Path(str(self.model_id)).is_dir():
            return {"local_files_only": True}
        token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
        return {"token": token} if token else {}

    def load_tracker(self) -> None:
        if self.tracker_model is not None and self.tracker_processor is not None:
            return
        with self._load_lock:
            if self.tracker_model is not None and self.tracker_processor is not None:
                return
            from transformers import Sam3TrackerVideoModel, Sam3TrackerVideoProcessor

            kwargs = self._pretrained_kwargs()
            print(f"[sam3] loading tracker from: {self.model_id}")
            print(f"[sam3] device={self.device}, dtype={self.torch_dtype}")
            self.tracker_model = Sam3TrackerVideoModel.from_pretrained(self.model_id, **kwargs).to(
                self.device, dtype=self.torch_dtype
            )
            self.tracker_processor = Sam3TrackerVideoProcessor.from_pretrained(self.model_id, **kwargs)
            self.tracker_model.eval()
            print("[sam3] tracker loaded once and cached.")

    @property
    def model(self):
        return self.tracker_model

    @property
    def processor(self):
        return self.tracker_processor

    def make_tracker_session(self, frames: list[Image.Image]):
        self.load_tracker()
        return self.tracker_processor.init_video_session(
            video=frames,
            inference_device=self.device,
            processing_device="cpu",
            inference_state_device="cpu",
            video_storage_device="cpu",
            dtype=self.torch_dtype,
            max_vision_features_cache_size=1,
        )

    def add_manual_boxes(self, session, frame_index: int, objects: list[dict[str, Any]]) -> None:
        self.load_tracker()
        if not objects:
            raise ValueError("No manual objects were supplied")
        ids = [int(o["object_id"]) for o in objects]
        boxes = [[float(v) for v in o["bbox"]] for o in objects]
        self.tracker_processor.add_inputs_to_inference_session(
            inference_session=session,
            frame_idx=int(frame_index),
            obj_ids=ids,
            input_boxes=[boxes],
        )

    def propagate_manual(self, session, max_frames: int, start_frame_idx: int):
        self.load_tracker()
        yield from self.tracker_model.propagate_in_video_iterator(
            inference_session=session,
            start_frame_idx=int(start_frame_idx),
            max_frame_num_to_track=int(max_frames),
            show_progress_bar=False,
        )

    def decode_tracker_output(self, session, output) -> tuple[list[TrackDetection], dict[int, np.ndarray]]:
        self.load_tracker()
        masks = self.tracker_processor.post_process_masks(
            [output.pred_masks],
            original_sizes=[[session.video_height, session.video_width]],
            binarize=True,
        )[0]
        masks_np = _as_numpy(masks)
        if masks_np is None:
            return [], {}
        masks_np = np.asarray(masks_np)
        if masks_np.ndim == 4 and masks_np.shape[1] == 1:
            masks_np = masks_np[:, 0]

        ids = [int(x) for x in getattr(session, "obj_ids", [])]
        scores_np = _as_numpy(getattr(output, "object_score_logits", None))
        if scores_np is not None:
            scores_np = np.asarray(scores_np).reshape(-1)
            scores_np = 1.0 / (1.0 + np.exp(-scores_np))

        detections: list[TrackDetection] = []
        mask_map: dict[int, np.ndarray] = {}
        for i, object_id in enumerate(ids):
            mask = masks_np[i] if i < len(masks_np) else None
            if mask is None:
                continue
            ys, xs = np.where(mask > 0.5)
            if len(xs) == 0:
                continue
            bbox = [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)]
            score = float(scores_np[i]) if scores_np is not None and i < len(scores_np) else None
            detections.append(
                TrackDetection(
                    object_id=object_id,
                    bbox=bbox,
                    score=score,
                    mask_area=int(mask.sum()),
                    sam3_object_id=object_id,
                )
            )
            mask_map[object_id] = mask
        return detections, mask_map

    @staticmethod
    def source_to_sampled_index(source_frame: int, source_indices: list[int]) -> int:
        if not source_indices:
            return source_frame
        return min(range(len(source_indices)), key=lambda i: abs(int(source_indices[i]) - source_frame))


_ENGINE: Sam3Engine | None = None
_ENGINE_LOCK = Lock()


def get_sam3_engine(model_id: str, device: str, dtype: str) -> Sam3Engine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = Sam3Engine(model_id, device, dtype)
        return _ENGINE
