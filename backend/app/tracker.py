from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
from PIL import Image

from .config import DEVICE, DTYPE, MODEL_ID
from .services.sam3_engine import get_sam3_engine, read_video
from .services.visualization import draw_frame, open_video_writer


RESULT_FILE_NAME = "tracker_results.json"
OVERLAY_FILE_NAME = "tracker_overlay.mp4"


def _json_bbox(values: list[float]) -> list[float]:
    return [round(float(v), 3) for v in values]


def _load_seed(
    annotation_json: str,
    width: int,
    height: int,
    source_frame_count: int,
) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
    data = json.loads(Path(annotation_json).read_text(encoding="utf-8"))
    frame = data.get("frame", {})
    source_start = int(frame.get("frameIndex", 0))
    if source_start < 0 or source_start >= source_frame_count:
        raise ValueError(f"seed frame {source_start} outside source video range")

    objects: list[dict[str, Any]] = []
    for i, ann in enumerate(data.get("annotations", []), start=1):
        ann_frame = int(ann.get("frameIndex", source_start))
        if ann_frame != source_start:
            raise ValueError(
                f"annotation {ann.get('id')} is on frame {ann_frame}, expected {source_start}"
            )
        bbox = ann.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue

        x1, y1, x2, y2 = [float(v) for v in bbox]
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        x1 = max(0.0, min(float(max(0, width - 1)), x1))
        x2 = max(0.0, min(float(width), x2))
        y1 = max(0.0, min(float(max(0, height - 1)), y1))
        y2 = max(0.0, min(float(height), y2))
        if x2 <= x1 or y2 <= y1:
            continue

        try:
            object_id = int(ann.get("object_id", i))
        except Exception:
            object_id = i

        objects.append(
            {
                "object_id": object_id,
                "source_id": ann.get("id", f"object-{i}"),
                "id": ann.get("id", f"object-{i}"),
                "name": ann.get("name", "object"),
                "label": ann.get("name", "object"),
                "bbox": [x1, y1, x2, y2],
            }
        )

    if not objects:
        raise ValueError("No manual bbox annotations found")

    return data, source_start, objects


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def track_video(
    video_path: str,
    annotation_json: str,
    output_json: str,
    max_frames: int,
    bbox_mode: str = "pixel",
    start_frame: int | None = None,
) -> dict[str, Any]:
    """Run SAM3 on the exact original source-frame sequence.

    The persisted result is JSONL with one frame object per line, matching the
    supplied tracker_results.json structure. SAM3 frame_index and source_frame_index
    are intentionally identical in raw-frame mode.
    """
    if bbox_mode != "pixel":
        raise ValueError("FastAPI tracker expects pixel bbox input")
    if max_frames < 1:
        raise ValueError("max_frames must be >= 1")

    video_file = Path(video_path)
    cap_meta = _probe_video(video_file)
    width = cap_meta["width"]
    height = cap_meta["height"]
    source_frame_count = cap_meta["frameCount"]
    source_fps = cap_meta["fps"]

    source_json, seed_source_frame, objects = _load_seed(
        annotation_json,
        width,
        height,
        source_frame_count,
    )
    requested_source_start = seed_source_frame if start_frame is None else int(start_frame)
    if requested_source_start != seed_source_frame:
        raise ValueError("startFrame must match the annotation frameIndex")

    # Raw-frame mode: keep the exact source frame sequence.
    # This deliberately uses the original FPS and original frame indices so
    # SAM3, tracker_results.json and the browser all share one timeline.
    frames, meta = read_video(video_file, target_fps=None)
    source_indices = [int(x) for x in meta.get("source_frame_indices", [])]
    if not frames or not source_indices:
        raise ValueError("No source frames available")

    # In raw-frame mode the source frame index is the SAM3 session index.
    seed_frame = seed_source_frame
    if seed_frame >= len(frames):
        raise ValueError(f"Seed frame {seed_frame} outside decoded video")

    available = len(frames) - seed_frame
    requested = min(int(max_frames), available)
    if requested <= 0:
        raise ValueError("No frames remain from selected start frame")

    engine = get_sam3_engine(MODEL_ID, DEVICE, DTYPE)

    # IMPORTANT for a 4 GB GPU: preprocessing/storage stay on CPU, while the
    # actual SAM3 inference model remains on CUDA. These are the same controls
    # used by the validated standalone backend.
    session = engine.make_tracker_session(frames)
    engine.add_manual_boxes(session, seed_frame, objects)

    # object_id → name 映射，让 tracking 结果继承用户标注的名字
    object_names: dict[int, str] = {int(o["object_id"]): o.get("name") or f"object-{o['object_id']}" for o in objects}

    new_rows: list[dict[str, Any]] = []
    for output in engine.propagate_manual(
        session,
        max_frames=requested,
        start_frame_idx=seed_frame,
    ):
        frame_idx = int(output.frame_idx)
        if frame_idx <= seed_frame or frame_idx >= len(source_indices):
            continue

        detections, _ = engine.decode_tracker_output(session, output)
        source_idx = source_indices[frame_idx]

        object_rows: list[dict[str, Any]] = []
        for detection in detections:
            bbox = _json_bbox(detection.bbox)
            oid = int(detection.object_id)
            obj_row = {
                "object_id": oid,
                "name": object_names.get(oid, f"object-{oid}"),
                "bbox": bbox,
                "score": round(float(detection.score), 15)
                if detection.score is not None
                else None,
                "source": "manual_sam3_tracker",
                "mask_area": int(detection.mask_area)
                if detection.mask_area is not None
                else None,
                "sam3_object_id": int(detection.sam3_object_id)
                if detection.sam3_object_id is not None
                else oid,
            }
            object_rows.append(obj_row)

        row = {
            "frame_index": frame_idx,
            "source_frame_index": source_idx,
            "objects": object_rows,
        }
        new_rows.append(row)
        print(
            f"[sam3] frame={frame_idx} source={source_idx} "
            f"tracked_objects={len(object_rows)}"
        )

    merged_rows = _merge_rows(Path(output_json), new_rows)
    overlay_path = Path(output_json).parent / OVERLAY_FILE_NAME
    _render_overlay_video(video_file, overlay_path, meta, merged_rows)

    result = {
        "frames": merged_rows,
        "resultFile": str(Path(output_json).resolve()),
        "overlayVideo": str(overlay_path.resolve()),
        "startFrame": requested_source_start,
        "requestedFrames": requested,
        "processedFrames": len(new_rows),
        "sourceFps": source_fps,
        "processFps": source_fps,
        "sampleInterval": 1,
        "sourceFrameIndices": source_indices,
        "model": engine.model_id,
        "device": str(engine.device),
        "dtype": str(engine.torch_dtype).replace("torch.", ""),
        "media": {
            "name": video_file.name,
            "width": width,
            "height": height,
            "fps": source_fps,
            "frameCount": source_frame_count,
        },
    }
    print(f"[sam3] result jsonl: {output_json}")
    print(f"[sam3] overlay mp4: {overlay_path}")
    return result


def _merge_rows(path: Path, new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        try:
            key = int(row.get("source_frame_index", row.get("frame_index", -1)))
        except Exception:
            continue
        if key >= 0:
            merged[key] = row

    for row in new_rows:
        key = int(row["source_frame_index"])
        merged[key] = row

    ordered = [merged[key] for key in sorted(merged)]
    _write_jsonl(path, ordered)
    return ordered


def _render_overlay_video(
    source_video: Path,
    output_path: Path,
    source_meta: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    """Write a full-length overlay MP4 at the original source FPS.

    Every original frame is decoded in order. Frames with a tracking row are
    rendered with boxes; other frames are copied unchanged. This keeps the
    overlay video on exactly the same frame/time axis as the browser source.
    """
    frame_map = {
        int(row.get("source_frame_index", row.get("frame_index", -1))): row
        for row in rows
        if int(row.get("source_frame_index", row.get("frame_index", -1))) >= 0
    }

    cap = cv2.VideoCapture(str(source_video))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {source_video}")

    width = int(source_meta.get("width") or cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(source_meta.get("height") or cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(source_meta.get("fps") or cap.get(cv2.CAP_PROP_FPS) or 30.0)
    meta = {"width": width, "height": height, "fps": fps}

    writer = None
    frame_idx = 0
    try:
        writer = open_video_writer(output_path, meta)
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            row = frame_map.get(frame_idx)
            objects = []
            if row:
                for obj in row.get("objects", []):
                    objects.append({
                        "track_id": obj.get("object_id", 0),
                        "object_id": obj.get("object_id", 0),
                        "bbox": obj.get("bbox"),
                        "score": obj.get("score"),
                        "source": obj.get("source", "manual_sam3_tracker"),
                    })

            rendered = draw_frame(
                image,
                objects,
                title=f"SAM3 TRACKER | frame {frame_idx}",
            )
            writer.write(rendered)
            frame_idx += 1
    finally:
        cap.release()
        if writer is not None:
            writer.release()

def _probe_video(path: Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {path}")
    try:
        return {
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
            "frameCount": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
            "fps": float(cap.get(cv2.CAP_PROP_FPS) or 0.0),
        }
    finally:
        cap.release()


def get_tracker_engine():
    return get_sam3_engine(MODEL_ID, DEVICE, DTYPE)
