from __future__ import annotations

import json
import logging
import math
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
from PIL import Image

from .config import DEVICE, DTYPE, MODEL_ID
from .annotation_state import is_deleted, read_state, remember_tracking_results
from .review_source_lock import source_write
from .services.anomaly_detector import (
    AnomalyConfig,
    AnomalyDetector,
    AnomalyLevel,
    ConfirmedGeometryReference,
    ManualBaseline,
)
from .services.sam3_engine import get_sam3_engine, SourceVideoWindow
from .services.visualization import draw_frame, open_video_writer


RESULT_FILE_NAME = "tracker_results.json"
OVERLAY_FILE_NAME = "tracker_overlay.mp4"
WORKSPACE_STATE_FILE_NAME = "workspace_state.json"


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

    annotations = data.get("annotations", [])
    if not isinstance(annotations, list):
        raise ValueError("seed annotation JSON annotations must be a list")

    objects: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for idx, ann in enumerate(annotations):
        if not isinstance(ann, dict):
            raise ValueError(f"annotations[{idx}] must be an object")
        ann_frame = int(ann.get("frameIndex", source_start))
        if ann_frame != source_start:
            raise ValueError(
                f"annotation {ann.get('id')} is on frame {ann_frame}, expected {source_start}"
            )
        bbox = ann.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue

        object_id_raw = ann.get("object_id", ann.get("objectId"))
        if object_id_raw is None:
            raise ValueError(
                f"annotation {ann.get('id', idx)} is missing object_id; "
                "identity must be persisted explicitly and never derived from array order"
            )
        try:
            object_id = int(object_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"annotation {ann.get('id', idx)} has invalid object_id={object_id_raw!r}") from exc
        if object_id <= 0:
            raise ValueError(f"annotation {ann.get('id', idx)} has non-positive object_id={object_id}")
        if object_id in seen_ids:
            raise ValueError(f"duplicate object_id={object_id} in seed annotations")
        seen_ids.add(object_id)

        try:
            x1, y1, x2, y2 = [float(v) for v in bbox]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"annotation {ann.get('id', idx)} bbox contains non-numeric values") from exc
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        x1 = max(0.0, min(float(max(0, width - 1)), x1))
        x2 = max(0.0, min(float(width), x2))
        y1 = max(0.0, min(float(max(0, height - 1)), y1))
        y2 = max(0.0, min(float(height), y2))
        if x2 <= x1 or y2 <= y1:
            continue

        objects.append(
            {
                "object_id": object_id,
                "source_id": ann.get("id", f"object-{object_id}"),
                "id": ann.get("id", f"object-{object_id}"),
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
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        import os
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        from .tracking_restart import _sync_directory
        _sync_directory(path.parent)
        remember_tracking_results(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _clean_history_before_seed(
    output_json: Path,
    seed_source_frame: int,
    rows: list[dict[str, Any]] | None = None,
) -> list[tuple[int, dict[int, list[float]]]]:
    """Return prior clean boxes for the long-term anomaly baseline.

    Results at the new seed frame and later will be replaced by this tracking
    request, so they must not affect the baseline.  Previously persisted HARD
    anomalies are also excluded: they are exactly the oversized boxes that the
    detector must not learn as a normal object size.
    """
    frames: list[tuple[int, dict[int, list[float]]]] = []
    for row in rows if rows is not None else _read_jsonl(output_json):
        try:
            source_frame = int(row.get("source_frame_index", row.get("frame_index", -1)))
        except (TypeError, ValueError):
            continue
        if source_frame < 0 or source_frame >= int(seed_source_frame):
            continue

        objects: dict[int, list[float]] = {}
        for obj in row.get("objects", []):
            if not isinstance(obj, dict) or obj.get("anomaly_level") in {
                AnomalyLevel.ANOMALY.value,
                AnomalyLevel.DISAPPEARED.value,
            }:
                continue
            bbox = obj.get("bbox")
            try:
                if not isinstance(bbox, list) or len(bbox) != 4:
                    continue
                clean_bbox = [float(v) for v in bbox]
                if clean_bbox[2] <= clean_bbox[0] or clean_bbox[3] <= clean_bbox[1]:
                    continue
                objects[int(obj["object_id"])] = clean_bbox
            except (KeyError, TypeError, ValueError):
                continue
        if objects:
            frames.append((source_frame, objects))
    return sorted(frames, key=lambda item: item[0])


def _load_latest_manual_baselines(
    seed_path: Path,
    seed_source_frame: int,
    *, include_checkpoints: bool = True,
) -> dict[int, ManualBaseline]:
    """Restore each object's latest manual bbox/checkpoint up to the seed frame.

    ``annotations_frame_*.json`` is durable per-media state, so this works for
    resume requests and after a backend restart.  The current ``seed_frame`` is
    also considered for direct API clients.  An AI/warning/anomaly box is never
    accepted merely by reusing it as a SAM3 seed. Explicit frame checkpoints
    are separately restored from server-owned feedback, retaining provenance.
    """
    candidates = list(seed_path.parent.glob("annotations_frame_*.json"))
    if seed_path not in candidates:
        candidates.append(seed_path)

    latest: dict[int, ManualBaseline] = {}
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            frame_index = int(payload.get("frame", {}).get("frameIndex", -1))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if frame_index < 0 or frame_index > int(seed_source_frame):
            continue

        for annotation in payload.get("annotations", []):
            if not isinstance(annotation, dict) or annotation.get("source") != "manual":
                continue
            try:
                object_id = int(annotation.get("object_id", annotation.get("objectId")))
                bbox = [float(value) for value in annotation["bbox"]]
            except (KeyError, TypeError, ValueError):
                continue
            if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            previous = latest.get(object_id)
            if previous is None or frame_index >= previous.frame_index:
                latest[object_id] = ManualBaseline(
                    bbox=bbox,
                    frame_index=frame_index,
                    name=annotation.get("name") or None,
                )

    # workspace_state.json is written whenever the browser changes a manual
    # box.  It can therefore be newer than the last explicit Tracking seed.
    # Only entries explicitly marked manual are accepted; AI predictions can
    # never become a baseline merely by being persisted in the workspace.
    workspace_file = seed_path.parent / WORKSPACE_STATE_FILE_NAME
    try:
        workspace = json.loads(workspace_file.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        workspace = {}
    for item in workspace.get("manualBaselines", []):
        if not isinstance(item, dict) or item.get("source") != "manual":
            continue
        try:
            object_id = int(item.get("objectId", item.get("object_id")))
            frame_index = int(item.get("frameIndex", item.get("frame_index", -1)))
            bbox = [float(value) for value in item["bbox"]]
        except (KeyError, TypeError, ValueError):
            continue
        if frame_index < 0 or frame_index > int(seed_source_frame):
            continue
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        previous = latest.get(object_id)
        if previous is None or frame_index >= previous.frame_index:
            latest[object_id] = ManualBaseline(
                bbox=bbox,
                frame_index=frame_index,
                name=item.get("name") or None,
            )
    if not include_checkpoints:
        return latest
    # An explicit whole-frame inspection replaces the effective baseline, but
    # remains a separate human decision instead of forging source=manual data.
    checkpoints: dict[int, dict[str, Any]] = {}
    for event in workspace.get('trackingFeedbackEvents', []):
        if not isinstance(event, dict):
            continue
        oid, frame = event.get('objectId'), event.get('frameIndex')
        if event.get('decision') == 'reset':
            checkpoints.pop(oid, None)
            continue
        if type(frame) is not int or not 0 <= frame <= seed_source_frame:
            continue
        if event.get('decision') == 'corrected':
            checkpoints.pop(oid, None)
            continue
        if event.get('decision') == 'normal' and isinstance(event.get('geometryReference'), dict) and event['geometryReference'].get('checkpoint') is True:
            if type(oid) is not int or oid <= 0:
                raise ValueError('人工检查点损坏，未继续追踪')
            prior = checkpoints.get(oid)
            if prior is None or frame >= prior['frameIndex']:
                checkpoints[oid] = event
    for oid, event in checkpoints.items():
        frame = event['frameIndex']
        if is_deleted(workspace, frame, oid) or is_deleted(workspace, seed_source_frame, oid):
            continue
        baseline = latest.get(oid)
        # Real edits at this frame or later supersede the inspected geometry.
        if baseline and baseline.frame_index >= frame:
            continue
        ref = event.get('geometryReference')
        if not isinstance(ref, dict) or ref.get('frameIndex') != frame or ref.get('objectId') != oid or ref.get('source') != 'confirmed-normal' or ref.get('checkpoint') is not True:
            raise ValueError('人工检查点身份损坏，未继续追踪')
        bbox = _valid_reference_bbox(ref.get('bbox'))
        if bbox is None:
            raise ValueError('人工检查点框损坏，未继续追踪')
        area = ref.get('maskArea')
        area = area if type(area) in (int, float) and math.isfinite(area) and area > 0 else None
        latest[oid] = ManualBaseline(bbox, frame, ref.get('name'), 'confirmed-normal', area)
    return latest


def _load_identity_references(workspace, seed_source_frame, manual_baselines):
    """Identity state has independent provenance; old v1 peaks are never trust.

    Reset/correction, deleted objects, future events and a newer real manual
    edit invalidate a compact optical identity. Shape-only normal acceptance
    does not erase its reliable original subject.
    """
    latest = {}
    generation = workspace.get('generationId')
    retired = set(workspace.get('_retiredTrackingRelationshipEventIds', []))
    for event in workspace.get('trackingFeedbackEvents', []):
        if not isinstance(event,dict):
            continue
        if event.get('id') in retired:
            continue
        oid,frame = event.get('objectId'),event.get('frameIndex')
        if type(oid) is not int or oid <= 0:
            continue
        if event.get('decision') == 'reset':
            latest.pop(oid,None); continue
        if type(frame) is not int or not 0 <= frame <= seed_source_frame:
            continue
        if event.get('decision') == 'corrected':
            latest.pop(oid,None); continue
        value = event.get('identityReference')
        if event.get('decision') != 'normal' or not isinstance(value,dict) or value.get('version') != 2 or value.get('status') != 'reliable' or value.get('objectId') != oid or value.get('frameIndex') != frame:
            continue
        if generation is not None and value.get('generationId') != generation:
            continue
        epochs = workspace.get('_overlapReferenceEpochs') or {}
        current_epoch = epochs.get(str(oid),0) if isinstance(epochs,dict) else None
        if type(current_epoch) is not int or value.get('objectEpoch',0) != current_epoch:
            continue
        manual = manual_baselines.get(oid)
        if manual and manual.source == 'manual' and manual.frame_index > frame:
            continue
        if manual and manual.source == 'manual' and manual.frame_index == frame and _valid_reference_bbox(value.get('bbox')) != list(manual.bbox):
            continue
        if is_deleted(workspace,frame,oid) or is_deleted(workspace,seed_source_frame,oid):
            continue
        previous = latest.get(oid)
        if previous is None or value['frameIndex'] >= previous['frameIndex']:
            latest[oid] = value
    return latest


def _load_confirmed_geometry_references(
    workspace: dict[str, Any],
    seed_source_frame: int,
    manual_baselines: dict[int, ManualBaseline],
) -> dict[int, list[ConfirmedGeometryReference]]:
    """Restore explicit shape acceptance without changing manual provenance.

    Events are append-only server records. Reset/correction ends the previous
    acceptance, and neither future feedback nor a newer manual box may supply
    the geometry reference for this tracking branch.
    """
    latest: dict[int, list[ConfirmedGeometryReference]] = {}
    for event in workspace.get("trackingFeedbackEvents", []):
        if not isinstance(event, dict):
            continue
        oid = event.get("objectId")
        if type(oid) is not int or oid <= 0:
            continue
        if event.get("decision") == "reset":
            latest.pop(oid, None)
            continue
        frame = event.get("frameIndex")
        if type(frame) is not int or not 0 <= frame <= seed_source_frame:
            continue
        if event.get("decision") == "corrected":
            latest.pop(oid, None)
            continue
        reference = event.get("geometryReference")
        if event.get("decision") != "normal" or not isinstance(reference, dict):
            continue
        if reference.get("source") != "confirmed-normal" or reference.get("objectId") != oid or reference.get("frameIndex") != frame:
            continue
        if is_deleted(workspace, frame, oid) or is_deleted(workspace, seed_source_frame, oid):
            continue
        bbox = _valid_reference_bbox(reference.get("bbox"))
        if bbox is None:
            continue
        manual = manual_baselines.get(oid)
        if manual and manual.frame_index >= frame:
            continue
        mask_area = reference.get("maskArea")
        if type(mask_area) not in (int, float) or not math.isfinite(mask_area) or mask_area <= 0:
            mask_area = None
        accepted = latest.setdefault(oid, [])
        # The complete latest 3 explicit shapes are candidates. We never form
        # an oversized range by mixing width from one reference with height
        # from another, nor let ordinary AI rows silently teach normality.
        accepted.append(ConfirmedGeometryReference(bbox, frame, mask_area=mask_area, event_id=event.get("id")))
        accepted.sort(key=lambda item: item.frame_index)
        latest[oid] = accepted[-3:]
    return latest


def _valid_reference_bbox(raw_bbox: Any) -> list[float] | None:
    if (not isinstance(raw_bbox, list) or len(raw_bbox) != 4
            or any(type(value) not in (int, float) for value in raw_bbox)):
        return None
    try:
        bbox = [float(value) for value in raw_bbox]
    except (ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in bbox) or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _load_confirmed_overlap_references(
    workspace: dict[str, Any], seed_source_frame: int,
    manual_baselines: dict[int, ManualBaseline],
) -> dict[tuple[int, int], Any]:
    """Restore explicit pair approvals only for the current media/branch.

    The reference can make an unchanged overlap a warning; it is never an
    object-wide exemption. New manual provenance/reset/correction invalidates
    that object's pairs, while future approvals cannot affect earlier frames.
    """
    from .services.anomaly_detector import OverlapReference
    from .annotation_state import _pair_snapshot

    events_by_id = {event.get('id'): event for event in workspace.get('trackingFeedbackEvents', [])
                    if isinstance(event,dict) and isinstance(event.get('id'),str)}
    retired = set(workspace.get('_retiredTrackingRelationshipEventIds', []))

    references: dict[tuple[int, int], OverlapReference] = {}
    for event in workspace.get("trackingFeedbackEvents", []):
        if not isinstance(event, dict):
            continue
        if event.get('id') in retired:
            continue
        oid = event.get("objectId")
        if type(oid) is not int or oid <= 0:
            continue
        decision = event.get("decision")
        frame = event.get("frameIndex")
        if decision == "reset":
            references = {pair: ref for pair, ref in references.items() if oid not in pair}
            continue
        if type(frame) is not int or not 0 <= frame <= seed_source_frame:
            continue
        if decision == "corrected":
            references = {pair: ref for pair, ref in references.items() if oid not in pair}
            continue
        entries = event.get("overlapReferences")
        if decision != "normal" or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("source") != "confirmed-normal" or entry.get("frameIndex") != frame:
                continue
            ids = entry.get("objectIds")
            if (not isinstance(ids, list) or len(ids) != 2
                    or any(type(value) is not int or value <= 0 for value in ids)
                    or ids[0] == ids[1] or oid not in ids):
                continue
            if entry.get('approvalVersion') == 2:
                approved_ids, event_ids = entry.get('confirmedObjectIds'), entry.get('confirmedEventIds')
                if (entry.get('generationId') != workspace.get('generationId') or approved_ids != sorted(ids)
                        or not isinstance(event_ids,list) or len(event_ids)!=2 or len(set(event_ids))!=2
                        or not isinstance(entry.get('pauseId'),str) or not entry['pauseId']):
                    continue
                epochs = workspace.get('_overlapReferenceEpochs') or {}
                saved_epochs = entry.get('objectEpochs') or {}
                if (not isinstance(epochs,dict) or not isinstance(saved_epochs,dict)
                        or any(saved_epochs.get(str(identity),0) != epochs.get(str(identity),0) for identity in ids)):
                    continue
                owners = [events_by_id.get(event_id) for event_id in event_ids]
                if any(event_id in retired for event_id in event_ids) or any(not isinstance(owner,dict) or owner.get('objectId') != object_id
                       or owner.get('decision') != 'normal' or owner.get('frameIndex') != frame
                       or owner.get('pauseId') != entry['pauseId']
                       or owner.get('pairSnapshots',{}).get(str(other)) != entry.get('pairSnapshotId')
                       for owner,object_id,other in zip(owners,approved_ids,reversed(approved_ids))):
                    continue
            elif any((workspace.get('_overlapReferenceEpochs') or {}).get(str(identity),0) != 0 for identity in ids):
                continue
            if any(is_deleted(workspace, fi, identity) for identity in ids for fi in (frame, seed_source_frame)):
                continue
            if any(identity in manual_baselines and (manual_baselines[identity].frame_index > frame or
                   (manual_baselines[identity].frame_index == frame and manual_baselines[identity].source == 'manual')) for identity in ids):
                continue
            boxes = entry.get("bboxes")
            if not isinstance(boxes, list) or len(boxes) != 2:
                continue
            valid_boxes = [_valid_reference_bbox(box) for box in boxes]
            if any(box is None for box in valid_boxes):
                continue
            if entry.get('approvalVersion') == 2 and entry.get('pairSnapshotId') != _pair_snapshot(
                    workspace,frame,entry['pauseId'],ids,valid_boxes):
                continue
            pair = tuple(sorted(ids))
            prior = references.get(pair)
            if prior is None or frame >= prior.frame_index:
                references[pair] = OverlapReference(pair=pair, boxes=dict(zip(ids, valid_boxes)),
                                                    frame_index=frame, event_id=event.get("id"))
    return references


def rewind_tracking_results(
    path: Path,
    video_file: Path,
    cutoff_frame: int,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drop only stale future SAM3 rows *after* ``cutoff_frame``.

    Frame N is the user's new authoritative branch point. It must be kept so
    existing AI boxes on that frame can be combined with human edits/additions
    and then submitted as the next SAM3 seed. Rows strictly before N are also
    retained. The replacement tracking run will upsert/replace frame N when its
    new result is written.
    """
    began = time.perf_counter()
    cutoff = int(cutoff_frame)
    if cutoff < 0:
        raise ValueError("cutoff_frame must be >= 0")

    if path.name == 'tracker_results.json' and (path.parent / WORKSPACE_STATE_FILE_NAME).is_file():
        # Source relationships and discarded output must move together. The
        # normal endpoint preserves the workspace revision; its journal still
        # guarantees that a failed publication restores both old files.
        from .tracking_restart import rewind
        return rewind(path.parent, cutoff)

    rows = _read_jsonl(path)
    kept: list[dict[str, Any]] = []
    removed = 0
    for row in rows:
        try:
            frame = int(row.get("source_frame_index", row.get("frame_index", -1)))
        except Exception:
            continue
        if frame <= cutoff:
            kept.append(row)
        else:
            removed += 1

    if removed:
        _write_jsonl(path, kept)
        (path.parent / OVERLAY_FILE_NAME).unlink(missing_ok=True)

    # Annotation JSON is user data and also the durable source of manual
    # baselines.  Rewinding tracking must never delete it; the next save simply
    # overwrites the selected frame if the user confirms a new annotation.
    deleted_seed_files = 0

    logging.getLogger('review.tracking').info('tracking.rewind media=%s cutoff=%s kept=%s removed=%s elapsed_ms=%.1f overlay_regenerated=false',
        path.parent.name, cutoff, len(kept), removed, (time.perf_counter() - began) * 1000)
    return {
        "cutoffFrame": cutoff,
        "keptRows": len(kept),
        "removedRows": removed,
        "deletedFutureSeedFiles": deleted_seed_files,
        "overlayRegenerated": False,
    }


def track_video(
    video_path: str,
    annotation_json: str,
    output_json: str,
    max_frames: int,
    bbox_mode: str = "pixel",
    start_frame: int | None = None,
    normal_feedback: list[dict[str, Any]] | None = None,
    progress=None,
    edge_discovery: bool = False,
    review_mode: bool = False,
    endpoint_mode: bool = False,
    continuation_row: dict | None = None,
) -> dict[str, Any]:
    """Run SAM3 on the exact original source-frame sequence.

    The persisted result is JSONL with one frame object per line, matching the
    supplied tracker_results.json structure. Published frame_index and
    source_frame_index match; local model indices are mapped before publication.
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
    if continuation_row is not None:
        if not review_mode:
            raise ValueError('AI continuation requires concentrated review mode')
        seed_source_frame = int(continuation_row['source_frame_index'])
        objects = [dict(o) for o in continuation_row['objects']]
    workspace = read_state(Path(annotation_json).parent)
    objects = [obj for obj in objects if not is_deleted(workspace, seed_source_frame, int(obj["object_id"]))]
    if not objects:
        raise ValueError("当前帧没有可追踪对象；已删除对象不会重新进入追踪")
    requested_source_start = seed_source_frame if start_frame is None else int(start_frame)
    if requested_source_start != seed_source_frame:
        raise ValueError("startFrame must match the annotation frameIndex")

    # Model indices are local to this bounded window; every external frame,
    # metric and seed stays on the original source timeline and pixel grid.
    requested = min(int(max_frames), source_frame_count - seed_source_frame)
    if progress: progress(stage='decoding', message='正在读取本轮原始帧')
    began = time.perf_counter()
    if progress: progress(stage='preparing_model', message='正在准备模型和本轮输入')
    engine = get_sam3_engine(MODEL_ID, DEVICE, DTYPE)
    with SourceVideoWindow(video_file, max_frames=requested, target_fps=None, start_frame=seed_source_frame) as window:
        session = engine.make_tracker_session(window)
        meta = window.meta
    source_indices = [int(x) for x in meta.get("source_frame_indices", [])]
    if not source_indices:
        raise ValueError("No source frames available")

    if source_indices != list(range(seed_source_frame, seed_source_frame + len(source_indices))):
        raise ValueError('Decoded window does not match the original source-frame sequence')
    seed_frame = 0
    requested = len(source_indices)
    if requested <= 0:
        raise ValueError("No frames remain from selected start frame")

    logging.getLogger('review.tracking').info('tracking.decoded media=%s start=%s frames=%s width=%s height=%s rgb_bytes=%s elapsed_ms=%.1f',
        video_file.parent.name, seed_source_frame, requested, width, height, requested * width * height * 3, (time.perf_counter() - began) * 1000)

    # IMPORTANT for a 4 GB GPU: preprocessing/storage stay on CPU, while the
    # actual SAM3 inference model remains on CUDA. These are the same controls
    # used by the validated standalone backend.
    engine.add_manual_boxes(session, seed_frame, objects)

    # Restore explicit human decisions for this media. AI history supplies
    # motion context only; accepted geometry comes solely from feedback.
    active_object_ids = {int(o["object_id"]) for o in objects}
    manual_baselines = {
        object_id: baseline
        for object_id, baseline in _load_latest_manual_baselines(Path(annotation_json), seed_source_frame, include_checkpoints=False).items()
        if object_id in active_object_ids
    }
    anomaly_config = AnomalyConfig()
    observation_history = max(anomaly_config.MOTION_HISTORY_FRAMES,
                              anomaly_config.GEOMETRY_PERSISTENCE_FRAMES,
                              anomaly_config.OVERLAP_PERSISTENCE_FRAMES,
                              anomaly_config.IMAGE_PERSISTENCE_FRAMES * 4,
                              anomaly_config.SHARED_CORE_PERSISTENCE_FRAMES)
    history_rows = [row for row in _read_jsonl(Path(output_json))
                    if max(0, seed_source_frame - observation_history) <= int(row.get("source_frame_index", row.get("frame_index", -1))) <= seed_source_frame]
    detector = AnomalyDetector(
        config=anomaly_config,
        fps=source_fps,
        frame_width=width,
        frame_height=height,
        all_object_ids=active_object_ids,
        manual_baselines=manual_baselines,
        normal_feedback=normal_feedback if normal_feedback is not None else workspace.get("normalMotionSamples", []),
        geometry_references=_load_confirmed_geometry_references(workspace, seed_source_frame, manual_baselines),
        overlap_references=_load_confirmed_overlap_references(workspace, seed_source_frame, manual_baselines),
    )
    # Source validation must precede historical observation replay. Otherwise
    # valid native rows cannot restore their longer persistent-risk windows.
    # Legacy engines still do not claim to have native image/Mask evidence.
    decode_regions = getattr(engine, 'decode_tracker_output_regions', None)
    if callable(decode_regions):
        from .video_frames import source_signature
        detector.image_source_signature = source_signature(video_file)
    from .tracking_pause_policy import require_confirmed_geometry
    try:
        require_confirmed_geometry(workspace, seed_source_frame, active_object_ids, manual_baselines,
                                   detector.geometry_references)
    except ValueError:
        logging.getLogger("review.tracking").exception("tracking.confirmed_reference_invalid media=%s seed=%s",
                                                      video_file.parent.name, seed_source_frame)
        raise
    # Old JSONL deliberately retains deleted boxes for undo. Only identities
    # in this run's seed may contribute history or disappearance expectations.
    detector.prime_history(
        (fi, {oid: box for oid, box in history.items()
              if oid in active_object_ids and not is_deleted(workspace, fi, oid)})
        for fi, history in _clean_history_before_seed(Path(output_json), seed_source_frame, history_rows)
    )
    logging.getLogger("review.tracking").info("tracking.active_objects seed_frame=%s active_ids=%s deleted_ids=%s",
             seed_source_frame, sorted(active_object_ids), workspace.get("deletedObjectIds", []))
    logging.getLogger("review.tracking").info("tracking.frame_checkpoints_loaded media=%s seed=%s objects=%s",
             video_file.parent.name, seed_source_frame,
             {oid: {'frame': baseline.frame_index, 'bbox': baseline.bbox}
              for oid, baseline in manual_baselines.items() if baseline.source == 'confirmed-normal'})

    # object_id → name 映射，让 tracking 结果继承用户标注的名字
    object_names: dict[int, str] = {int(o["object_id"]): o.get("name") or f"object-{o['object_id']}" for o in objects}

    # 把 Tracking 的 seed 帧也写入 tracker_results.json。
    # 以前这里直接跳过 seed frame，导致常见的第 0 帧标注根本不会进入结果文件。
    seed_source_idx = source_indices[seed_frame]
    seed_rows = [{
        "object_id": int(obj["object_id"]),
        "name": object_names.get(int(obj["object_id"]), obj.get("name") or f"object-{obj['object_id']}"),
        "bbox": _json_bbox(obj["bbox"]),
        "score": None,
        "source": "manual_seed",
        "mask_area": None,
        "sam3_object_id": int(obj["object_id"]),
    } for obj in objects]
    new_rows: list[dict[str, Any]] = [{
        "frame_index": seed_source_idx,
        "source_frame_index": seed_source_idx,
        "objects": seed_rows,
    }]

    if continuation_row is not None:
        # Preserve actual AI provenance and doubt evidence at the chunk boundary.
        new_rows[0] = dict(continuation_row)
    # 人工确认的 seed 作为可信历史写入，但不把它当作 AI 输出触发暂停。
    if continuation_row is None:
        detector.initialize_seed(seed_source_idx, {int(obj["object_id"]): list(obj["bbox"]) for obj in objects})
    else:
        detector.prime_history([(seed_source_idx, {int(obj['object_id']): list(obj['bbox']) for obj in objects})])
    observation_rows = [{**row, "objects": [obj for obj in row.get("objects", [])
                         if int(obj.get("object_id", 0)) in active_object_ids
                         and not is_deleted(workspace, int(row.get("source_frame_index", row.get("frame_index", -1))), int(obj.get("object_id", 0)))]}
                        for row in history_rows]
    detector.prime_observations(observation_rows, seed_source_idx,
                               {int(obj["object_id"]): list(obj["bbox"]) for obj in objects})
    logging.getLogger("review.tracking").info(
        "tracking.confirmed_references_loaded media=%s seed=%s geometry=%s overlap=%s",
        video_file.parent.name, seed_source_frame,
        {oid: [{"frame": ref.frame_index, "bbox": ref.bbox, "maskArea": ref.mask_area}
               for ref in refs] for oid, refs in detector.geometry_references.items()},
        {str(pair): {"frame": ref.frame_index, "boxes": ref.boxes} for pair, ref in detector.overlap_references.items()},
    )

    result_anomaly_paused = None
    warning_summary: dict[tuple[int, str], dict[str, Any]] = {}
    last_processed_frame = seed_source_idx
    analyzed_through_frame = seed_source_idx
    end_frame_exclusive = seed_frame + requested
    # Only the actual engine exposes native mask ROIs. Legacy/test engines
    # retain the existing BBox fallback without claiming image verification.
    from contextlib import nullcontext
    evidence_context = nullcontext(None)
    if callable(decode_regions):
        from .services.tracking_image_evidence import NativeImageEvidence
        image_baselines = dict(manual_baselines)
        for oid, refs in detector.geometry_references.items():
            for ref in refs:
                prior = image_baselines.get(oid)
                if ref.frame_index <= seed_source_idx and (prior is None or ref.frame_index > prior.frame_index):
                    image_baselines[oid] = ManualBaseline(ref.bbox, ref.frame_index, source='confirmed-normal')
        evidence_context = NativeImageEvidence(video_file, fps=source_fps, width=width, height=height,
                                              manual_baselines=image_baselines, seed_frame=seed_source_idx,
                                              identity_baselines=_load_latest_manual_baselines(Path(annotation_json), seed_source_idx, include_checkpoints=False),
                                              identity_references=_load_identity_references(workspace, seed_source_idx,
                                                  _load_latest_manual_baselines(Path(annotation_json), seed_source_idx, include_checkpoints=False)))
    if progress: progress(stage='tracking', message='正在追踪', processedFrames=1, lastProcessedFrame=seed_source_idx)
    from .edge_entry import EntryPropagation, EdgeReader
    outputs = EntryPropagation(engine, session, requested)
    edge_reader = None
    old_rows = _read_jsonl(Path(output_json)) if edge_discovery else []
    # IDs reserved by manual annotations (including future edits), tombstones,
    # seed files and historical results must never be reused by discovery.
    reserved_ids = set(active_object_ids)
    def reserve(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('objectId', 'object_id') and isinstance(item, int): reserved_ids.add(item)
                elif key in ('deletedObjectIds', 'deletedTrackingIds') and isinstance(item, list):
                    reserved_ids.update(int(v) for v in item if str(v).isdigit())
                else: reserve(item)
        elif isinstance(value, list):
            for item in value: reserve(item)
    if edge_discovery:
        reserve(workspace); reserve(old_rows)
        for path in Path(annotation_json).parent.glob('*frame_*.json'):
            reserve(json.loads(path.read_text(encoding='utf-8')))
        prior = next((r for r in old_rows if int(r['source_frame_index']) == seed_source_idx), {})
        edge_reader = EdgeReader(video_file, width, height, source_fps, prior.get('edgeDiscovery'))
    from .review_evidence import ReviewEvidence
    review_checker = ReviewEvidence(video_file, seed_source_idx+1) if review_mode else None
    review_expected_ids = set(active_object_ids) | set((continuation_row or {}).get("reviewEvidence", {}).get("expectedObjectIds", []))
    edge_backfill = {}
    entry_origins = {}
    from contextlib import ExitStack
    with ExitStack() as resources:
        if edge_reader is not None: resources.callback(edge_reader.close)
        image_evidence = resources.enter_context(evidence_context)
        try:
            for output in outputs:
                frame_idx = int(output.frame_idx)
                if frame_idx >= end_frame_exclusive:
                    break
                if frame_idx < seed_frame or frame_idx >= len(source_indices):
                    continue
                source_idx = source_indices[frame_idx]
                entries = []
                identity_evidence = None
                if image_evidence is not None:
                    detections, mask_regions = decode_regions(session, output)
                    visible_boxes = {int(d.object_id): _json_bbox(d.bbox) for d in detections
                                     if int(d.object_id) in active_object_ids
                                     and not is_deleted(workspace, source_idx, int(d.object_id))}
                    identity_evidence = image_evidence.observe(source_idx, visible_boxes, mask_regions)
                    detection_areas = {int(d.object_id): d.mask_area for d in detections}
                    for oid, item in identity_evidence.items():
                        if item.get('status') == 'unknown' and item.get('maskArea') is None:
                            # The real detector still has an area when a Mask
                            # ROI is deliberately omitted by the evidence
                            # memory budget. This binds provenance only; it
                            # grants no support or geometry exemption.
                            item['maskArea'] = detection_areas.get(oid)
                    counts = {status: sum(item.get('status') == status for item in identity_evidence.values())
                              for status in ('supported', 'warning', 'uncertain', 'risk', 'unknown')}
                    logging.getLogger('review.tracking').info(
                        'tracking.image_evidence media=%s frame=%s counts=%s risks=%s', video_file.parent.name, source_idx,
                        counts, {oid: item.get('reasons') for oid, item in identity_evidence.items() if item.get('status') == 'risk'})
                elif frame_idx > seed_frame:
                    detections, _ = engine.decode_tracker_output(session, output)
                if edge_reader is not None:
                    occupied = [list(o['bbox']) for o in objects] if frame_idx == seed_frame else [list(d.bbox) for d in detections]
                    # Deleted/historical objects are exclusions, not fresh births.
                    occupied += [o['bbox'] for r in old_rows if int(r['source_frame_index']) == source_idx for o in r['objects']]
                    if frame_idx == seed_frame and prior.get('edgeDiscovery'):
                        # This source frame was already observed before resume.
                        edge_reader.prime_frame(source_idx)
                    else:
                        missing = frame_idx > seed_frame and not active_object_ids.issubset({int(d.object_id) for d in detections})
                        entries = edge_reader.observe(source_idx, occupied, allow_new=not missing)
                    for entry in entries:
                        samples = entry['samples']
                        # Never bridge an uncertain gap or resurrect a discarded branch.
                        first = samples[0]['frame']
                        if first < seed_source_idx and not all(any(int(r['source_frame_index']) == f for r in old_rows) for f in range(first,seed_source_idx)):
                            continue
                        oid = max(reserved_ids, default=0)+1
                        reserved_ids.add(oid); active_object_ids.add(oid)
                        object_names[oid] = f'rare sperm {oid}'
                        origin = {'kind':'edge-entry', 'firstFrame':first, 'confirmedFrame':source_idx,
                                  'motionVerified':True, 'humanConfirmed':False}
                        entry_origins[oid] = origin
                        for sample in samples:
                            row = {'object_id':oid, 'name':object_names[oid], 'bbox':sample['box'], 'score':sample['score'],
                                   'source':'edge_detector', 'mask_area':None, 'sam3_object_id':oid, 'entryOrigin':origin}
                            if sample['frame'] == source_idx:
                                from .services.sam3_engine import TrackDetection
                                detections.append(TrackDetection(oid, sample['box'], sample['score']))
                            else:
                                edge_backfill.setdefault(sample['frame'], []).append(row)
                        detector.prime_history([(source_idx, {oid:samples[-1]['box']})])
                        # Pass prompts as a batch; do not reset existing object state.
                        logging.getLogger('review.tracking').info('tracking.edge_entry_added media=%s object=%s first=%s confirmed=%s bbox=%s', video_file.parent.name, oid, first, source_idx, samples[-1]['box'])
                    added = [{'object_id':oid, 'bbox':next(d.bbox for d in detections if int(d.object_id)==oid)} for oid, origin in entry_origins.items() if origin['confirmedFrame']==source_idx] if entries else []
                    if added: outputs.add(frame_idx, added)
                    if frame_idx == seed_frame: new_rows[0]['edgeDiscovery'] = edge_reader.gate.snapshot()
                if frame_idx <= seed_frame:
                    continue

                object_rows: list[dict[str, Any]] = []
                for detection in detections:
                    bbox = _json_bbox(detection.bbox)
                    oid = int(detection.object_id)
                    if oid not in active_object_ids:
                        continue
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
                    if oid in entry_origins:
                        obj_row['entryOrigin'] = entry_origins[oid]
                        if source_idx == entry_origins[oid]['confirmedFrame']:
                            obj_row['source'] = 'edge_detector'

                # ── 异常检测 ──
                frame_objs_for_detector: dict[int, list[float]] = {}
                for obj_row in object_rows:
                    oid = int(obj_row["object_id"])
                    frame_objs_for_detector[oid] = list(obj_row["bbox"])
                hidden_ids = {oid for oid in active_object_ids if is_deleted(workspace, source_idx, oid)}
                anomaly_report = detector.push(source_idx, frame_objs_for_detector, ignored_object_ids=hidden_ids,
                                               mask_areas={int(obj["object_id"]): obj["mask_area"] for obj in object_rows if obj.get("mask_area") is not None},
                                               identity_evidence=identity_evidence)

                # A single-frame omission must not end this object's model history.
                # Retain raw predictions for deletion undo. API/workflow readers apply
                # the durable tombstones; only decisions are omitted here. Subsequent
                # frames retain the model identity and independent anomaly checks.
                visible_anomalies = [af for af in anomaly_report.frames if af.object_id not in hidden_ids]
                should_pause = anomaly_report.should_pause and any(af.level in (AnomalyLevel.ANOMALY, AnomalyLevel.DISAPPEARED) for af in visible_anomalies)
                for af in visible_anomalies:
                    if af.level != AnomalyLevel.WARNING:
                        continue
                    groups = set()
                    for reason in af.reasons:
                        groups.add("motion" if reason.startswith("adjacent_center_shift=") else "overlap" if reason.startswith("bbox_overlap_with=") else "image" if reason.startswith("image_") else "size")
                    for reason in groups:
                        key = (af.object_id, reason)
                        summary = warning_summary.setdefault(key, {"objectId": af.object_id, "name": object_names.get(af.object_id), "reason": reason, "count": 0, "firstFrame": source_idx, "lastFrame": source_idx, "calibrated": False})
                        summary["count"] += 1
                        summary["lastFrame"] = source_idx
                        summary["calibrated"] = summary["calibrated"] or (reason == "motion" and bool(af.details.get("normal_sample_matched")))

                # ── 把 anomaly level + reasons 写入每个 object row ──
                for af in anomaly_report.frames:
                    for obj_row in object_rows:
                        if int(obj_row["object_id"]) == af.object_id:
                            obj_row["anomaly_level"] = af.level.value
                            obj_row["anomaly_reasons"] = af.reasons
                            obj_row["anomaly_details"] = af.details
                            break

                row = {
                    "frame_index": source_idx,
                    "source_frame_index": source_idx,
                    "objects": object_rows,
                    "anomalies": [
                        {
                            "object_id": int(af.object_id),
                            "level": af.level.value,
                            "reasons": af.reasons,
                            "details": af.details,
                        }
                        for af in visible_anomalies
                        if af.level in (AnomalyLevel.ANOMALY, AnomalyLevel.DISAPPEARED)
                    ],
                }
                if review_checker is not None:
                    row['reviewEvidence'] = review_checker.observe(source_idx, {oid:box for oid,box in frame_objs_for_detector.items() if oid not in hidden_ids}, identity_evidence)
                    review_expected_ids.update(active_object_ids)
                    row['reviewEvidence']['expectedObjectIds'] = sorted(review_expected_ids)
                    for oid in sorted(review_expected_ids - set(frame_objs_for_detector)):
                        if not is_deleted(workspace, source_idx, oid):
                            row['reviewEvidence']['findings'].append(dict(objectId=oid, kind='tracking_lost', message='追踪对象缺失，请查看是否遮挡、出画或跟丢'))
                    logging.getLogger('review.tracking').info('tracking.review_observation media=%s frame=%s state=%s findings=%s', video_file.parent.name, source_idx, row['reviewEvidence']['state'], len(row['reviewEvidence']['findings']))
                new_rows.append(row)
                if edge_reader is not None: row['edgeDiscovery'] = edge_reader.gate.snapshot()
                last_processed_frame = source_idx
                analyzed_through_frame = source_idx
                if progress: progress(stage='tracking', message='正在追踪', processedFrames=len(new_rows), lastProcessedFrame=source_idx)
                print(
                    f"[sam3] frame={frame_idx} source={source_idx} "
                    f"tracked_objects={len(object_rows)}"
                )

                # HARD 异常 → 在当前异常帧保存结果后立即暂停。
                if should_pause and not review_mode:
                    from .tracking_pause_policy import classify_pause_reason, select_pause_evidence, summarize_warnings
                    earlier = [row for row in history_rows if int(row.get("source_frame_index", row.get("frame_index", -1))) <= seed_source_idx]
                    source_idx, visible_anomalies, retained_rows = select_pause_evidence(anomaly_report, earlier + new_rows, source_idx)
                    # Own the first risk and all later rows, even when observation
                    # began in the preceding chunk. Existing human data is separate.
                    publication_start = min(seed_source_idx, source_idx)
                    new_rows = [row for row in retained_rows if int(row["source_frame_index"]) >= publication_start]
                    last_processed_frame = source_idx
                    warning_summary = summarize_warnings(new_rows)
                    logging.getLogger("review.tracking").info(
                        "tracking.pause_window media=%s first=%s decision=%s published_through=%s objects=%s",
                        video_file.parent.name, source_idx, analyzed_through_frame, last_processed_frame,
                        [{"objectId": af.object_id, "reasons": af.reasons, "referenceFrame": af.details.get("geometry_reference_frame"),
                          "referenceBox": af.details.get("geometry_reference_bbox"), "currentBox": af.details.get("current_bbox")} for af in visible_anomalies],
                    )
                    pause_objects = []
                    for af in visible_anomalies:
                        if af.level in (AnomalyLevel.ANOMALY, AnomalyLevel.DISAPPEARED):
                            details = af.details or {}
                            area_ratio = details.get("area_ratio")
                            width_ratio = details.get("width_ratio")
                            height_ratio = details.get("height_ratio")
                            pause_type = classify_pause_reason(
                                af.reasons, disappeared=af.level == AnomalyLevel.DISAPPEARED)
                            logging.getLogger('review.tracking').info(
                                'tracking.pause_reason media=%s frame=%s object=%s type=%s hard_reasons=%s',
                                video_file.parent.name, source_idx, af.object_id, pause_type,
                                [reason for reason in af.reasons if reason.endswith(' HARD')])
                            other_id = details.get("other_object_id")
                            display_name = object_names.get(int(af.object_id), f"object-{af.object_id}")
                            pause_objects.append({
                                "object_id": int(af.object_id),
                                "name": display_name,
                                "display_name": display_name,
                                "type": pause_type,
                                "reasons": list(af.reasons),
                                "details": details,
                                "currentBox": details.get("current_bbox"),
                                "manualBaselineBox": details.get("manual_baseline_bbox"),
                                "manualBaselineFrame": details.get("manual_baseline_frame"),
                                "geometryReferenceBox": details.get("geometry_reference_bbox"),
                                "geometryReferenceFrame": details.get("geometry_reference_frame"),
                                "geometryReferenceSource": details.get("geometry_reference_source"),
                                "metrics": {
                                    "areaRatio": area_ratio,
                                    "widthRatio": width_ratio,
                                    "heightRatio": height_ratio,
                                    "aspectRatio": details.get("aspect_ratio_ratio"),
                                    "diagonalRatio": details.get("diagonal_ratio"),
                                    "overlapCoverage": details.get("overlap_coverage"),
                                    "centerShift": details.get("center_shift"),
                                    "motionNormalized": details.get("motion_normalized"),
                                    "motionPauseThreshold": details.get("motion_pause_threshold"),
                                    "motionStreak": details.get("motion_streak"),
                                    "feedbackSampleCount": details.get("feedback_sample_count", 0),
                                },
                                "reviewStartFrame": details.get("review_start_frame"),
                                "reviewEndFrame": details.get("review_end_frame"),
                                "reviewLookbackFrames": details.get("review_lookback_frames"),
                                "other_object_id": other_id,
                                "other_display_name": object_names.get(int(other_id), f"object-{other_id}") if other_id is not None else None,
                                "ratio": area_ratio,
                                "prevArea": details.get("baseline_area"),
                                "currArea": details.get("current_area"),
                            })
                    print(f"[anomaly] HARD detected → pause source_frame={source_idx}: {pause_objects}")
                    result_anomaly_paused = {
                        "frame_index": source_idx,
                        "reasons": pause_objects,
                        "levels": {str(af.object_id): af.level.value for af in visible_anomalies},
                    }
                    break

        finally:
            close_outputs = getattr(outputs, "close", None)
            if callable(close_outputs):
                close_outputs()

    if progress: progress(stage='saving_results', message='正在保存追踪结果')
    publication_start = min(seed_source_idx, last_processed_frame)
    # Only publish entries confirmed no later than the retained pause frame.
    valid_entries = {oid for oid, origin in entry_origins.items() if origin['confirmedFrame'] <= last_processed_frame}
    backfill = {fi:[o for o in rows if o['object_id'] in valid_entries] for fi,rows in edge_backfill.items()}
    backfill = {fi:rows for fi,rows in backfill.items() if rows}
    if backfill:
        earliest = min(publication_start, min(backfill))
        earlier_rows = [r for r in old_rows if earliest <= int(r['source_frame_index']) < publication_start]
        new_rows = earlier_rows + new_rows
        publication_start = earliest
        for row in new_rows:
            row['objects'].extend(backfill.get(int(row['source_frame_index']), []))
    merged_rows = _merge_rows(Path(output_json), new_rows, keep_before_source_frame=publication_start, pause=result_anomaly_paused)

    result = {
        "frames": [{**row, "objects": [obj for obj in row.get("objects", []) if not is_deleted(workspace, int(row["source_frame_index"]), int(obj["object_id"]))]} for row in merged_rows],
        "resultFile": str(Path(output_json).resolve()),
        "overlayVideo": None,
        "overlayDeferred": True,
        "startFrame": requested_source_start,
        "requestedFrames": requested,
        "processedFrames": len(new_rows),
        "lastProcessedFrame": last_processed_frame,
        "analyzedThroughFrame": analyzed_through_frame,
        "reachedVideoEnd": last_processed_frame >= source_frame_count - 1,
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
        "anomaly_paused": result_anomaly_paused,
        "warningSummary": list(warning_summary.values()),
    }
    logging.getLogger("review.tracking").info(
        "tracking.completed media=%s start=%s last=%s paused=%s feedback_objects=%s geometry_references=%s warnings=%s",
        video_file.parent.name, seed_source_frame, last_processed_frame, result_anomaly_paused is not None,
        sorted(detector.normal_motion_samples),
        {oid: refs[-1].frame_index for oid, refs in detector.geometry_references.items() if refs}, list(warning_summary.values()),
    )
    print(f"[sam3] result jsonl: {output_json}")
    print('[sam3] full-video preview is generated only when requested')
    return result


@source_write
def _merge_rows(
    path: Path,
    new_rows: list[dict[str, Any]],
    keep_before_source_frame: int | None = None,
    pause: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Merge a new tracking segment into the result file.

    When a tracking run starts/resumes at a seed frame, stale rows at or after
    that seed must be removed; otherwise a newly paused run could still expose
    old future tracking results after the pause frame. Rows strictly before the
    new seed are retained, while the new segment owns the seed-and-later range.
    """
    from .tracking_restart import recover_directory
    recover_directory(path.parent)
    merged: dict[int, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        try:
            key = int(row.get("source_frame_index", row.get("frame_index", -1)))
        except Exception:
            continue
        if key < 0:
            continue
        if keep_before_source_frame is not None and key >= int(keep_before_source_frame):
            continue
        merged[key] = row

    for row in new_rows:
        key = int(row["source_frame_index"])
        merged[key] = row

    ordered = [merged[key] for key in sorted(merged)]
    from .annotation_state import prepare_pause_publication, recover_pause_publication
    prepare_pause_publication(path.parent, ordered, pause)
    _write_jsonl(path, ordered)
    recover_pause_publication(path.parent)
    (path.parent / OVERLAY_FILE_NAME).unlink(missing_ok=True)
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
    workspace = read_state(source_video.parent)
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
                    if is_deleted(workspace, frame_idx, int(obj.get("object_id", 0))):
                        continue
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
