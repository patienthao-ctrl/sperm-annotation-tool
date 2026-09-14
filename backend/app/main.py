from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .auth import current_user, hash_password, sign_jwt, verify_password
from .config import DB_FILE, DEVICE, DTYPE, HOST, JWT_SECRET, MAX_VIDEO_BYTES, MODEL_ID, PORT, TRACK_DATA_DIR, TRACK_FRAMES
from .db import create_user, delete_annotation, get_user, get_user_by_id, init_db, insert_annotations, list_annotations
from .schemas import (
    AnomalyFrameOut,
    AnomalyScanRequest,
    AnomalyScanResponse,
    AuthRequest,
    ManualAnnotationRequest,
    TrackRequest,
)
from .tracker import (
    OVERLAY_FILE_NAME,
    RESULT_FILE_NAME,
    _probe_video,
    get_tracker_engine,
    track_video,
)

app = FastAPI(title="SAM3 Annotation Backend", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# GPU tracking strictly serialized for 4GB cards; FastAPI remains responsive.
TRACK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sam3-track")
TASKS: dict[str, dict[str, Any]] = {}
TASK_LOCK = threading.Lock()

def tracking_is_busy() -> bool:
    with TASK_LOCK:
        return any(item.get("status") in {"queued", "running"} for item in TASKS.values())


def allocate_media_id(stem: str) -> str:
    """同名视频不覆盖旧目录：首次为 stem，后续依次 stem_001、stem_002...。"""
    candidate = stem
    index = 1
    while (TRACK_DATA_DIR / candidate).exists():
        candidate = f"{stem}_{index:03d}"
        index += 1
    return candidate


class HealthResponse(BaseModel):
    ok: bool
    service: str
    storage: str
    sam3: dict[str, Any]


@app.on_event("startup")
def startup() -> None:
    init_db()
    TRACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 70)
    print("✅ FastAPI backend started")
    print(f"   URL        : http://{HOST}:{PORT}")
    print(f"   SQLite     : {DB_FILE}")
    print(f"   SAM3 model : {MODEL_ID}")
    print(f"   SAM3 device: {DEVICE} / {DTYPE}")
    print("   登录、标注、上传、视频播放、SAM3 Tracking 共用一个 FastAPI 进程")
    print("   SAM3 模型不会在启动时加载；第一次 Tracking 时加载并缓存")
    print("=" * 70)


@app.on_event("shutdown")
def shutdown() -> None:
    TRACK_EXECUTOR.shutdown(wait=False, cancel_futures=True)


@app.get("/api/health", response_model=HealthResponse)
def health() -> dict[str, Any]:
    try:
        engine = get_tracker_engine()
        model_loaded = engine.model is not None and engine.processor is not None
    except Exception:
        model_loaded = False
    with TASK_LOCK:
        running = sum(1 for x in TASKS.values() if x["status"] == "running")
        queued = sum(1 for x in TASKS.values() if x["status"] == "queued")
    return {
        "ok": True,
        "service": "sam3-annotation-backend",
        "storage": "sqlite",
        "sam3": {"ok": True, "modelLoaded": model_loaded, "queued": queued, "running": running, "trackingBusy": (queued + running) > 0, "trackFrames": TRACK_FRAMES},
    }


# ---------------------------- auth ----------------------------
@app.post("/api/auth/register", status_code=201)
def register(req: AuthRequest) -> dict[str, Any]:
    name = req.username.strip()
    if not name or not req.password:
        raise HTTPException(400, "账号和密码不能为空")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if get_user(name):
        raise HTTPException(409, "账号已存在")
    uid = create_user(name, hash_password(req.password))
    return {"token": sign_jwt({"uid": uid, "username": name}), "user": {"id": uid, "username": name}}


@app.post("/api/auth/login")
def login(req: AuthRequest) -> dict[str, Any]:
    user = get_user(req.username.strip())
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "账号或密码错误")
    uid = int(user["id"])
    return {"token": sign_jwt({"uid": uid, "username": user["username"]}), "user": {"id": uid, "username": user["username"]}}


@app.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    row = get_user_by_id(user["uid"])
    if not row:
        raise HTTPException(404, "用户不存在")
    return {"id": int(row["id"]), "username": row["username"]}


# -------------------------- annotation --------------------------
def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and v == v else None


def _px(obj: dict[str, Any], width: float | None, height: float | None) -> dict[str, float | None]:
    out = {"px_x1": None, "px_y1": None, "px_x2": None, "px_y2": None, "px_point_x": None, "px_point_y": None}
    if not width or not height:
        return out
    if isinstance(obj.get("bbox"), dict):
        b = obj["bbox"]
        out.update({
            "px_x1": round(float(b.get("x", 0)) / 100 * width),
            "px_y1": round(float(b.get("y", 0)) / 100 * height),
            "px_x2": round(float(b.get("x", 0) + b.get("width", 0)) / 100 * width),
            "px_y2": round(float(b.get("y", 0) + b.get("height", 0)) / 100 * height),
        })
    if isinstance(obj.get("point"), dict):
        out["px_point_x"] = round(float(obj["point"].get("x", 0)) / 100 * width)
        out["px_point_y"] = round(float(obj["point"].get("y", 0)) / 100 * height)
    return out


@app.post("/api/annotation/annotations/manual", status_code=201)
def save_manual(req: ManualAnnotationRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if tracking_is_busy():
        raise HTTPException(409, "SAM3 Tracking 正在运行，暂时禁止人工标注")
    if not req.mediaId:
        raise HTTPException(400, "缺少 mediaId")
    if not req.objects:
        raise HTTPException(400, "objects 为空")
    width, height = _num(req.mediaWidth), _num(req.mediaHeight)
    raw = req.model_dump_json()
    batch_id = f"batch-{user['uid']}-{uuid.uuid4().hex[:10]}"
    rows = []
    for obj in req.objects:
        px = _px(obj, width, height)
        bbox = obj.get("bbox") if isinstance(obj.get("bbox"), dict) else None
        point = obj.get("point") if isinstance(obj.get("point"), dict) else None
        rows.append({
            "user_id": user["uid"], "batch_id": batch_id, "object_id": str(obj.get("id", "")),
            "media_id": req.mediaId, "media_name": req.mediaName, "media_type": req.mediaType,
            "media_width": int(width) if width else None, "media_height": int(height) if height else None,
            "frame_index": req.frameIndex, "timestamp_ms": int(req.timestampMs or 0),
            "object_name": obj.get("name"), "source": obj.get("source", "manual"),
            "confidence": _num(obj.get("confidence")), "shape_type": "bbox" if bbox else "point",
            "pct_x": _num((bbox or point or {}).get("x")), "pct_y": _num((bbox or point or {}).get("y")),
            "pct_w": _num((bbox or {}).get("width")), "pct_h": _num((bbox or {}).get("height")),
            **px, "annotation_version": req.annotationVersion, "raw_json": raw,
        })
    insert_annotations(rows)
    return {"id": f"annotation-{batch_id}", "batchId": batch_id, "ok": True, "count": len(rows)}


@app.get("/api/annotation/projects/{project_id}/results")
def results(project_id: str, mediaId: str | None = Query(default=None), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    rows = list_annotations(user["uid"], mediaId)
    return {"items": rows, "total": len(rows)}


@app.get("/api/annotation/media/{media_id}")
def results_by_media(media_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    rows = list_annotations(user["uid"], media_id)
    return {"items": rows, "total": len(rows)}


@app.delete("/api/annotation/{annotation_id}")
def remove_annotation(annotation_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, bool]:
    if not delete_annotation(annotation_id, user["uid"]):
        raise HTTPException(404, "记录不存在或无权删除")
    return {"ok": True}


# ----------------------------- files -----------------------------
def safe_stem(filename: str) -> str:
    stem = Path(Path(filename).name).stem
    clean = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in stem).strip()
    return clean or f"video-{uuid.uuid4().hex[:8]}"


def media_dir(media_id: str) -> Path:
    return TRACK_DATA_DIR / Path(media_id).name


def resolve_media_dir(media_id: str, media_name: str | None = None) -> Path | None:
    """
    查找素材的真实 track_data 目录。
    前端可能传的是 local-xxx / server-xxx 这类 id，
    但后端 track_data 目录名可能是 UUID hash。
    优先直接用 media_id 匹配，找不到就用 media_name 反查所有 media.json。
    """
    # 1. 直接匹配（大多数情况）
    direct = media_dir(media_id)
    if direct.is_dir():
        return direct

    # 2. 用 media_name 反查所有 media.json 的 videoName
    if media_name:
        for d in TRACK_DATA_DIR.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            mj = d / "media.json"
            if not mj.is_file():
                continue
            try:
                meta = json.loads(mj.read_text(encoding="utf-8"))
                if meta.get("videoName") == media_name or meta.get("mediaId") == media_id:
                    return d
            except Exception:
                continue

    # 3. 兜底：按文件名（不含路径）匹配
    if media_name:
        stem = Path(media_name).stem  # 去掉扩展名
        for d in TRACK_DATA_DIR.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            # 目录名本身就是 stem（790663... 这种）
            if d.name == stem:
                return d
            # 目录里的 mp4 文件名匹配
            for f in d.glob("*.mp4"):
                if f.name == media_name or f.stem == stem:
                    return d

    return None


def find_video(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    # Prefer the original uploaded MP4 recorded in media.json.
    meta_file = directory / "media.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            candidate = directory / Path(str(meta.get("videoName", ""))).name
            if candidate.is_file() and candidate.suffix.lower() == ".mp4":
                return candidate
        except Exception:
            pass
    # Never mistake the processed overlay video for the source video.
    return next(
        (x for x in directory.iterdir()
         if x.is_file() and x.suffix.lower() == ".mp4" and x.name != OVERLAY_FILE_NAME),
        None,
    )


async def save_upload(upload: UploadFile, target: Path) -> int:
    total = 0
    with target.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_VIDEO_BYTES:
                raise HTTPException(413, f"视频超过大小限制 {MAX_VIDEO_BYTES} bytes")
            out.write(chunk)
    return total


@app.post("/api/track/upload", status_code=201)
async def upload_video(file: UploadFile = File(...), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    filename = Path(file.filename or "video.mp4").name
    if Path(filename).suffix.lower() != ".mp4":
        raise HTTPException(400, "目前只支持 MP4 视频")
    stem = safe_stem(filename)
    media_id = allocate_media_id(stem)
    directory = media_dir(media_id)
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / filename
    try:
        size = await save_upload(file, target)
    except Exception:
        target.unlink(missing_ok=True)
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass
        raise
    finally:
        await file.close()
    if size <= 0:
        target.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass
        raise HTTPException(400, "视频文件为空")
    # 用 cv2 读取视频真实元信息（FPS / 分辨率 / 帧数），供前端帧对齐使用
    video_meta: dict[str, Any] = {}
    try:
        import cv2
        cap = cv2.VideoCapture(str(target))
        if cap.isOpened():
            video_meta = {
                "fps": float(cap.get(cv2.CAP_PROP_FPS) or 0),
                "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
                "frameCount": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
            }
        cap.release()
    except Exception:
        video_meta = {}
    (directory / "media.json").write_text(json.dumps({"mediaId": media_id, "videoName": filename, "videoPath": str(target.resolve()), "createdBy": user["uid"], **video_meta}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"mediaId": media_id, "videoName": filename, "videoUrl": f"/api/track/video/{media_id}", **video_meta}


@app.post("/api/track/annotations", status_code=201)
def save_frame_annotations(req: dict[str, Any], user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if tracking_is_busy():
        raise HTTPException(409, "SAM3 Tracking 正在运行，暂时禁止人工标注")
    frame = int(req.get("frameIndex", -1))
    annotations = req.get("annotations")
    if not req.get("mediaId") or frame < 0 or not isinstance(annotations, list) or not annotations:
        raise HTTPException(400, "mediaId/frameIndex/annotations 无效")
    directory = media_dir(str(req["mediaId"]))
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "media": {"id": str(req["mediaId"]), "name": req.get("mediaName") or f"{req['mediaId']}.mp4", "type": "video", "width": req.get("mediaWidth"), "height": req.get("mediaHeight")},
        "frame": {"frameIndex": frame, "timestampMs": req.get("timestampMs", 0)},
        "coordinateSystem": {"source": "frontend-pixel", "target": "pixel", "bbox": "[x1, y1, x2, y2]"},
        "annotations": annotations,
    }
    filename = f"annotations_frame_{frame:06d}.json"
    (directory / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"filename": filename, "frameIndex": frame}


# ---------------------------- tracking ----------------------------
def _set_task(task_id: str, **patch: Any) -> None:
    with TASK_LOCK:
        TASKS[task_id] = {**TASKS.get(task_id, {}), **patch}


def _run_tracking_task(task_id: str, req: TrackRequest, video: Path, seed_file: Path, output_file: Path) -> None:
    _set_task(task_id, status="running", message="SAM3 tracking running")
    try:
        result = track_video(str(video), str(seed_file), str(output_file), max_frames=req.maxFrames, bbox_mode="pixel", start_frame=req.startFrame)
        anomaly_paused = result.get("anomaly_paused")
        if anomaly_paused:
            _set_task(
                task_id,
                status="paused",
                message=f"Anomaly detected at frame {anomaly_paused['frame_index']}",
                paused=True,
                pausedFrame=anomaly_paused["frame_index"],
                pausedObjects=anomaly_paused.get("reasons", []),
                anomalyLevels=anomaly_paused.get("levels", {}),
                processedFrames=result.get("processedFrames", 0),
            )
        else:
            _set_task(
                task_id,
                status="success",
                message="completed",
                processedFrames=result.get("processedFrames", 0),
            )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _set_task(task_id, status="failed", message=str(exc))


@app.post("/api/track", status_code=202)
def start_tracking(req: TrackRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if tracking_is_busy():
        raise HTTPException(409, "已有 SAM3 Tracking 任务正在运行，请等待完成")
    if req.maxFrames > TRACK_FRAMES:
        raise HTTPException(400, f"单次 Tracking 最多 {TRACK_FRAMES} 帧")
    if not req.annotations:
        raise HTTPException(400, "没有 Tracking seed bbox")
    directory = media_dir(req.mediaId)
    video = find_video(directory)
    if not video:
        raise HTTPException(404, "视频文件不存在，请重新上传")
    seed_file = directory / f"seed_frame_{req.startFrame:06d}.json"
    output_file = directory / RESULT_FILE_NAME
    payload = {
        "media": {"id": req.mediaId, "name": req.mediaName or video.name, "type": "video", "width": req.mediaWidth, "height": req.mediaHeight},
        "frame": {"frameIndex": req.startFrame, "timestampMs": 0},
        "coordinateSystem": {"source": "frontend-pixel", "target": "pixel", "bbox": "[x1, y1, x2, y2]"},
        "annotations": req.annotations,
    }
    seed_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    task_id = uuid.uuid4().hex
    _set_task(task_id, taskId=task_id, status="queued", message="queued", mediaId=req.mediaId, startFrame=req.startFrame, userId=user["uid"])
    TRACK_EXECUTOR.submit(_run_tracking_task, task_id, req, video, seed_file, output_file)
    return {"taskId": task_id, "status": "queued", "maxFrames": req.maxFrames, "trackFrames": TRACK_FRAMES}


@app.get("/api/track/status/{task_id}")
def tracking_status(task_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with TASK_LOCK:
        item = TASKS.get(task_id)
    if not item:
        raise HTTPException(404, "task not found")
    return {k: v for k, v in item.items() if k != "userId"}


@app.post("/api/anomaly/scan", response_model=AnomalyScanResponse)
def scan_anomalies(req: AnomalyScanRequest, user: dict[str, Any] = Depends(current_user)):
    """对已有 tracker_results.json 做事后异常扫描"""
    from .services.anomaly_detector import AnomalyDetector, AnomalyConfig

    directory = resolve_media_dir(req.mediaId, req.mediaName)
    if not directory:
        raise HTTPException(404, f"media {req.mediaId} not found")

    video = find_video(directory)
    if not video:
        raise HTTPException(404, "video file not found")

    cap_meta = _probe_video(video)
    width = cap_meta["width"]
    height = cap_meta["height"]
    fps = cap_meta["fps"]

    tracker_file = directory / RESULT_FILE_NAME
    rows = _read_tracker_jsonl(tracker_file) if tracker_file.is_file() else []

    detector = AnomalyDetector(config=AnomalyConfig(), fps=fps, frame_width=width, frame_height=height)
    all_frames: list[AnomalyFrameOut] = []
    summary: dict[str, int] = {"anomaly": 0, "warning": 0, "disappeared": 0}
    pause_at: int | None = None

    for row in rows:
        fi = int(row.get("frame_index", 0))
        frame_objs: dict[int, list[float]] = {}
        for obj in row.get("objects", []):
            frame_objs[int(obj["object_id"])] = list(obj["bbox"])
        report = detector.push(fi, frame_objs)

        for af in report.frames:
            all_frames.append(AnomalyFrameOut(
                frame_index=af.frame_index,
                object_id=af.object_id,
                level=af.level.value,
                reasons=af.reasons,
                details=af.details,
            ))
            if af.level.value in summary:
                summary[af.level.value] += 1

        if report.should_pause and pause_at is None:
            pause_at = fi

    return AnomalyScanResponse(
        mediaId=req.mediaId,
        totalFrames=len(rows),
        anomalyFrames=all_frames,
        summary=summary,
        shouldPauseAt=pause_at,
    )


def _read_tracker_jsonl(file: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not file.is_file():
        return rows
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _tracker_rows_to_frames(rows: list[dict[str, Any]], fps: float = 0.0) -> list[dict[str, Any]]:
    """Convert raw-frame JSONL rows directly to browser-ready frame results.

    Raw-frame mode uses the same frame index as the source MP4. No interpolation
    or sampled-frame conversion is performed.
    """
    frames: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: int(r.get("frame_index", r.get("source_frame_index", 0)))):
        frame_index = int(row.get("frame_index", row.get("source_frame_index", 0)))
        annotations = []
        for obj in row.get("objects", []):
            bbox = obj.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            object_id = int(obj.get("object_id", obj.get("sam3_object_id", 0)))
            annotations.append({
                "id": str(object_id),
                "objectId": object_id,
                "name": obj.get("name") or f"object-{object_id}",
                "source": "ai",
                "bbox": [float(v) for v in bbox],
                "frameIndex": frame_index,
                "timestampMs": int(round(frame_index * 1000.0 / fps)) if fps > 0 else 0,
                "confidence": obj.get("score"),
                "score": obj.get("score"),
                "sam3ObjectId": obj.get("sam3_object_id", object_id),
                "maskArea": obj.get("mask_area"),
                "anomaly": obj.get("anomaly"),
            })
        frames.append({
            "frameIndex": frame_index,
            "timestampMs": int(round(frame_index * 1000.0 / fps)) if fps > 0 else 0,
            "annotations": annotations,
        })
    return frames


@app.get("/api/track/result/{media_id}")
def tracking_result(media_id: str, frameIndex: int | None = Query(default=None), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    directory = media_dir(media_id)
    file = directory / RESULT_FILE_NAME
    if not file.is_file():
        raise HTTPException(404, f"{RESULT_FILE_NAME} 尚未生成")
    rows = _read_tracker_jsonl(file)
    meta = {}
    meta_file = directory / "media.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    source_fps = float(meta.get("fps") or 0.0)
    frames = _tracker_rows_to_frames(rows, source_fps)
    if frameIndex is None:
        return {"format": "sam3-tracking-results-jsonl", "frames": frames, "count": len(frames)}
    return next(
        (f for f in frames if int(f.get("frameIndex", -1)) == frameIndex),
        {"frameIndex": frameIndex, "timestampMs": 0, "annotations": []},
    )


@app.get("/api/track/result-file/{media_id}")
def tracking_result_file(media_id: str, user: dict[str, Any] = Depends(current_user)) -> FileResponse:
    file = media_dir(media_id) / RESULT_FILE_NAME
    if not file.is_file():
        raise HTTPException(404, f"{RESULT_FILE_NAME} 尚未生成")
    return FileResponse(file, media_type="application/x-ndjson", filename=RESULT_FILE_NAME, content_disposition_type="inline")


@app.get("/api/track/media")
def list_media(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """列出后端已有的所有视频素材（刷新页面后可恢复素材列表）。"""
    items: list[dict[str, Any]] = []
    if not TRACK_DATA_DIR.is_dir():
        return {"items": items}
    name_count: dict[str, int] = {}
    for entry in sorted(TRACK_DATA_DIR.iterdir()):
        if not entry.is_dir():
            continue
        video_file = find_video(entry)
        if not video_file:
            continue
        meta: dict[str, Any] = {}
        meta_file = entry / "media.json"
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        has_result = (entry / RESULT_FILE_NAME).is_file()
        base_name = meta.get("videoName") or video_file.name
        name_count[base_name] = name_count.get(base_name, 0) + 1
        count = name_count[base_name]
        display_name = base_name if count == 1 else f"{base_name} ({count})"
        items.append({
            "mediaId": entry.name,
            "videoName": display_name,
            "videoUrl": f"/api/track/video/{entry.name}",
            "hasTrackingResult": has_result,
            "fps": meta.get("fps") or 0,
            "width": meta.get("width") or 0,
            "height": meta.get("height") or 0,
            "frameCount": meta.get("frameCount") or 0,
        })
    return {"items": items}


@app.delete("/api/track/media/{media_id}")
def delete_media(media_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """删除一个视频素材及其目录下的所有文件。"""
    if tracking_is_busy():
        raise HTTPException(409, "SAM3 Tracking 正在运行，无法删除素材")
    directory = media_dir(media_id)
    if not directory.is_dir():
        raise HTTPException(404, "素材不存在")
    import shutil
    shutil.rmtree(directory, ignore_errors=True)
    return {"deleted": True, "mediaId": media_id}


@app.get("/api/track/video/{media_id}")
def video(media_id: str) -> FileResponse:
    file = find_video(media_dir(media_id))
    if not file:
        raise HTTPException(404, "视频不存在")
    return FileResponse(file, media_type="video/mp4", filename=file.name, content_disposition_type="inline")


@app.get("/api/track/overlay/{media_id}")
def tracking_overlay(media_id: str, user: dict[str, Any] = Depends(current_user)) -> FileResponse:
    file = media_dir(media_id) / OVERLAY_FILE_NAME
    if not file.is_file():
        raise HTTPException(404, f"{OVERLAY_FILE_NAME} 尚未生成")
    return FileResponse(file, media_type="video/mp4", filename=OVERLAY_FILE_NAME, content_disposition_type="inline")


@app.get("/api/track/sam3/health")
def sam3_health(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    engine = get_tracker_engine()
    return {"ok": True, "modelLoaded": engine.model is not None and engine.processor is not None, "model": engine.model_id, "device": engine.device, "dtype": str(engine.dtype)}


# ---------------------------- dataset export ----------------------------

class DatasetExportRequest(BaseModel):
    mediaId: str
    mediaType: str = "video"
    mediaName: str | None = None
    mediaWidth: float | None = None
    mediaHeight: float | None = None
    format: str = "coco"         # "coco" | "yolo" | "both"
    splitRatio: float = 0.8      # 训练集比例，0 = 不划分
    classNames: list[str] = []   # 类别名（从前端 object 名提取）
    annotations: list[dict[str, Any]]  # 所有帧的标注（来自 annotationsByMedia）


@app.post("/api/export/dataset")
def export_dataset(req: DatasetExportRequest, user: dict[str, Any] = Depends(current_user)) -> FileResponse:
    """
    导出训练数据集（参考 CVAT 风格）。

    接收前端 annotationsByMedia 里该素材的所有帧标注，
    从后端 track_data 找到视频，抽取所有被标注的帧，
    生成 COCO JSON 或 YOLO txt，打包 zip 返回。
    """
    import shutil
    import zipfile
    import cv2

    # ── 1. 准备工作目录 ──
    export_id = f"export-{uuid.uuid4().hex[:10]}"
    work_dir = TRACK_DATA_DIR / "_exports" / export_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── 2. 找到视频 ──
        video_path: Path | None = None
        directory: Path | None = None
        if req.mediaType == "video":
            directory = resolve_media_dir(req.mediaId, getattr(req, 'mediaName', None))
            if directory:
                video_path = find_video(directory)
        # ── 3. 解析标注：按帧聚合 ──
        # 前端传来的 annotations 可能不完整（localStorage 没同步全 tracking 结果），
        # 所以我们主动从后端 track_data 读完整数据 + 前端传来的合并
        frames_map: dict[int, list[dict[str, Any]]] = {}
        class_names: dict[str, int] = {}  # name → class_id
        all_names = req.classNames or []

        # 先给前端传来的 classNames 编号
        for i, name in enumerate(all_names):
            class_names[name] = i

        def _register(obj: dict[str, Any], source_hint: str = "frontend"):
            """把一个 annotation object 注册进 frames_map。"""
            fi = int(obj.get("frameIndex", 0))
            obj_copy = {**obj}
            # 确保有 source 字段
            if "source" not in obj_copy:
                obj_copy["source"] = source_hint
            frames_map.setdefault(fi, []).append(obj_copy)
            name = obj.get("name") or obj.get("objectId") or "object"
            if name not in class_names:
                class_names[name] = len(class_names)

        # 3a. 前端传来的（最高优先级，最权威）
        for obj in req.annotations:
            _register(obj, "frontend")

        # 3b. 后端 tracker_results.json（AI tracking 完整结果，JSONL 格式）
        # 实际格式: {"frame_index": N, "objects": [{"object_id": N, "bbox": [x1,y1,x2,y2], "score": ..., "name": ...}]}
        if req.mediaType == "video":
            tracker_file = (directory or media_dir(req.mediaId)) / RESULT_FILE_NAME
            if tracker_file.is_file():
                try:
                    for line in tracker_file.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line: continue
                        row = json.loads(line)
                        fi = int(row.get("frame_index", 0))
                        for ann in row.get("objects", []):
                            bbox = ann.get("bbox") or [0, 0, 0, 0]
                            px_w = float(req.mediaWidth) if req.mediaWidth else 640
                            px_h = float(req.mediaHeight) if req.mediaHeight else 480
                            pct_obj = {
                                "id": f"ai-{fi}-{ann.get('object_id')}",
                                "objectId": ann.get("object_id"),
                                "name": ann.get("name") or f"object-{ann.get('object_id')}",
                                "source": "ai",
                                "confidence": ann.get("score"),
                                "frameIndex": fi,
                                "bbox": {
                                    "x": (bbox[0] / px_w) * 100 if px_w > 0 else 0,
                                    "y": (bbox[1] / px_h) * 100 if px_h > 0 else 0,
                                    "width": ((bbox[2] - bbox[0]) / px_w) * 100 if px_w > 0 else 0,
                                    "height": ((bbox[3] - bbox[1]) / px_h) * 100 if px_h > 0 else 0,
                                },
                            }
                            key = f"{fi}:{ann.get('object_id')}"
                            existing_keys = {
                                f"{int(o.get('frameIndex',0))}:{o.get('objectId')}"
                                for o in frames_map.get(fi, [])
                            }
                            if key not in existing_keys:
                                _register(pct_obj, "backend-tracker")
                except Exception:
                    pass  # tracker JSONL 解析失败不阻塞

            # 3c. 后端 annotations_frame_*.json（seed JSON，每帧一个）
            for seed_file in sorted((directory or media_dir(req.mediaId)).glob("annotations_frame_*.json")):
                try:
                    seed = json.loads(seed_file.read_text(encoding="utf-8"))
                    fi = int(seed.get("frame", {}).get("frameIndex", 0))
                    seed_w = float(seed.get("media", {}).get("width") or req.mediaWidth or 640)
                    seed_h = float(seed.get("media", {}).get("height") or req.mediaHeight or 480)
                    for ann in seed.get("annotations", []):
                        bbox = ann.get("bbox") or [0, 0, 0, 0]
                        if isinstance(bbox, dict):
                            # 已经是 {x, y, width, height} 格式（前端存的）
                            pct_obj = {**ann, "frameIndex": fi, "bbox": bbox}
                        else:
                            # 像素 [x1, y1, x2, y2]
                            pct_obj = {
                                "id": ann.get("id") or f"seed-{fi}",
                                "objectId": ann.get("objectId") or ann.get("id"),
                                "name": ann.get("name") or "object",
                                "source": ann.get("source") or "manual",
                                "frameIndex": fi,
                                "bbox": {
                                    "x": (bbox[0] / seed_w) * 100 if seed_w > 0 else 0,
                                    "y": (bbox[1] / seed_h) * 100 if seed_h > 0 else 0,
                                    "width": ((bbox[2] - bbox[0]) / seed_w) * 100 if seed_w > 0 else 0,
                                    "height": ((bbox[3] - bbox[1]) / seed_h) * 100 if seed_h > 0 else 0,
                                },
                            }
                        key = f"{fi}:{pct_obj.get('objectId') or pct_obj.get('id')}"
                        existing_keys = {
                            f"{int(o.get('frameIndex',0))}:{o.get('objectId') or o.get('id')}"
                            for o in frames_map.get(fi, [])
                        }
                        if key not in existing_keys:
                            _register(pct_obj, "backend-seed")
                except Exception:
                    pass

        
        if not frames_map:
            raise HTTPException(400, "没有标注数据可导出")

        # 去重：同帧同 objectId 可能因为 seed 和前端重复（取前端优先）
        for fi in list(frames_map.keys()):
            seen_keys: set[str] = set()
            deduped = []
            # 前端优先：先处理 frontend/source=manual 的，再处理 backend-* 的
            sorted_objs = sorted(frames_map[fi], key=lambda o: (
                0 if o.get("source") in ("frontend", "manual") else
                1 if o.get("source") == "ai" else 2
            ))
            for obj in sorted_objs:
                key = f"{obj.get('objectId') or obj.get('id')}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    deduped.append(obj)
            frames_map[fi] = deduped

        # ── 4. 收集所有有标注的 frameIndex（排序） ──
        annotated_frames = sorted(frames_map.keys())
        img_w = int(req.mediaWidth) if req.mediaWidth else 640
        img_h = int(req.mediaHeight) if req.mediaHeight else 480

        # ── 5. 抽取视频帧（如果是视频） ──
        images_dir = work_dir / "images"
        images_dir.mkdir(exist_ok=True)
        frame_filename: dict[int, str] = {}  # frameIndex → image filename

        if req.mediaType == "video" and video_path:
            cap = cv2.VideoCapture(str(video_path))
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or img_w
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or img_h
            if req.mediaWidth is None: img_w = actual_w
            if req.mediaHeight is None: img_h = actual_h
            for fi in annotated_frames:
                cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                ret, frame = cap.read()
                if not ret:
                    continue
                fname = f"frame_{fi:06d}.jpg"
                cv2.imwrite(str(images_dir / fname), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                frame_filename[fi] = fname
            cap.release()
        else:
            # 图片素材：直接用 img_w/img_h，不需要抽帧
            fname = "image_000000.jpg"
            frame_filename[annotated_frames[0]] = fname

        if not frame_filename:
            raise HTTPException(400, "视频帧抽取失败，请检查视频是否存在")

        # ── 6. 划分 train/val（如果 splitRatio > 0） ──
        split_ratio = float(req.splitRatio)
        do_split = 0.0 < split_ratio < 1.0
        split_label = "train"  # 默认都放 train（不划分时）

        if do_split:
            split_idx = max(1, int(len(annotated_frames) * split_ratio))
            train_frames = annotated_frames[:split_idx]
            val_frames = annotated_frames[split_idx:]
            train_names = {fi: frame_filename[fi] for fi in train_frames if fi in frame_filename}
            val_names = {fi: frame_filename[fi] for fi in val_frames if fi in frame_filename}

            # 把 train/val 帧移到子目录
            for split, names in [("train", train_names), ("val", val_names)]:
                split_dir = work_dir / split / "images"
                split_dir.mkdir(parents=True, exist_ok=True)
                for fi, fname in names.items():
                    src = images_dir / fname
                    dst = split_dir / fname
                    if src.is_file():
                        shutil.move(str(src), str(dst))

            # 更新 frame_filename 映射：变成 (split, fi)
            frame_filename = {**{("train", fi): f"train/images/{fname}" for fi, fname in train_names.items()},
                              **{("val", fi): f"val/images/{fname}" for fi, fname in val_names.items()}}
        else:
            split = split_label  # "train"
            # 不划分：images 保持在根 images/ 下
            frame_filename = {(split, fi): f"images/{fname}" for fi, fname in frame_filename.items()}

        # ── 7. 生成 COCO JSON ──
        do_coco = req.format in ("coco", "both")
        do_yolo = req.format in ("yolo", "both")

        if do_coco:
            _generate_coco(work_dir, class_names, frames_map, frame_filename, img_w, img_h, do_split)

        # ── 8. 生成 YOLO txt ──
        if do_yolo:
            _generate_yolo(work_dir, class_names, frames_map, frame_filename, img_w, img_h, do_split)

        # ── 9. 生成 dataset_info.txt ──
        _write_dataset_info(work_dir, req.mediaName or req.mediaId, req.mediaType,
                            class_names, len(annotated_frames), do_split, split_ratio)

        # ── 10. 打包 zip ──
        zip_path = work_dir.parent / f"{export_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in work_dir.rglob("*"):
                if f.is_file():
                    zf.write(str(f), f.relative_to(work_dir.parent))

        zip_size_mb = zip_path.stat().st_size / 1024 / 1024
        print(f"[export] zip ready: {zip_path.name} ({zip_size_mb:.2f}MB, {len(frame_filename)} frames)")

        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"{req.mediaName or req.mediaId}_dataset.zip",
            content_disposition_type="attachment",
        )
    except HTTPException:
        raise
    finally:
        # 清理工作目录（保留 zip）
        shutil.rmtree(work_dir, ignore_errors=True)


def _bbox_px(obj: dict[str, Any], img_w: int, img_h: int) -> tuple[float, float, float, float]:
    """从前端 bbox（百分比）转像素 (x1, y1, x2, y2)。"""
    bbox = obj.get("bbox")
    if not bbox:
        return (0, 0, 0, 0)
    # 前端存的是百分比坐标
    x = float(bbox.get("x", 0))
    y = float(bbox.get("y", 0))
    w = float(bbox.get("width", 0))
    h = float(bbox.get("height", 0))
    # 判断是百分比 (0-100) 还是已经是像素 (0-img_w)
    if x > 100 or y > 100 or w > 100 or h > 100:
        # 已经是像素
        x1, y1, x2, y2 = x, y, x + w, y + h
    else:
        # 百分比 → 像素
        x1 = x / 100.0 * img_w
        y1 = y / 100.0 * img_h
        x2 = (x + w) / 100.0 * img_w
        y2 = (y + h) / 100.0 * img_h
    return (x1, y1, x2, y2)


def _generate_coco(work_dir: Path, class_names: dict[str, int], frames_map: dict[int, list],
                   frame_filename: dict, img_w: int, img_h: int, do_split: bool) -> None:
    """生成 COCO detection JSON（参考 CVAT COCO export）。"""
    # 如果划分了 train/val，每个 split 一个 json；否则一个全局 json
    splits = ["train", "val"] if do_split else ["train"]
    if not do_split:
        # 不划分时 frame_filename 的 key 是 ("train", fi)
        pass

    image_id = 1
    ann_id = 1

    def _build_for_split(split_name: str, frame_list: list[tuple]) -> dict:
        nonlocal image_id, ann_id
        coco = {
            "info": {
                "description": f"Sperm Annotation Dataset ({split_name})",
                "version": "1.0",
                "date_created": __import__("datetime").datetime.now().isoformat(),
                "contributor": "标注工具"
            },
            "licenses": [],
            "categories": [
                {"id": cid, "name": name, "supercategory": "sperm"}
                for name, cid in sorted(class_names.items(), key=lambda x: x[1])
            ],
            "images": [],
            "annotations": [],
        }
        for fi, fname in frame_list:
            coco["images"].append({
                "id": image_id,
                "width": img_w,
                "height": img_h,
                "file_name": fname,
                "frame_index": fi,
            })
            for obj in frames_map.get(fi, []):
                x1, y1, x2, y2 = _bbox_px(obj, img_w, img_h)
                bw = max(0.0, x2 - x1)
                bh = max(0.0, y2 - y1)
                if bw < 1 or bh < 1:
                    continue
                area = bw * bh
                coco["annotations"].append({
                    "id": ann_id,
                    "image_id": image_id,
                    "category_id": class_names.get(obj.get("name") or obj.get("objectId") or "object", 0),
                    "segmentation": [],
                    "area": round(area, 2),
                    "bbox": [round(x1, 2), round(y1, 2), round(bw, 2), round(bh, 2)],  # COCO: xywh
                    "iscrowd": 0,
                })
                ann_id += 1
            image_id += 1
        return coco

    if do_split:
        for split in ["train", "val"]:
            split_key = split
            # frame_filename 的 key 是 ("train", fi)
            split_frames = sorted([
                (fi, fname) for (s, fi), fname in frame_filename.items() if s == split_key
            ], key=lambda x: x[0])
            if not split_frames:
                continue
            coco = _build_for_split(split, split_frames)
            out_dir = work_dir / split / "annotations"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "instances.json").write_text(
                json.dumps(coco, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    else:
        all_frames = sorted([
            (fi, fname) for (s, fi), fname in frame_filename.items()
        ], key=lambda x: x[0])
        coco = _build_for_split("train", all_frames)
        out_dir = work_dir / "annotations"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "instances.json").write_text(
            json.dumps(coco, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _generate_yolo(work_dir: Path, class_names: dict[str, int], frames_map: dict[int, list],
                   frame_filename: dict, img_w: int, img_h: int, do_split: bool) -> None:
    """生成 YOLO txt（每行: class_id x_center y_center width height，均归一化 0-1）。"""
    # 还要写 data.yaml
    splits = ["train", "val"] if do_split else ["train"]

    for split in splits:
        split_frames = sorted([
            (fi, fname) for (s, fi), fname in frame_filename.items() if s == split
        ], key=lambda x: x[0])
        if not split_frames:
            continue

        if do_split:
            labels_dir = work_dir / split / "labels"
        else:
            labels_dir = work_dir / "labels"
        labels_dir.mkdir(parents=True, exist_ok=True)

        # 生成每个帧的 label txt
        for fi, fname in split_frames:
            # image filename: train/images/frame_000123.jpg 或 images/frame_000123.jpg
            # label filename: 同名 .txt
            label_fname = fname.rsplit("/", 1)[-1].rsplit(".", 1)[0] + ".txt"
            label_path = labels_dir / label_fname

            lines = []
            for obj in frames_map.get(fi, []):
                x1, y1, x2, y2 = _bbox_px(obj, img_w, img_h)
                bw = max(0.0, x2 - x1)
                bh = max(0.0, y2 - y1)
                if bw < 1 or bh < 1:
                    continue
                cx = (x1 + x2) / 2 / img_w
                cy = (y1 + y2) / 2 / img_h
                nw = bw / img_w
                nh = bh / img_h
                cid = class_names.get(obj.get("name") or obj.get("objectId") or "object", 0)
                lines.append(f"{cid} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

            if lines:
                label_path.write_text("\n".join(lines), encoding="utf-8")
            else:
                label_path.write_text("", encoding="utf-8")

    # 写 data.yaml
    yaml_lines = [
        f"# 数据集路径（根据你的实际目录调整）",
        f"path: {work_dir}",
    ]
    if do_split:
        yaml_lines += [
            f"train: train/images",
            f"val: val/images",
        ]
    else:
        yaml_lines += [
            f"train: images",
        ]
    yaml_lines += [
        f"",
        f"# 类别数",
        f"nc: {len(class_names)}",
        f"",
        f"# 类别名",
        f"names:",
    ]
    for name, cid in sorted(class_names.items(), key=lambda x: x[1]):
        yaml_lines.append(f"  {cid}: '{name}'")

    (work_dir / "data.yaml").write_text("\n".join(yaml_lines), encoding="utf-8")


def _write_dataset_info(work_dir: Path, media_name: str, media_type: str,
                        class_names: dict[str, int], frame_count: int,
                        do_split: bool, split_ratio: float) -> None:
    """写一个 dataset_info.txt 方便训练前看。"""
    lines = [
        f"数据集导出信息",
        f"{'='*60}",
        f"素材名称:    {media_name}",
        f"素材类型:    {media_type}",
        f"标注帧数:    {frame_count}",
        f"类别数:      {len(class_names)}",
        f"类别列表:    {', '.join(f'{name}(id={cid})' for name, cid in sorted(class_names.items(), key=lambda x: x[1]))}",
        f"",
        f"划分设置:",
    ]
    if do_split:
        lines.append(f"  训练集比例:  {split_ratio}")
        lines.append(f"  验证集比例:  {1 - split_ratio}")
    else:
        lines.append(f"  未划分（全部作为训练集）")
    lines.append("")
    lines += [
        f"输出格式说明:",
        f"  COCO:  annotations/instances.json（或 train/val/annotations/instances.json）",
        f"  YOLO: labels/*.txt（每行 class cx cy w h，归一化 0-1）",
        f"  images/: 对应每帧的 JPG 图片",
        f"",
        f"训练命令示例 (YOLOv8):",
        f"  yolo detect train model=yolov8n.pt data=data.yaml epochs=100",
        f"",
        f"训练命令示例 (YOLOv5):",
        f"  python train.py --img 640 --batch 16 --epochs 100 --data data.yaml --weights yolov5n.pt",
    ]
    (work_dir / "dataset_info.txt").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
