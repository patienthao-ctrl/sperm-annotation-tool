"""Detector prelabel: whole-frame detections on every frame, linked into stable tracks.

Confident boxes (>= HEAD_DETECTOR_CONF) may start a track; boxes between the low and the
confident threshold may only extend a track that already exists, at its velocity-predicted
position, across up to MAX_GAP frames without one. Frames without any detection are left
without a box: guessed boxes landed on empty background when a head left by the edge or a
fast head jumped beyond the gate (rec_20261022_20 objects 30, 11). A track needs at least
MIN_CONFIDENT confident frames.

Linking only decides which boxes are the same object, never whether something is sperm:
motion is not a criterion (dead sperm do not move, debris drifts). Places where two tracks
come close or a link jumps are flagged so the reviewer checks them first.
"""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Callable

from . import head_click

POLICY = 'detector-track-v1'
SOURCE = 'detector_track'
DETECTIONS_FILE = 'detections.json'
TRACKS_FILE = 'detector_tracks.json'
MAX_GAP = 3
SPEED_STEPS = 3
MIN_CONFIDENT = 2
CHECK_STEP = 10
BATCH = 4
log = logging.getLogger('review.prelabel')


def gate_for(width: int, height: int) -> float:
    """Largest per-frame move of one head: 100 px at 4K (fastest confirmed sperm ~94 px)."""
    return 100.0 * max(width, height) / 4096.0


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _diag(box):
    return math.hypot(box[2] - box[0], box[3] - box[1])


def iou(a, b) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _predict(observed: dict[int, list[float]], frame: int):
    keys = sorted(observed)
    last = keys[-1]
    cx, cy = _center(observed[last])
    if len(keys) >= 2:
        prev = keys[-1 - min(SPEED_STEPS, len(keys) - 1)]
        px, py = _center(observed[prev])
        span = max(1, last - prev)
        vx, vy = (cx - px) / span, (cy - py) / span
    else:
        vx = vy = 0.0
    gap = frame - last
    return cx + vx * gap, cy + vy * gap, gap


def link(frames_rows: list[list[list[float]]], width: int, height: int, high: float, low: float) -> list[dict[str, Any]]:
    """frames_rows[f] = [[x1,y1,x2,y2,score], ...] in source pixels.

    Returns tracks as {'frames': {f: {'bbox', 'score', 'kind'}}, 'flags': [...]}, kind being
    'high' or 'low'; every box is a real detection. Tracks are ordered by first frame, then x.
    """
    gate = gate_for(width, height)
    tracks: list[dict[str, Any]] = []
    active: list[int] = []
    for f, rows in enumerate(frames_rows):
        active = [t for t in active if f - max(tracks[t]['obs']) <= MAX_GAP + 1]
        used: set[int] = set()
        for stage in ('high', 'low'):
            idx = [i for i, r in enumerate(rows) if i not in used and
                   (r[4] >= high if stage == 'high' else low <= r[4] < high)]
            free = [t for t in active if f not in tracks[t]['obs']]
            if not idx or not free:
                continue
            pairs = []
            for t in free:
                px, py, gap = _predict(tracks[t]['obs'], f)
                limit = gate * (1 + 0.5 * (gap - 1))
                for i in idx:
                    cx, cy = _center(rows[i])
                    d = math.hypot(cx - px, cy - py)
                    if d <= limit:
                        pairs.append((d, t, i))
            pairs.sort()
            taken_t: set[int] = set()
            for d, t, i in pairs:
                if t in taken_t or i in used:
                    continue
                track = tracks[t]
                track['obs'][f] = rows[i][:4]
                track['frames'][f] = {'bbox': [float(v) for v in rows[i][:4]], 'score': float(rows[i][4]), 'kind': stage}
                if d > 0.6 * gate:
                    track['flags'].append({'kind': 'jump', 'frame': f})
                taken_t.add(t)
                used.add(i)
        for i, r in enumerate(rows):
            if i in used or r[4] < high:
                continue
            tracks.append({'obs': {f: r[:4]}, 'frames': {f: {'bbox': [float(v) for v in r[:4]], 'score': float(r[4]), 'kind': 'high'}}, 'flags': []})
            active.append(len(tracks) - 1)
    kept = []
    for track in tracks:
        if sum(item['kind'] == 'high' for item in track['frames'].values()) < MIN_CONFIDENT:
            continue
        kept.append({'frames': dict(sorted(track['frames'].items())), 'flags': track['flags']})
    _flag_crossings(kept)
    kept.sort(key=lambda t: (min(t['frames']), _center(t['frames'][min(t['frames'])]['bbox'])[0]))
    return kept


def _flag_crossings(tracks: list[dict[str, Any]]) -> None:
    """Two heads closer than about one head length may swap identities; flag both."""
    by_frame: dict[int, list[tuple[int, list[float]]]] = {}
    for n, track in enumerate(tracks):
        for f, item in track['frames'].items():
            by_frame.setdefault(f, []).append((n, item['bbox']))
    for f, items in by_frame.items():
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                (na, ba), (nb, bb) = items[a], items[b]
                reach = 1.2 * max(_diag(ba), _diag(bb))
                ca, cb = _center(ba), _center(bb)
                if math.hypot(ca[0] - cb[0], ca[1] - cb[1]) < reach:
                    for n, other in ((na, nb), (nb, na)):
                        tracks[n].setdefault('_cross', {}).setdefault(other, []).append(f)
    for track in tracks:
        for other, frames in track.pop('_cross', {}).items():
            track['flags'].append({'kind': 'cross', 'frame': frames[len(frames) // 2], 'with': other})


def summarize_flags(track: dict[str, Any], frame_count: int) -> list[dict[str, Any]]:
    flags = [dict(item) for item in track['flags']]
    keys = list(track['frames'])
    if keys and (keys[0] > 0 or keys[-1] < frame_count - 1):
        flags.append({'kind': 'partial', 'frame': keys[0], 'last': keys[-1]})
    weak = sum(item['kind'] != 'high' for item in track['frames'].values())
    if weak > 0.3 * len(keys):
        flags.append({'kind': 'weak'})
    return flags


def check_frames(frame_count: int, tracks: list[dict[str, Any]], step: int = CHECK_STEP) -> list[dict[str, Any]]:
    """Frames the annotator scans for heads the detector never reported.

    Every `step` frames plus the last frame, and frames where a track starts or ends inside
    the video (something enters or leaves; a missed target is likely near), unless an
    already chosen frame lies within two frames.
    """
    if frame_count <= 0:
        return []
    chosen = {f: 'fixed' for f in range(0, frame_count, step)}
    chosen[frame_count - 1] = 'fixed'
    events = []
    for track in tracks:
        keys = list(track['frames'])
        if keys and keys[0] > 0:
            events.append(keys[0])
        if keys and keys[-1] < frame_count - 1:
            events.append(keys[-1])
    for f in sorted(set(events)):
        if all(abs(f - g) > 2 for g in chosen):
            chosen[f] = 'event'
    return [{'frame': f, 'kind': kind} for f, kind in sorted(chosen.items())]


def reserved_object_ids(workspace: dict[str, Any], directory: Path) -> set[int]:
    """IDs a new track must never reuse: manual boxes, tombstones and old result rows."""
    reserved: set[int] = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('objectId', 'object_id') and isinstance(item, int):
                    reserved.add(item)
                elif key in ('deletedObjectIds', 'deletedTrackingIds') and isinstance(item, list):
                    reserved.update(int(v) for v in item if str(v).lstrip('-').isdigit())
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(workspace)
    for path in directory.glob('*frame_*.json'):
        try:
            walk(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            continue
    return reserved


def suppress_manual(frames_rows: list[list[list[float]]], manual: dict[int, list[list[float]]]) -> None:
    """A head the annotator already boxed by hand is not offered again as a detector track."""
    for f, boxes in manual.items():
        if 0 <= f < len(frames_rows) and boxes:
            frames_rows[f] = [r for r in frames_rows[f] if all(iou(r, b) <= 0.3 for b in boxes)]


def build_rows(tracks: list[dict[str, Any]], frame_count: int, reserved: set[int]) -> tuple[list[dict[str, Any]], dict[int, int]]:
    next_id = max([0, *reserved]) + 1
    ids: dict[int, int] = {}
    rows = [{'frame_index': f, 'source_frame_index': f, 'objects': [], 'anomalies': []} for f in range(frame_count)]
    for n, track in enumerate(tracks):
        oid = next_id
        next_id += 1
        ids[n] = oid
        for f, item in track['frames'].items():
            if 0 <= f < frame_count:
                rows[f]['objects'].append({
                    'object_id': oid, 'name': f'rare sperm {oid}',
                    'bbox': [round(float(v), 3) for v in item['bbox']],
                    'score': None if item['score'] is None else round(item['score'], 4),
                    'source': SOURCE, 'linkKind': item['kind'],
                    'mask_area': None, 'sam3_object_id': None,
                })
    return rows, ids


def read_frames(video: Path, frame_count: int):
    """Sequential decode from frame 0, exactly frame_count frames or a clear failure."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError('无法打开原视频，自动预标未完成')
    try:
        for index in range(frame_count):
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError(f'原视频第 {index + 1} 帧读取失败，自动预标未完成')
            yield frame
    finally:
        cap.release()


def detect_video(video: Path, frame_count: int, progress: Callable[..., None] | None = None) -> list[list[list[float]]]:
    frames_rows: list[list[list[float]]] = []
    batch = []
    for frame in read_frames(video, frame_count):
        batch.append(frame)
        if len(batch) == BATCH:
            frames_rows.extend(head_click.detect_batch(batch))
            batch = []
            if progress:
                progress(stage='detecting', message='正在逐帧识别精子头', processedFrames=len(frames_rows),
                         lastProcessedFrame=len(frames_rows) - 1, totalFrames=frame_count)
    if batch:
        frames_rows.extend(head_click.detect_batch(batch))
    if progress:
        progress(stage='detecting', message='正在逐帧识别精子头', processedFrames=len(frames_rows),
                 lastProcessedFrame=len(frames_rows) - 1, totalFrames=frame_count)
    return frames_rows


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    os.replace(tmp, path)


def model_record() -> dict[str, Any]:
    try:
        manifest = json.loads(head_click.BUNDLED_MODEL.with_suffix('.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        manifest = {}
    custom = head_click.model_path().resolve() != head_click.BUNDLED_MODEL.resolve()
    return {'modelId': 'custom' if custom else manifest.get('modelId'), 'high': head_click.high_confidence(), 'low': head_click.low_confidence()}


def prelabel(directory: Path, video: Path, info: dict[str, Any], workspace: dict[str, Any],
             manual: dict[int, list[list[float]]], progress: Callable[..., None] | None = None) -> dict[str, Any]:
    """Detect, link and publish. Returns the summary stored in detector_tracks.json."""
    from .tracker import _merge_rows, RESULT_FILE_NAME
    frame_count, width, height = int(info['frameCount']), int(info['width']), int(info['height'])
    record = model_record()
    frames_rows = detect_video(video, frame_count, progress)
    _write_json(directory / DETECTIONS_FILE, {'version': 1, 'policy': POLICY, **record, 'width': width, 'height': height,
                                              'frames': [[[round(v, 3) for v in r] for r in rows] for rows in frames_rows]})
    if progress:
        progress(stage='linking', message='正在连成目标轨迹', processedFrames=frame_count, lastProcessedFrame=frame_count - 1)
    linked_input = [list(rows) for rows in frames_rows]
    suppress_manual(linked_input, manual)
    tracks = link(linked_input, width, height, record['high'], record['low'])
    rows, ids = build_rows(tracks, frame_count, reserved_object_ids(workspace, directory))
    summary = {
        'version': 1, 'policy': POLICY, **record, 'frameCount': frame_count,
        'checkFrames': check_frames(frame_count, tracks),
        'tracks': [{'objectId': ids[n], 'first': min(t['frames']), 'last': max(t['frames']),
                    'flags': [{**flag, **({'with': ids[flag['with']]} if 'with' in flag else {})}
                              for flag in summarize_flags(t, frame_count)]}
                   for n, t in enumerate(tracks)],
    }
    _write_json(directory / TRACKS_FILE, summary)
    _merge_rows(directory / RESULT_FILE_NAME, rows, keep_before_source_frame=0)
    log.info('prelabel.published media=%s frames=%s tracks=%s check_frames=%s high=%s low=%s',
             directory.name, frame_count, len(tracks), len(summary['checkFrames']), record['high'], record['low'])
    return summary


def read_summary(directory: Path) -> dict[str, Any] | None:
    try:
        value = json.loads((directory / TRACKS_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get('policy') == POLICY else None


def read_detections(directory: Path) -> list[list[list[float]]] | None:
    try:
        value = json.loads((directory / DETECTIONS_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    frames = value.get('frames') if isinstance(value, dict) else None
    return frames if isinstance(frames, list) else None


def extend(detections: list[list[list[float]]], seed_frame: int, seed_box: list[float],
           occupied: dict[int, list[list[float]]], width: int, height: int, low: float) -> list[dict[str, Any]]:
    """Follow one clicked head through the cached detections, both directions.

    Only boxes not already taken by another target are used and nothing is guessed: frames
    without a detection get no box. The search stops after MAX_GAP+1 misses in a row.
    """
    gate = gate_for(width, height)
    found = {seed_frame: [float(v) for v in seed_box]}
    kinds = {seed_frame: 'seed'}
    for direction in (1, -1):
        observed = {0: found[seed_frame]}
        misses = 0
        f = seed_frame + direction
        while 0 <= f < len(detections) and misses <= MAX_GAP:
            step = abs(f - seed_frame)
            px, py, gap = _predict(observed, step)
            limit = gate * (1 + 0.5 * (gap - 1))
            best = None
            for r in detections[f]:
                if len(r) != 5 or r[4] < low:
                    continue
                if any(iou(r, b) > 0.5 for b in occupied.get(f, [])):
                    continue
                cx, cy = _center(r)
                d = math.hypot(cx - px, cy - py)
                if d <= limit and (best is None or d < best[0]):
                    best = (d, r)
            if best:
                found[f] = [float(v) for v in best[1][:4]]
                kinds[f] = 'low'
                observed[step] = found[f]
                misses = 0
            else:
                misses += 1
            f += direction
    return [{'frameIndex': f, 'bbox': [round(v, 3) for v in found[f]], 'kind': kinds[f]} for f in sorted(found)]


# ---------------------------------------------------------------- completion before review
# Once people have confirmed boxes, a much lower threshold is safe *next to them*: a hole between
# two boxes of a target, or the frames around a head someone added by hand. Only real detections
# near the expected place are taken; frames where nothing is found stay empty and are reported.
COMPLETE_CONF = float(os.getenv('HEAD_DETECTOR_COMPLETE_CONF', '0.01'))
COMPLETE_FILE = 'detector_complete.json'
MAX_HOLE = 8
CHAIN_RANGE = 30


def _inside(row: list[float], boxes: list[list[float]], pad: float = 4.0) -> bool:
    cx, cy = (row[0] + row[2]) / 2, (row[1] + row[3]) / 2
    return any(b[0] - pad <= cx <= b[2] + pad and b[1] - pad <= cy <= b[3] + pad for b in boxes)


def completion_frames(objects: dict[int, dict[int, tuple[list[float], str]]], frame_count: int) -> list[int]:
    """Frames worth detecting again: holes inside targets and the reach of hand-added targets."""
    need: set[int] = set()
    for frames in objects.values():
        keys = sorted(frames)
        for a, b in zip(keys, keys[1:]):
            if 1 < b - a <= MAX_HOLE + 1:
                need.update(range(a + 1, b))
        if keys and all(source == 'manual' for _, source in frames.values()):
            need.update(range(max(0, keys[0] - CHAIN_RANGE), keys[0]))
            need.update(range(keys[-1] + 1, min(frame_count, keys[-1] + CHAIN_RANGE + 1)))
    return sorted(need)


def complete(objects: dict[int, dict[int, tuple[list[float], str]]], detections: dict[int, list[list[float]]],
             width: int, height: int, frame_count: int) -> dict[str, list[dict[str, Any]]]:
    """objects[objectId][frame] = (xyxy, 'manual'|'ai'); detections[frame] = rows at COMPLETE_CONF.

    Returns {'boxes': [{objectId, frameIndex, bbox, score, kind}], 'missing': [{objectId, frameIndex}]}.
    """
    gate = gate_for(width, height)
    taken: dict[int, list[list[float]]] = {}
    for frames in objects.values():
        for f, (box, _) in frames.items():
            taken.setdefault(f, []).append(box)
    boxes, missing = [], []

    def take(oid: int, f: int, row: list[float], kind: str) -> None:
        bbox = [round(float(v), 3) for v in row[:4]]
        taken.setdefault(f, []).append(bbox)
        boxes.append({'objectId': oid, 'frameIndex': f, 'bbox': bbox, 'score': round(float(row[4]), 4), 'kind': kind})

    for oid in sorted(objects):
        frames = objects[oid]
        keys = sorted(frames)
        for a, b in zip(keys, keys[1:]):
            if not 1 < b - a <= MAX_HOLE + 1:
                continue
            for f in range(a + 1, b):
                w = (f - a) / (b - a)
                guess = [(1 - w) * u + w * v for u, v in zip(frames[a][0], frames[b][0])]
                gx, gy = _center(guess)
                limit = gate * (1 + 0.5 * (min(f - a, b - f) - 1))
                best = None
                for row in detections.get(f, []):
                    if len(row) != 5 or row[4] < COMPLETE_CONF or _inside(row, taken.get(f, [])):
                        continue
                    cx, cy = _center(row)
                    d = math.hypot(cx - gx, cy - gy)
                    if d <= limit and (best is None or d < best[0]):
                        best = (d, row)
                if best:
                    take(oid, f, best[1], 'hole')
                else:
                    missing.append({'objectId': oid, 'frameIndex': f})
        if keys and all(source == 'manual' for _, source in frames.values()):
            rows = [detections.get(f, []) for f in range(frame_count)]
            for seed, keep in ((keys[0], lambda f: f < keys[0]), (keys[-1], lambda f: f > keys[-1])):
                occupied = {f: list(v) for f, v in taken.items()}
                for item in extend(rows, seed, frames[seed][0], occupied, width, height, COMPLETE_CONF):
                    f = item['frameIndex']
                    if keep(f) and not _inside(item['bbox'] + [0], taken.get(f, [])):
                        score = next((r[4] for r in detections.get(f, []) if [round(v, 3) for v in r[:4]] == item['bbox']), COMPLETE_CONF)
                        take(oid, f, item['bbox'] + [score], 'chain')
    return {'boxes': boxes, 'missing': missing}


def run_completion(directory: Path, video: Path, info: dict[str, Any], objects: dict[int, dict[int, tuple[list[float], str]]],
                   revision: int, progress: Callable[..., None] | None = None) -> dict[str, Any]:
    """Detect the needed frames again at COMPLETE_CONF and publish the proposal; writes no boxes."""
    n = int(info['frameCount'])
    need = completion_frames(objects, n)
    wanted, detections, batch = set(need), {}, []

    def flush() -> None:
        for (f, _), rows in zip(batch, head_click.detect_batch([img for _, img in batch], conf=COMPLETE_CONF, caller='complete')):
            detections[f] = [[float(v) for v in r] for r in rows]
        batch.clear()
        if progress:
            progress(stage='detecting', processedFrames=len(detections), totalFrames=len(need),
                     lastProcessedFrame=max(detections) if detections else None)
    if need:
        for f, frame in enumerate(read_frames(video, max(need) + 1)):
            if f in wanted:
                batch.append((f, frame))
                if len(batch) == BATCH:
                    flush()
        if batch:
            flush()
    result = complete(objects, detections, int(info['width']), int(info['height']), n)
    record = {'version': 1, 'conf': COMPLETE_CONF, 'workspaceRevision': revision, 'frames': len(need), **result}
    path = directory / COMPLETE_FILE
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(record), encoding='utf-8')
    os.replace(tmp, path)
    return record


def read_completion(directory: Path) -> dict[str, Any] | None:
    try:
        value = json.loads((directory / COMPLETE_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get('boxes'), list) else None
