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
from .schemas import AuthRequest, ManualAnnotationRequest, TrackRequest
from .tracker import RESULT_FILE_NAME, OVERLAY_FILE_NAME, get_tracker_engine, track_video

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
