from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
import mimetypes
import tempfile
import time
import hashlib

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .auth import current_user, current_video_user, clear_video_session, set_video_session, hash_password, sign_jwt, verify_password
from .config import (
    DATASET_EXPORT_DIR, DB_FILE, DEVICE, DTYPE, HOST, JWT_SECRET, LEGACY_TRACK_DATA_DIR, MAX_PACKAGE_BYTES, MAX_VIDEO_BYTES, MODEL_ID, PORT,
    TRACK_DATA_DIR, TRACK_FRAMES, SAM3_ENABLED,
)
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
    WORKSPACE_STATE_FILE_NAME,
    _probe_video,
    get_tracker_engine,
    track_video,
    rewind_tracking_results,
    _load_latest_manual_baselines,
)
from .review_routes import register_review_routers
from .review_source_lock import source_write
from . import annotation_state, media_reimport

app = FastAPI(title="SAM3 Annotation Backend", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

register_review_routers(app)
from .statistics_export_routes import router as statistics_router
app.include_router(statistics_router)

# GPU tracking strictly serialized for 4GB cards; FastAPI remains responsive.
TRACK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sam3-track")
TASKS: dict[str, dict[str, Any]] = {}
TASK_LOCK = threading.Lock()
UPLOAD_LOCK = threading.Lock()

def require_tracking_enabled() -> None:
    if not SAM3_ENABLED:
        raise HTTPException(503, "此服务器未启用 AI Tracking。请联系部署人员配置 SAM3 模型并启用 GPU 部署；人工标注、审查和导出仍可使用。")

def tracking_is_busy(media_id: str | None = None) -> bool:
    """Any queued/running task, or only those of one video. Writes lock just the
    video being tracked; other videos stay editable while tasks queue on the GPU."""
    with TASK_LOCK:
        return any(item.get("status") in {"queued", "running"} and (media_id is None or item.get("mediaId") == media_id)
                   for item in TASKS.values())


MEDIA_HISTORY_TABLES = ("annotation_reviews", "annotation_review_exclusions", "annotation_export_snapshots", "media_revisions")


def _media_id_has_history(media_id: str) -> bool:
    """Review rounds, exclusions and export snapshots are keyed by mediaId and outlive a
    deleted video. A new video must never reuse such an ID and inherit a review pass."""
    import sqlite3
    from contextlib import closing
    from .db import connect
    with closing(connect()) as conn:
        for table in MEDIA_HISTORY_TABLES:
            try:
                if conn.execute(f"SELECT 1 FROM {table} WHERE media_id=? LIMIT 1", (media_id,)).fetchone():
                    return True
            except sqlite3.OperationalError:
                continue  # Table not migrated yet on this installation.
    return False


def allocate_media_id(stem: str) -> str:
    """同名视频不覆盖旧目录，也不复用已删除视频仍留有审查记录的编号：首次为 stem，后续依次 stem_001、stem_002...。"""
    candidate = stem
    index = 1
    while media_dir(candidate).exists() or _media_id_has_history(candidate):
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
    from .training_export import recover_interrupted
    recover_interrupted()
    TRACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for root in (TRACK_DATA_DIR, LEGACY_TRACK_DATA_DIR):
        media_reimport.recover(root)
        from .tracking_restart import recover as recover_tracking_restart
        recover_tracking_restart(root)
    for directory in iter_media_dirs():
        annotation_state.recover_pause_publication(directory)
    DATASET_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
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
    from .video_frames import frames
    frames.close()
    from .training_export import shutdown as stop_training_exports
    stop_training_exports()
    from .statistics_export_jobs import shutdown as stop_statistics_exports
    stop_statistics_exports()
    TRACK_EXECUTOR.shutdown(wait=False, cancel_futures=True)


@app.get("/api/health", response_model=HealthResponse)
def health() -> dict[str, Any]:
    try:
        engine = get_tracker_engine() if SAM3_ENABLED else None
        model_loaded = bool(engine and engine.model is not None and engine.processor is not None)
    except Exception:
        model_loaded = False
    with TASK_LOCK:
        running = sum(1 for x in TASKS.values() if x["status"] == "running")
        queued = sum(1 for x in TASKS.values() if x["status"] == "queued")
    return {
        "ok": True,
        "service": "sam3-annotation-backend",
        "storage": "sqlite",
        "sam3": {"ok": SAM3_ENABLED, "enabled": SAM3_ENABLED, "modelLoaded": model_loaded, "queued": queued, "running": running, "trackingBusy": (queued + running) > 0, "trackFrames": TRACK_FRAMES},
    }


# ---------------------------- auth ----------------------------
@app.post("/api/auth/register", status_code=201)
def register(req: AuthRequest, request: Request, response: Response) -> dict[str, Any]:
    name = req.username.strip()
    if not name or not req.password:
        raise HTTPException(400, "账号和密码不能为空")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if get_user(name):
        raise HTTPException(409, "账号已存在")
    uid = create_user(name, hash_password(req.password))
    set_video_session(response, request, uid)
    return {"token": sign_jwt({"uid": uid, "username": name}), "user": {"id": uid, "username": name}}


@app.post("/api/auth/login")
def login(req: AuthRequest, request: Request, response: Response) -> dict[str, Any]:
    user = get_user(req.username.strip())
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "账号或密码错误")
    uid = int(user["id"])
    set_video_session(response, request, uid)
    return {"token": sign_jwt({"uid": uid, "username": user["username"]}), "user": {"id": uid, "username": user["username"]}}


@app.get("/api/auth/me")
def me(request: Request, response: Response, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    row = get_user_by_id(user["uid"])
    if not row:
        raise HTTPException(404, "用户不存在")
    set_video_session(response, request, int(row['id']))
    return {"id": int(row["id"]), "username": row["username"]}


@app.post('/api/auth/logout')
def logout(response: Response) -> dict[str, bool]:
    clear_video_session(response)
    return {'ok': True}


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
@source_write
def save_manual(req: ManualAnnotationRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not req.mediaId:
        raise HTTPException(400, "缺少 mediaId")
    if tracking_is_busy(req.mediaId):
        raise HTTPException(409, "该视频正在 AI 追踪，暂时禁止人工标注")
    if not req.objects:
        raise HTTPException(400, "objects 为空")
    width, height = _num(req.mediaWidth), _num(req.mediaHeight)
    raw = req.model_dump_json()
    batch_id = f"batch-{user['uid']}-{uuid.uuid4().hex[:10]}"
    rows = []
    workspace = annotation_state.read_state(media_dir(req.mediaId))
    annotation_state.require_generation(workspace, req.generationId)
    for obj in req.objects:
        # 数据库只记录人工新增/人工修改后的对象；AI Tracking 结果不进入人工结果库。
        if obj.get("source") != "manual":
            continue
        px = _px(obj, width, height)
        bbox = obj.get("bbox") if isinstance(obj.get("bbox"), dict) else None
        point = obj.get("point") if isinstance(obj.get("point"), dict) else None
        # 逐对象取 frameIndex/timestampMs：一次保存可能包含多个视频帧，不能把请求当前帧覆盖所有对象。
        obj_frame_index = int(obj.get("frameIndex", req.frameIndex) or 0)
        if annotation_state.is_deleted(workspace, obj_frame_index, int(obj.get("objectId") or 0)):
            continue
        obj_timestamp_ms = int(round(float(obj.get("timestampMs", req.timestampMs) or 0)))
        rows.append({
            "user_id": user["uid"], "batch_id": batch_id,
            "object_id": str(obj.get("objectId") if obj.get("objectId") is not None else obj.get("id", "")),
            "media_id": req.mediaId, "media_name": req.mediaName, "media_type": req.mediaType,
            "media_width": int(width) if width else None, "media_height": int(height) if height else None,
            "frame_index": obj_frame_index, "timestamp_ms": obj_timestamp_ms,
            "object_name": obj.get("name"), "source": obj.get("source", "manual"),
            "confidence": _num(obj.get("confidence")), "shape_type": "bbox" if bbox else "point",
            "pct_x": _num((bbox or point or {}).get("x")), "pct_y": _num((bbox or point or {}).get("y")),
            "pct_w": _num((bbox or {}).get("width")), "pct_h": _num((bbox or {}).get("height")),
            **px, "annotation_version": req.annotationVersion, "raw_json": raw,
        })
    if not rows:
        raise HTTPException(400, "没有可保存的人工标注对象（AI Tracking 结果不会写入人工标注数据库）")
    insert_annotations(rows)
    return {"id": f"annotation-{batch_id}", "batchId": batch_id, "ok": True, "count": len(rows)}


@app.get("/api/annotation/projects/{project_id}/results")
def results(project_id: str, mediaId: str | None = Query(default=None), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    # 标注结果页查看的是整张人工标注记录表，因此这里不按当前用户限制；
    # 结果仍然只允许已登录用户访问，并且严格只返回 source=manual。
    rows = _visible_manual_records(list_annotations(None, mediaId, source="manual"))
    return {"items": rows, "total": len(rows)}


@app.get("/api/annotation/media/{media_id}")
def results_by_media(media_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    rows = _visible_manual_records(list_annotations(None, media_id, source="manual"))
    return {"items": rows, "total": len(rows)}


def _visible_manual_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    workspaces: dict[str, dict[str, Any]] = {}
    result = []
    for row in rows:
        media_id = str(row.get("media_id") or "")
        if media_id not in workspaces:
            workspaces[media_id] = annotation_state.read_state(media_dir(media_id)) if media_id else {}
        try:
            object_id = int(row.get("object_id") or 0)
        except (ValueError, TypeError):
            object_id = 0
        if not annotation_state.is_deleted(workspaces[media_id], int(row.get("frame_index") or 0), object_id):
            result.append(row)
    return result


@app.delete("/api/annotation/{annotation_id}")
def remove_annotation(annotation_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, bool]:
    if not delete_annotation(annotation_id, user["uid"]):
        raise HTTPException(404, "记录不存在或无权删除")
    return {"ok": True}


# ----------------------------- files -----------------------------
def safe_stem(filename: str) -> str:
    stem = Path(Path(filename).name).stem
    clean = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in stem).strip()
    return clean if clean and clean not in {'.','..'} else f"video-{uuid.uuid4().hex[:8]}"


def media_dir(media_id: str) -> Path:
    """Return the new storage path, or a matching legacy media directory."""
    if not isinstance(media_id, str) or not media_id or media_id in {'.', '..'} or any(c in media_id for c in ('/', '\\', '\x00')):
        raise HTTPException(422, 'mediaId 必须是规范的素材标识')
    safe_id = media_id
    current = TRACK_DATA_DIR / safe_id
    legacy = LEGACY_TRACK_DATA_DIR / safe_id
    if not current.is_dir() and legacy.is_dir():
        return legacy
    return current


def iter_media_dirs() -> list[Path]:
    """List new storage first, then legacy directories not shadowed by it."""
    result: list[Path] = []
    seen: set[str] = set()
    for root in (TRACK_DATA_DIR, LEGACY_TRACK_DATA_DIR):
        if not root.is_dir():
            continue
        for directory in sorted(root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("_") or directory.name in seen:
                continue
            seen.add(directory.name)
            result.append(directory)
    return result


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
        for d in iter_media_dirs():
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
        for d in iter_media_dirs():
            # 目录名本身就是 stem（790663... 这种）
            if d.name == stem:
                return d
            for f in d.iterdir():
                if f.is_file() and f.name == media_name:
                    return d

    return None


def find_video(directory: Path) -> Path | None:
    """Return the original source video without requiring MP4 or conversion."""
    if not directory.is_dir():
        return None
    meta_file = directory / "media.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            candidate = directory / Path(str(meta.get("videoName", ""))).name
            if candidate.is_file() and candidate.name != OVERLAY_FILE_NAME:
                return candidate
        except Exception:
            pass
    ignored_names = {OVERLAY_FILE_NAME, "media.json", RESULT_FILE_NAME}
    ignored_suffixes = {".json", ".jsonl", ".db", ".txt", ".log", ".jpg", ".jpeg", ".png"}
    for x in sorted(directory.iterdir()):
        if not x.is_file() or x.name in ignored_names:
            continue
        if x.name.startswith(".") or x.suffix.lower() in ignored_suffixes:
            continue
        try:
            import cv2
            cap = cv2.VideoCapture(str(x))
            opened = cap.isOpened()
            cap.release()
            if opened:
                return x
        except Exception:
            continue
    return None


def _video_media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed if guessed and guessed.startswith("video/") else "application/octet-stream"


def source_media_info(directory: Path) -> dict[str, Any]:
    video = find_video(directory)
    if not video:
        raise HTTPException(404, '素材不存在')
    try:
        info = json.loads((directory / 'media.json').read_text(encoding='utf-8'))
        if not isinstance(info, dict) or any(type(info.get(k)) not in (int, float) or info[k] <= 0 for k in ('width', 'height', 'frameCount')):
            raise ValueError('incomplete media metadata')
        return info
    except (OSError, ValueError):
        try:
            return _probe_video(video)
        except Exception as exc:
            logging.getLogger('review.media').exception('media.metadata_read_failed media=%s', directory.name)
            raise HTTPException(500, '原视频元数据无法读取，请检查存储并重试') from exc


async def save_upload(upload: UploadFile, target: Path) -> int:
    return await run_in_threadpool(_save_upload_stream,upload.file,target)


def _save_upload_stream(stream, target: Path, limit: int = MAX_VIDEO_BYTES) -> int:
    total = 0
    with target.open("wb") as out:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, f"文件超过大小限制 {limit} bytes")
            out.write(chunk)
    return total


@app.post("/api/track/upload", status_code=201)
async def upload_video(file: UploadFile = File(...), user: dict[str, Any] = Depends(current_user)):
    filename = Path(file.filename or "video.bin").name
    if not Path(filename).suffix:
        raise HTTPException(400, "视频文件必须包含扩展名")
    TRACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="_upload-", dir=TRACK_DATA_DIR) as tmp:
            target = Path(tmp) / filename
            size = await save_upload(file, target)
            if size <= 0:
                raise HTTPException(400, "视频文件为空")
            try:
                video_meta = await run_in_threadpool(_probe_video,target)
            except Exception as exc:
                raise HTTPException(400, f"无法解码该视频格式：{exc}") from exc
            if any(float(video_meta.get(key) or 0) <= 0 for key in ("frameCount", "width", "height", "fps")):
                raise HTTPException(400, "上传文件不可读取，或无法获得有效的帧数、尺寸、FPS 元数据")
            return await run_in_threadpool(publish_upload,target, filename, video_meta, user["uid"])
    finally:
        await file.close()


@app.post("/api/track/import-package", status_code=201)
async def import_annotation_package(file: UploadFile = File(...), user: dict[str, Any] = Depends(current_user)):
    """Import a training ZIP exported with original videos; each video arrives unreviewed."""
    from .package_import import import_package
    TRACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="_import-", dir=TRACK_DATA_DIR) as tmp:
            package = Path(tmp) / "package.zip"
            if await run_in_threadpool(_save_upload_stream, file.file, package, MAX_PACKAGE_BYTES) <= 0:
                raise HTTPException(400, "文件为空")
            return await run_in_threadpool(import_package, package, int(user["uid"]), Path(tmp))
    finally:
        await file.close()


@source_write
def _upload_source_snapshot():
    return [(directory,video,media_reimport.source_fingerprint(video))
            for directory in iter_media_dirs() if (video := find_video(directory))]


def publish_upload(target, filename, video_meta, uid):
    began = time.perf_counter()
    # Imports serialize with each other, but long hashing/decoding must not hold
    # the lock needed by another video's annotation/review writes.
    with UPLOAD_LOCK:
        for attempt in range(3):
            snapshot = _upload_source_snapshot()
            videos = {directory:video for directory,video,_ in snapshot}
            try:
                existing,sha = media_reimport.duplicate(videos, videos.get, target)
            except OSError as exc:
                if snapshot != _upload_source_snapshot():
                    continue  # Deletion during hashing: take a fresh source snapshot.
                logging.getLogger('review.media').exception('media.upload_prepare_failed actor=%s',uid)
                raise HTTPException(500,'上传视频查重失败，请检查存储并重试') from exc
            result = _commit_upload(target,filename,video_meta,uid,snapshot,existing,sha)
            if result is not None:
                logging.getLogger('review.media').info('media.upload_prepared actor=%s elapsed_ms=%.1f retries=%s',uid,(time.perf_counter()-began)*1000,attempt)
                return result
        raise HTTPException(409,'素材库在导入期间发生变化，请重试上传')


@source_write
def _commit_upload(target, filename, video_meta, uid, snapshot, existing, sha):
    if snapshot != _upload_source_snapshot():
        return None
    if existing:
        duplicate = media_reimport.duplicate_data(existing, uid)
        source = find_video(existing)
        duplicate["media"] = {"mediaId": existing.name, "videoName": source.name,
                              "videoUrl": f"/api/track/video/{existing.name}", **video_meta,
                              "duration": video_meta["frameCount"] / video_meta["fps"]}
        return JSONResponse(status_code=409, content={"code": "DUPLICATE_VIDEO", "message": "该视频已存在，是否覆盖重新标注？", "duplicate": duplicate})
    media_id = allocate_media_id(safe_stem(filename))
    directory = media_dir(media_id)
    directory.mkdir(parents=True, exist_ok=False)
    video = directory / filename
    try:
        target.replace(video)
        duration = video_meta["frameCount"] / video_meta["fps"]
        meta = {"mediaId": media_id, "videoName": filename, "videoPath": str(video.resolve()), "videoMimeType": _video_media_type(video), "createdBy": uid, "sha256": sha, **video_meta, "duration": duration}
        meta['sourceFingerprint'] = media_reimport.source_fingerprint(video)
        (directory / "media.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        import shutil
        shutil.rmtree(directory)
        raise
    logging.getLogger("review.media").info("media.uploaded media=%s actor=%s sha=%s", media_id, uid, sha)
    return {"mediaId": media_id, "videoName": filename, "videoUrl": f"/api/track/video/{media_id}", "videoMimeType": meta["videoMimeType"], **video_meta, "duration": duration}


@app.post("/api/track/annotations", status_code=201)
@source_write
def save_frame_annotations(req: dict[str, Any], user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if tracking_is_busy(str(req.get("mediaId") or "")):
        raise HTTPException(409, "该视频正在 AI 追踪，暂时禁止人工标注")
    frame = req.get("frameIndex", -1)
    annotations = req.get("annotations")
    if not req.get("mediaId") or type(frame) is not int or frame < 0 or not isinstance(annotations, list) or not annotations:
        raise HTTPException(400, "mediaId/frameIndex/annotations 无效")
    directory = media_dir(req['mediaId'])
    info = source_media_info(directory)
    if frame >= info['frameCount']:
        raise HTTPException(422, 'seed 帧号超过原视频范围')

    # Seed identity 必须由前端显式传递并保持稳定；严禁按数组顺序重新编号。
    normalized_annotations: list[dict[str, Any]] = []
    seen_object_ids: set[int] = set()
    for idx, raw in enumerate(annotations):
        if not isinstance(raw, dict):
            raise HTTPException(400, f"annotations[{idx}] 无效")
        object_id_raw = raw.get("object_id", raw.get("objectId"))
        if type(object_id_raw) is not int:
            raise HTTPException(400, f"annotations[{idx}] 缺少有效 object_id")
        object_id = object_id_raw
        if object_id <= 0:
            raise HTTPException(400, f"annotations[{idx}] object_id 必须大于 0")
        if object_id in seen_object_ids:
            raise HTTPException(400, f"当前帧存在重复 object_id={object_id}")
        seen_object_ids.add(object_id)
        ann = dict(raw)
        ann["object_id"] = object_id
        ann["objectId"] = object_id
        ann["frameIndex"] = ann.get("frameIndex", frame)
        if type(ann['frameIndex']) is not int or ann["frameIndex"] != frame:
            raise HTTPException(400, f"annotations[{idx}] frameIndex 与当前 seed frame 不一致")
        normalized_annotations.append(ann)
    annotation_state.validate_workspace_geometry({'manualBaselines': [
        {**ann, 'objectId':ann['object_id'], 'source':'manual'} for ann in normalized_annotations
    ]}, info)
    workspace = annotation_state.read_state(directory)
    annotation_state.require_generation(workspace, req.get("generationId"))
    if any(annotation_state.is_deleted(workspace, frame, ann["object_id"]) for ann in normalized_annotations):
        raise HTTPException(409, "当前 seed 包含已删除的对象，请重新读取工作区")
    payload = {
        "media": {"id": req["mediaId"], "name": req.get("mediaName") or f"{req['mediaId']}.mp4", "type": "video", "width": info['width'], "height": info['height']},
        "frame": {"frameIndex": frame, "timestampMs": req.get("timestampMs", 0)},
        "coordinateSystem": {"source": "frontend-pixel", "target": "pixel", "bbox": "[x1, y1, x2, y2]"},
        "annotations": normalized_annotations,
    }
    filename = f"annotations_frame_{frame:06d}.json"
    temporary = directory / f'.{filename}.{uuid.uuid4().hex}.tmp'
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        temporary.replace(directory / filename)
    except OSError as exc:
        logging.getLogger('review.annotation').exception('annotation.seed_save_failed media=%s frame=%s actor=%s', req['mediaId'], frame, user['uid'])
        raise HTTPException(500, 'seed 保存失败，旧标注已保留，请重试') from exc
    finally:
        temporary.unlink(missing_ok=True)
    logging.getLogger('review.annotation').info('annotation.seed_saved media=%s frame=%s actor=%s objects=%s', req['mediaId'], frame, user['uid'], len(normalized_annotations))
    return {"filename": filename, "frameIndex": frame}


def _legacy_workspace_state(directory: Path, media_id: str) -> dict[str, Any] | None:
    """Build initial workspace state from durable manual seed files."""
    meta: dict[str, Any] = {}
    try:
        meta = json.loads((directory / "media.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    width = float(meta.get("width") or 0)
    height = float(meta.get("height") or 0)
    fps = float(meta.get("fps") or 0)
    if width <= 0 or height <= 0:
        return None

    annotations: dict[tuple[int, int], dict[str, Any]] = {}
    baselines: dict[int, dict[str, Any]] = {}
    for path in sorted(directory.glob("annotations_frame_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            frame_index = int(payload.get("frame", {}).get("frameIndex", -1))
            timestamp_ms = int(payload.get("frame", {}).get("timestampMs") or (round(frame_index * 1000 / fps) if fps > 0 else 0))
        except (OSError, TypeError, ValueError, AttributeError) as exc:
            logging.getLogger('review.annotation').exception('annotation.legacy_seed_read_failed media=%s file=%s', media_id, path.name)
            raise HTTPException(500, '历史人工标注无法读取，请检查 seed 文件并重试') from exc
        if frame_index < 0:
            raise HTTPException(500, '历史人工标注帧号损坏，请检查 seed 文件')
        if not isinstance(payload.get('annotations'), list):
            raise HTTPException(500, '历史人工标注结构损坏，请检查 seed 文件')
        for raw in payload.get("annotations", []):
            if not isinstance(raw,dict):
                raise HTTPException(500,'历史 seed 对象结构损坏，请检查存储')
            if raw.get("source") != "manual":
                continue
            try:
                object_id = int(raw.get("object_id", raw.get("objectId")))
                x1, y1, x2, y2 = [float(value) for value in raw["bbox"]]
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(500, '历史人工标注坐标或身份损坏，请检查 seed 文件') from exc
            if object_id <= 0 or x2 <= x1 or y2 <= y1 or (frame_index, object_id) in annotations:
                raise HTTPException(500, '历史人工标注坐标或身份损坏，请检查 seed 文件')
            name = str(raw.get("name") or f"object-{object_id}")
            annotations[(frame_index, object_id)] = {
                "id": f"manual-{frame_index}-{object_id}",
                "objectId": object_id,
                "name": name,
                "source": "manual",
                "bbox": {
                    "x": x1 / width * 100,
                    "y": y1 / height * 100,
                    "width": (x2 - x1) / width * 100,
                    "height": (y2 - y1) / height * 100,
                },
                "frameIndex": frame_index,
                "timestampMs": timestamp_ms,
            }
            previous = baselines.get(object_id)
            if previous is None or frame_index >= int(previous["frameIndex"]):
                baselines[object_id] = {
                    "objectId": object_id,
                    "name": name,
                    "source": "manual",
                    "frameIndex": frame_index,
                    "bbox": [x1, y1, x2, y2],
                }
    if not annotations:
        return None
    state = {
        "exists": True,
        "format": "annotation-workspace-v1",
        "mediaId": media_id,
        "currentFrame": 0,
        "manualAnnotations": list(annotations.values()),
        "manualBaselines": list(baselines.values()),
        "deletedTrackingIds": [],
        "anomalyFrames": [],
        "pausedAnomalies": [],
        "lastPausedContext": None,
        "display": {"brightness": 100, "contrast": 100, "zoom": 1},
        "migratedFrom": "annotations_frame_*.json",
    }
    try:
        annotation_state.validate_workspace_geometry(state,meta)
    except HTTPException as exc:
        logging.getLogger('review.annotation').exception('annotation.legacy_seed_read_failed media=%s',media_id)
        raise HTTPException(500,'历史人工标注数据损坏，请检查 seed 文件') from exc
    return state


_WORKSPACE_STAMPS: dict[str, tuple[tuple[int, int], int, Any, Any]] = {}
_STAMP_LOCK = threading.Lock()


def _file_stamp(path: Path) -> list[int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return [stat.st_mtime_ns, stat.st_size]


@app.get("/api/track/workspace/{media_id}/stamp")
def get_workspace_stamp(media_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Cheap collaboration poll: who changed what, without the global source lock.

    Workspace and tracking files are replaced atomically, so an unlocked read sees a
    whole old or new file. The revision is parsed only when the file stamp changed.
    """
    directory = media_dir(media_id)
    if not directory.is_dir():
        raise HTTPException(404, "素材不存在")
    path = directory / WORKSPACE_STATE_FILE_NAME
    stamp = _file_stamp(path)
    revision, generation, updated_by = 0, None, None
    if stamp:
        key = (stamp[0], stamp[1])
        with _STAMP_LOCK:
            cached = _WORKSPACE_STAMPS.get(media_id)
        if cached and cached[0] == key:
            _, revision, generation, updated_by = cached
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                revision, generation, updated_by = int(payload.get("revision", 0)), payload.get("generationId"), payload.get("updatedBy")
            except (OSError, ValueError, TypeError) as exc:
                raise HTTPException(503, "工作区正在写入，请稍后重试") from exc
            with _STAMP_LOCK:
                _WORKSPACE_STAMPS[media_id] = (key, revision, generation, updated_by)
    with TASK_LOCK:
        task = next((t for t in TASKS.values() if t.get("mediaId") == media_id and t.get("status") in {"queued", "running"}), None)
    tracking = None
    if task:
        from .db import get_user_by_id
        owner = get_user_by_id(int(task["userId"])) if task.get("userId") is not None else None
        tracking = {"userId": task.get("userId"), "userName": owner["username"] if owner else None, "status": task["status"]}
    editor = None
    if updated_by is not None:
        from .db import get_user_by_id
        row = get_user_by_id(int(updated_by))
        editor = row["username"] if row else None
    return {"mediaId": media_id, "revision": revision, "generationId": generation, "updatedBy": updated_by,
            "updatedByName": editor, "trackingStamp": _file_stamp(directory / RESULT_FILE_NAME), "tracking": tracking}


@app.get("/api/track/workspace/{media_id}")
@source_write
def get_workspace_state(media_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Return durable per-video editor state without duplicating tracker JSONL."""
    directory = media_dir(media_id)
    if not directory.is_dir() or not find_video(directory):
        raise HTTPException(404, "素材不存在")
    from .tracking_restart import recover_directory
    recover_directory(directory)
    annotation_state.recover_pause_publication(directory)
    path = directory / WORKSPACE_STATE_FILE_NAME
    if not path.is_file():
        return {**(_legacy_workspace_state(directory, media_id) or {"exists": False, "mediaId": media_id}), "revision": 0}
    payload = annotation_state.read_state(directory)
    try:
        annotation_state.validate_deletions(payload)
        annotation_state.validate_workspace_geometry(payload, source_media_info(directory))
    except HTTPException as exc:
        logging.getLogger('review.annotation').exception('annotation.workspace_data_invalid media=%s',media_id)
        raise HTTPException(500,'工作区数据损坏，不能将未知状态当作空标注，请检查存储') from exc
    return {**annotation_state.public_state(payload), "exists": True, "mediaId": media_id, "revision": int(payload.get("revision", 0))}


@app.put("/api/track/workspace/{media_id}")
@source_write
def save_workspace_state(media_id: str, payload: dict[str, Any], user: dict[str, Any] = Depends(current_user), idempotency_key: str = Header(default="")) -> dict[str, Any]:
    """Atomically persist editor-only state beside the source video."""
    directory = media_dir(media_id)
    if not directory.is_dir() or not find_video(directory):
        raise HTTPException(404, "素材不存在")
    manual_annotations = payload.get("manualAnnotations", [])
    manual_baselines = payload.get("manualBaselines", [])
    if not isinstance(manual_annotations, list) or not isinstance(manual_baselines, list):
        raise HTTPException(400, "manualAnnotations/manualBaselines 必须是数组")
    if len(manual_annotations) > 1_000_000 or len(manual_baselines) > 100_000:
        raise HTTPException(413, "工作区状态过大")

    def build(previous: dict[str, Any]) -> dict[str, Any]:
        clean = {**previous, **{k: v for k, v in payload.items() if k not in annotation_state.SERVER_FIELDS and not k.startswith("_")}}
        annotation_state.validate_deletions(clean)
        annotation_state.validate_workspace_geometry(clean, source_media_info(directory))
        if tracking_is_busy(media_id) and any(clean.get(field, []) != previous.get(field, []) for field in ("manualAnnotations", "manualBaselines", "deletedObjectIds", "deletedFrameObjects", "deletedTrackingIds")):
            raise HTTPException(409, "AI Tracking 正在运行，暂时不能修改标注或删除范围")
        if payload.get("expectedRevision") is None:
            for field in ("deletedObjectIds", "deletedFrameObjects"):
                if field in payload and payload[field] != previous.get(field, []):
                    raise HTTPException(428, "修改删除范围需要工作区版本，请刷新页面")
                clean[field] = previous.get(field, [])
            legacy_deleted = payload.get('deletedTrackingIds', [])
            if not isinstance(legacy_deleted, list):
                raise HTTPException(422, '历史删除范围必须为数组')
            existing_legacy_deleted = previous.get('deletedTrackingIds', [])
            if set(map(str, legacy_deleted)) - set(map(str, existing_legacy_deleted)):
                raise HTTPException(428, '修改删除范围需要工作区版本，请刷新页面')
            # Stale legacy snapshots may omit tombstones; preserve the current
            # set, but never allow an unversioned request to add deletions.
            clean['deletedTrackingIds'] = existing_legacy_deleted
        known_deleted = set(previous.get("deletedAnnotationFrames", []))
        for obj in [*previous.get("manualAnnotations", []), *manual_annotations]:
            frame = int(obj.get("frameIndex", 0))
            if annotation_state.is_deleted(clean, frame, int(obj.get("objectId") or 0)):
                known_deleted.add(frame)
        clean["deletedAnnotationFrames"] = sorted(known_deleted)
        # An old client may resend boxes cached before a deletion; control fields
        # remain authoritative and those boxes never become effective again.
        clean["manualAnnotations"] = [obj for obj in manual_annotations if not annotation_state.is_deleted(clean, int(obj.get("frameIndex", 0)), int(obj.get("objectId") or 0))]
        clean["manualBaselines"] = [obj for obj in manual_baselines if not annotation_state.is_deleted(clean, int(obj.get("frameIndex", 0)), int(obj.get("objectId") or 0))]
        annotation_state.preserve_pause(previous, clean, payload, directory)
        return clean
    return annotation_state.write_state(directory, media_id, int(user["uid"]), idempotency_key if isinstance(idempotency_key, str) else "", payload, "workspace", build)


@app.get("/api/track/deletion-preview/{media_id}/{object_id}")
@source_write
def preview_annotation_deletion(media_id: str, object_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    directory = media_dir(media_id)
    if object_id <= 0:
        raise HTTPException(422, "objectId 必须大于 0")
    if not directory.is_dir() or not find_video(directory):
        raise HTTPException(404, "素材不存在")
    if tracking_is_busy(media_id):
        raise HTTPException(409, "该视频正在 AI 追踪，请等待完成再删除")
    return annotation_state.deletion_preview(directory, object_id)


@app.post("/api/track/feedback/{media_id}")
@source_write
def save_tracking_feedback(media_id: str, payload: dict[str, Any], user: dict[str, Any] = Depends(current_user), idempotency_key: str = Header(default="")) -> dict[str, Any]:
    directory = media_dir(media_id)
    if not directory.is_dir() or not find_video(directory):
        raise HTTPException(404, "素材不存在")
    if tracking_is_busy(media_id):
        raise HTTPException(409, "该视频正在 AI 追踪，请等待完成再确认")
    return annotation_state.write_state(directory, media_id, int(user["uid"]), idempotency_key if isinstance(idempotency_key, str) else "", payload, "feedback", lambda previous: annotation_state.feedback_state(previous, directory, payload, int(user["uid"])))


# ---------------------------- tracking ----------------------------
def _set_task(task_id: str, **patch: Any) -> None:
    with TASK_LOCK:
        TASKS[task_id] = {**TASKS.get(task_id, {}), **patch}


def _effective_track_frames(start_frame: int, total_frames: int, requested_frames: int) -> int:
    """Return the bounded frame count, including the seed frame."""
    remaining = max(0, int(total_frames) - int(start_frame))
    return min(max(1, int(requested_frames)), TRACK_FRAMES, remaining) if remaining else 0


def _require_pause_resolved(workspace: dict[str, Any], frame: int) -> None:
    context = workspace.get("lastPausedContext") or {}
    pause_frame = context.get("frameIndex", frame)
    unresolved = [item for item in workspace.get("pausedAnomalies", []) if not item.get("resolved") and not annotation_state.is_deleted(workspace, pause_frame, int(item.get("object_id", item.get("objectId", 0))))]
    if unresolved:
        raise HTTPException(409, "请逐个明确确认暂停帧的异常；要从更早帧重新追踪，请先确认重建追踪分支")


def _run_tracking_task(task_id: str, req: TrackRequest, video: Path, seed_file: Path, output_file: Path) -> None:
    began = time.perf_counter()
    log = logging.getLogger('review.tracking')
    _set_task(task_id, status="running", stage='decoding', message="正在准备本轮原始帧")
    log.info('tracking.task_started task=%s media=%s start=%s max_frames=%s', task_id, req.mediaId, req.startFrame, req.maxFrames)
    try:
        workspace = annotation_state.read_state(video.parent)
        options = dict(bbox_mode="pixel", normal_feedback=workspace.get("normalMotionSamples", []),
                       progress=lambda **patch: _set_task(task_id, elapsedSeconds=round(time.perf_counter() - began, 1), **patch))
        if req.edgeDiscovery: options['edge_discovery'] = True
        if req.reviewMode: options['review_mode'] = True
        if req.terminalAnnotations is not None: options['endpoint_mode'] = True
        position, continuation = req.startFrame, None
        while True:
            result = track_video(str(video), str(seed_file), str(output_file), max_frames=req.maxFrames,
                                 start_frame=position, **options,
                                 **({'continuation_row': continuation} if continuation is not None else {}))
            if not req.reviewMode or result.get('reachedVideoEnd'):
                break
            last = result.get('lastProcessedFrame', position)
            if last <= position:
                raise RuntimeError('追踪未向前推进，请检查已保存的片段后重试')
            continuation = next((r for r in result.get('frames', []) if r['source_frame_index'] == last), None)
            if not continuation or not continuation.get('objects'):
                raise RuntimeError('当前片段已没有可继续追踪的对象，已保留结果；请检查跟丢位置后重新设置起始框')
            log.info('tracking.review_continue task=%s frame=%s objects=%s', task_id, last, len(continuation['objects']))
            position = last
        if req.terminalAnnotations is not None:
            from .endpoint_tracking import verify_endpoints
            from .tracker import get_sam3_engine, MODEL_ID, DEVICE, DTYPE, _merge_rows
            engine = get_sam3_engine(MODEL_ID, DEVICE, DTYPE)
            reverse = {} if req.combineResults else None
            report = verify_endpoints(video, engine, result['frames'], req.terminalAnnotations,
                                      window_size=max(2, min(16, req.maxFrames)), progress=options['progress'],
                                      **({'reverse_observations': reverse, 'joint_mode': True} if req.combineResults else {}))
            if req.combineResults:
                from .joint_tracking import synthesize_video
                from copy import deepcopy
                try:
                    combined_rows = deepcopy(result['frames'])
                    joint = synthesize_video(video, combined_rows, reverse, report, workspace, progress=options['progress'])
                    result['frames'] = combined_rows
                except Exception as exc:
                    log.exception('tracking.joint_failed task=%s media=%s', task_id, req.mediaId)
                    for row in result['frames']:
                        evidence = row.setdefault('reviewEvidence', {'state':'not_sampled','findings':[]})
                        evidence['jointCheck'] = {'state':'failed','error':str(exc)}
                        evidence.setdefault('findings', []).append({'objectId':None,'kind':'check_failed','message':'综合标注未完成，请检查画面或重新生成'})
                    _merge_rows(output_file, result['frames'], keep_before_source_frame=0)
                    _set_task(task_id, jointTracking={'state':'failed','error':str(exc)})
                    raise
                _set_task(task_id, jointTracking=joint)
                result['warningSummary'] = []  # Old forward-only geometry diagnostics are stale after synthesis.
            result['frames'] = _merge_rows(output_file, result['frames'], keep_before_source_frame=0)
            _set_task(task_id, endpointVerification=report)
        anomaly_paused = result.get("anomaly_paused")
        # The real tracker publishes pause metadata with the JSONL intent;
        # finish an interrupted publication before reporting a successful task.
        annotation_state.recover_pause_publication(video.parent)
        if anomaly_paused:
            _set_task(
                task_id,
                status="paused",
                stage='paused',
                message=f"Anomaly detected at frame {anomaly_paused['frame_index']}",
                paused=True,
                pausedFrame=anomaly_paused["frame_index"],
                pausedObjects=anomaly_paused.get("reasons", []),
                anomalyLevels=anomaly_paused.get("levels", {}),
                processedFrames=result.get("processedFrames", 0),
                lastProcessedFrame=result.get("lastProcessedFrame"),
                reachedVideoEnd=result.get("reachedVideoEnd", False),
                warningSummary=result.get("warningSummary", []),
            )
        else:
            _set_task(
                task_id,
                status="success",
                stage='completed',
                message="completed",
                processedFrames=result.get("processedFrames", 0),
                lastProcessedFrame=result.get("lastProcessedFrame"),
                reachedVideoEnd=result.get("reachedVideoEnd", False),
                warningSummary=result.get("warningSummary", []),
            )
        log.info('tracking.task_finished task=%s media=%s elapsed_ms=%.1f', task_id, req.mediaId, (time.perf_counter() - began) * 1000)
    except Exception as exc:
        log.exception('tracking.task_failed task=%s media=%s elapsed_ms=%.1f', task_id, req.mediaId, (time.perf_counter() - began) * 1000)
        _set_task(task_id, status="failed", message=str(exc))



@app.post("/api/track/rewind")
@source_write
def rewind_tracking(req: dict[str, Any], user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Start a new tracking branch from the current human-corrected frame.

    Only rows strictly after startFrame in the previous SAM3 JSONL branch are deleted;
    the start frame itself remains as the new authoritative seed anchor. This is deliberately separate
    from /track/annotations so that only an explicit AI Tracking click rewinds the
    previous future branch.
    """
    # The frontend rewinds before starting inference. Reject disabled AI here
    # as well, so switching to CPU mode cannot truncate prior tracking results.
    require_tracking_enabled()
    media_id = str(req.get("mediaId") or "").strip()
    if tracking_is_busy(media_id):
        raise HTTPException(409, "该视频已有追踪任务在排队或运行，请等待完成")
    try:
        start_frame = int(req.get("startFrame"))
    except (TypeError, ValueError):
        raise HTTPException(400, "startFrame 无效")
    if not media_id or start_frame < 0:
        raise HTTPException(400, "mediaId/startFrame 无效")

    directory = media_dir(media_id)
    annotation_state.require_generation(annotation_state.read_state(directory), req.get("generationId"))
    _require_pause_resolved(annotation_state.read_state(directory), start_frame)
    video = find_video(directory)
    if not video:
        raise HTTPException(404, "视频文件不存在，请重新上传")
    from .tracking_restart import rewind as rewind_branch
    info = rewind_branch(directory, start_frame, uid=int(user['uid']))
    return {"ok": True, "mediaId": media_id, **info}


@app.post("/api/track/restart-branch/{media_id}")
@source_write
def restart_tracking_branch(media_id: str, payload: dict[str, Any], user: dict[str, Any] = Depends(current_user), idempotency_key: str = Header(default="")) -> dict[str, Any]:
    from .tracking_restart import restart
    require_tracking_enabled()
    directory = media_dir(media_id)
    return restart(directory, int(user["uid"]), idempotency_key, payload, source_media_info(directory), lambda: tracking_is_busy(media_id))


@app.post("/api/track", status_code=202)
@source_write
def start_tracking(req: TrackRequest, user: dict[str, Any] = Depends(current_user), idempotency_key: str = Header(default='')) -> dict[str, Any]:
    require_tracking_enabled()
    request_key = idempotency_key if isinstance(idempotency_key, str) else ''
    if len(request_key) > 128:
        raise HTTPException(422, '追踪请求重试标识过长')
    request_hash = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
    if request_key:
        with TASK_LOCK:
            prior = next((t for t in TASKS.values() if t.get('userId') == user['uid'] and t.get('requestKey') == request_key), None)
        if prior:
            if prior['requestHash'] != request_hash:
                raise HTTPException(409, '同一重试标识不能用于不同追踪输入')
            annotation_state.require_generation(annotation_state.read_state(media_dir(req.mediaId)), req.generationId)
            logging.getLogger('review.tracking').info('tracking.start_replay task=%s media=%s', prior['taskId'], req.mediaId)
            return {'taskId': prior['taskId'], 'status': 'queued', 'maxFrames': prior['maxFrames'], 'trackFrames': prior['maxFrames']}
    if tracking_is_busy(req.mediaId):
        raise HTTPException(409, "该视频已有追踪任务在排队或运行，请等待完成")
    if not req.annotations:
        raise HTTPException(400, "没有 Tracking seed bbox")

    # 与 /track/annotations 相同的强校验：直接调用 /track 也不能绕过稳定 ID 约束。
    seen_object_ids: set[int] = set()
    normalized_request_annotations: list[dict[str, Any]] = []
    for idx, raw in enumerate(req.annotations):
        if not isinstance(raw, dict):
            raise HTTPException(400, f"annotations[{idx}] 无效")
        raw_id = raw.get("object_id", raw.get("objectId"))
        try:
            object_id = int(raw_id)
        except (TypeError, ValueError):
            raise HTTPException(400, f"annotations[{idx}] 缺少有效 object_id")
        if object_id <= 0:
            raise HTTPException(400, f"annotations[{idx}] object_id 必须大于 0")
        if object_id in seen_object_ids:
            raise HTTPException(400, f"当前 Tracking seed 存在重复 object_id={object_id}")
        seen_object_ids.add(object_id)
        ann = dict(raw)
        ann["object_id"] = object_id
        ann["objectId"] = object_id
        ann["frameIndex"] = int(ann.get("frameIndex", req.startFrame))
        if ann["frameIndex"] != req.startFrame:
            raise HTTPException(400, f"annotations[{idx}] frameIndex 与 startFrame 不一致")
        normalized_request_annotations.append(ann)

    req.annotations = normalized_request_annotations
    directory = media_dir(req.mediaId)
    workspace = annotation_state.read_state(directory)
    annotation_state.require_generation(workspace, req.generationId)
    if any(annotation_state.is_deleted(workspace, req.startFrame, ann["object_id"]) for ann in req.annotations):
        raise HTTPException(409, "当前 seed 包含已删除的对象，请重新读取工作区")
    _require_pause_resolved(workspace, req.startFrame)
    video = find_video(directory)
    if not video:
        raise HTTPException(404, "视频文件不存在，请重新上传")
    video_info = _probe_video(video)
    video_frame_count = int(video_info.get("frameCount") or 0)
    if req.terminalAnnotations is not None:
        from .endpoint_tracking import validate_endpoints
        if req.startFrame != 0 or not req.reviewMode:
            raise HTTPException(422, '首尾双向追踪必须从首帧开始，并使用集中审查模式')
        try:
            validate_endpoints(workspace, req.annotations, req.terminalAnnotations,
                               video_info['width'], video_info['height'], video_frame_count)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if req.combineResults and req.terminalAnnotations is None:
        raise HTTPException(422, '综合标注需要人工首尾帧，请先完成首尾标注')
    effective_frames = _effective_track_frames(req.startFrame, video_frame_count, req.maxFrames)
    if effective_frames < 1:
        raise HTTPException(400, "startFrame 已超出视频末尾")
    # maxFrames includes the seed frame.  Environment configuration is always
    # the upper bound, regardless of what a client submits.
    req.maxFrames = effective_frames
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
    _set_task(task_id, taskId=task_id, status="queued", stage='queued', message="queued", mediaId=req.mediaId, startFrame=req.startFrame, maxFrames=req.maxFrames, userId=user["uid"], requestKey=request_key, requestHash=request_hash)
    TRACK_EXECUTOR.submit(_run_tracking_task, task_id, req, video, seed_file, output_file)
    return {"taskId": task_id, "status": "queued", "maxFrames": req.maxFrames, "trackFrames": req.maxFrames}


@app.get("/api/track/status/{task_id}")
def tracking_status(task_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with TASK_LOCK:
        item = TASKS.get(task_id)
    if not item:
        raise HTTPException(404, "task not found")
    return {k: v for k, v in item.items() if k not in {'userId', 'requestKey', 'requestHash'}}


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
    rows = annotation_state.read_rows(directory)
    workspace = annotation_state.read_state(directory)

    latest_frame = max(
        (int(row.get("source_frame_index", row.get("frame_index", 0))) for row in rows),
        default=0,
    )
    manual_baselines = _load_latest_manual_baselines(directory / "__anomaly_scan__.json", latest_frame)
    detector = AnomalyDetector(
        config=AnomalyConfig(),
        fps=fps,
        frame_width=width,
        frame_height=height,
        manual_baselines=manual_baselines,
        normal_feedback=workspace.get("normalMotionSamples", []),
    )
    all_frames: list[AnomalyFrameOut] = []
    summary: dict[str, int] = {"anomaly": 0, "warning": 0, "disappeared": 0}
    pause_at: int | None = None

    for row in rows:
        fi = int(row.get("frame_index", 0))
        frame_objs: dict[int, list[float]] = {}
        for obj in row.get("objects", []):
            frame_objs[int(obj["object_id"])] = list(obj["bbox"])
        ignored_ids = {oid for oid in frame_objs if annotation_state.is_deleted(workspace, fi, oid)}
        report = detector.push(fi, frame_objs, ignored_object_ids=ignored_ids)

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

    Raw-frame mode uses the same frame index as the source video. No interpolation
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
                "id": f"ai-{frame_index}-{object_id}",
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
                "anomaly_level": obj.get("anomaly_level"),
                "anomaly_reasons": obj.get("anomaly_reasons"),
                "anomaly_details": obj.get("anomaly_details"),
            })
        frames.append({
            "frameIndex": frame_index,
            "timestampMs": int(round(frame_index * 1000.0 / fps)) if fps > 0 else 0,
            "annotations": annotations,
        })
    return frames


@app.get("/api/track/result/{media_id}")
@source_write
def tracking_result(media_id: str, frameIndex: int | None = Query(default=None), required: bool = Query(default=False), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    directory = media_dir(media_id)
    if not directory.is_dir() or not find_video(directory):
        raise HTTPException(404, {'code':'MEDIA_NOT_FOUND', 'message':'原视频不存在，请核对素材库或联系管理员'})
    file = directory / RESULT_FILE_NAME
    from .endpoint_tracking import project_endpoints, projection_dimensions
    rows = project_endpoints(annotation_state.read_rows(directory, validate_geometry=True),**projection_dimensions(directory))
    if not file.is_file():
        if required is True:
            logging.getLogger('review.tracking').error('tracking.expected_result_missing media=%s', media_id)
            raise HTTPException(409, {'code':'TRACKING_RESULTS_MISSING', 'message':'追踪任务应已生成结果，但结果文件缺失，请检查任务日志与存储'})
        return {'format':'sam3-tracking-results-jsonl', 'state':'not_generated', 'frames':[], 'count':0}
    workspace = annotation_state.read_state(directory)
    rows = [{**row, "objects": [obj for obj in row.get("objects", []) if not annotation_state.is_deleted(workspace, int(row.get("source_frame_index", row.get("frame_index", 0))), int(obj.get("object_id", obj.get("objectId", 0))))]} for row in rows]
    from .endpoint_tracking import terminal_aliases
    for row in rows:
        aliases = terminal_aliases(row)
        if aliases:
            fi = row['source_frame_index']
            manuals = {o['objectId'] for o in workspace.get('manualAnnotations', []) if o.get('frameIndex') == fi}
            replaced = {canonical for tail_id, canonical in aliases.items()
                        if tail_id in manuals or annotation_state.is_deleted(workspace, fi, tail_id)}
            row['objects'] = [o for o in row['objects'] if o['object_id'] not in replaced]
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
        terminal = next(((r.get('reviewEvidence') or {}).get('endpointCheck') for r in rows
                         if 'terminalAnnotations' in ((r.get('reviewEvidence') or {}).get('endpointCheck') or {})), None)
        checks = [(r.get('reviewEvidence') or {}).get('jointCheck') for r in rows]
        joint_state = {'state':'checked' if all(c and c.get('state')=='checked' for c in checks) else 'failed',
                       'checkedFrames':sum(bool(c and c.get('state')=='checked') for c in checks)} if any(checks) else None
        return {"format": "sam3-tracking-results-jsonl", "state":"available", "frames": frames, "count": len(frames),
                **({'jointTracking':joint_state} if joint_state else {}),
                **({'terminalIdentity': {'frameIndex': terminal['anchorFrame'], 'anchorHash': terminal['anchorHash'],
                                        'matches': {**terminal['matches'],**terminal.get('unmatchedAliases',{})}}} if terminal and terminal['state']=='checked' else {})}
    return next(
        (f for f in frames if int(f.get("frameIndex", -1)) == frameIndex),
        {"frameIndex": frameIndex, "timestampMs": 0, "annotations": []},
    )


@app.get("/api/track/result-file/{media_id}")
@source_write
def tracking_result_file(media_id: str, required: bool = Query(default=False), user: dict[str, Any] = Depends(current_user)) -> Response:
    directory = media_dir(media_id)
    if not directory.is_dir() or not find_video(directory):
        raise HTTPException(404, {'code':'MEDIA_NOT_FOUND', 'message':'原视频不存在，请核对素材库或联系管理员'})
    file = directory / RESULT_FILE_NAME
    from .endpoint_tracking import project_endpoints, projection_dimensions
    rows = project_endpoints(annotation_state.read_rows(directory, validate_geometry=True),**projection_dimensions(directory))
    if not file.is_file():
        if required is True:
            logging.getLogger('review.tracking').error('tracking.expected_result_missing media=%s operation=result_file', media_id)
            raise HTTPException(409, {'code':'TRACKING_RESULTS_MISSING', 'message':'追踪任务应已生成结果，但结果文件缺失，请检查任务日志与存储'})
        raise HTTPException(404, {'code':'TRACKING_RESULTS_NOT_GENERATED', 'message':'尚未生成追踪结果，请先完成 AI Tracking'})
    workspace = annotation_state.read_state(directory)
    from .endpoint_tracking import terminal_aliases
    for row in rows:
        fi = int(row.get("source_frame_index", row.get("frame_index", 0)))
        row["objects"] = [obj for obj in row.get("objects", []) if not annotation_state.is_deleted(workspace, fi, int(obj.get("object_id", obj.get("objectId", 0))))]
        deleted_aliases={canonical for original,canonical in terminal_aliases(row).items() if annotation_state.is_deleted(workspace,fi,original)}
        row["objects"]=[o for o in row["objects"] if o["object_id"] not in deleted_aliases]
        row["anomalies"] = [obj for obj in row.get("anomalies", []) if not annotation_state.is_deleted(workspace, fi, int(obj.get("object_id", obj.get("objectId", 0))))]
    return Response("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), media_type="application/x-ndjson", headers={"Content-Disposition": f'inline; filename="{RESULT_FILE_NAME}"'})


@app.get("/api/track/media")
@source_write
def list_media(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """列出后端已有的所有视频素材（刷新页面后可恢复素材列表）。"""
    items: list[dict[str, Any]] = []
    name_count: dict[str, int] = {}
    for entry in iter_media_dirs():
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
        result_presence = annotation_state.tracking_result_presence(entry)
        has_result = None if result_presence == 'missing' else result_presence == 'present'
        has_workspace = (entry / WORKSPACE_STATE_FILE_NAME).is_file()
        base_name = meta.get("videoName") or video_file.name
        name_count[base_name] = name_count.get(base_name, 0) + 1
        count = name_count[base_name]
        display_name = base_name if count == 1 else f"{base_name} ({count})"
        items.append({
            "mediaId": entry.name,
            # Stable storage identity and the actual source filename are kept
            # separate from videoName, whose duplicate suffix is UI-only.
            "directoryName": entry.name,
            "sourceVideoName": base_name,
            "videoName": display_name,
            "videoUrl": f"/api/track/video/{entry.name}",
            "videoMimeType": meta.get("videoMimeType") or _video_media_type(video_file),
            "hasTrackingResult": has_result,
            "trackingResultState": result_presence,
            "hasWorkspaceState": has_workspace,
            "fps": meta.get("fps") or 0,
            "width": meta.get("width") or 0,
            "height": meta.get("height") or 0,
            "frameCount": meta.get("frameCount") or 0,
            "duration": meta.get("duration") or ((meta.get("frameCount") or 0) / (meta.get("fps") or 1) if meta.get("fps") else 0),
        })
    return {"items": items}


@app.delete("/api/track/media/{media_id}")
@source_write
def delete_media(media_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """删除一个视频素材及其目录下的所有文件。"""
    logger = logging.getLogger("review.media")
    if not media_id or media_id in {".", ".."} or Path(media_id).name != media_id or "\\" in media_id:
        raise HTTPException(400, "素材 ID 无效")
    if tracking_is_busy(media_id):
        logger.warning("media.delete_rejected media=%s user=%s reason=tracking_busy", media_id, user["uid"])
        raise HTTPException(409, "该视频正在 AI 追踪，无法删除素材")
    directory = media_dir(media_id)
    if not directory.is_dir():
        raise HTTPException(404, "素材不存在")
    from .tracking_restart import recover_directory
    recover_directory(directory)
    from .db import connect
    with connect() as conn:
        referenced = conn.execute('SELECT 1 FROM annotation_baselines a JOIN media_revisions m ON m.id=a.media_revision_id WHERE m.media_id=? LIMIT 1',(media_id,)).fetchone()
    if referenced:
        logger.warning("media.delete_rejected media=%s user=%s reason=baseline_reference", media_id, user["uid"])
        raise HTTPException(409, "视频已被审查基准引用，不能删除原始视频")
    current, legacy = TRACK_DATA_DIR / media_id, LEGACY_TRACK_DATA_DIR / media_id
    if current.is_dir() and legacy.is_dir() and current.resolve() != legacy.resolve():
        logger.warning("media.delete_rejected media=%s user=%s reason=duplicate_storage", media_id, user["uid"])
        raise HTTPException(409, "新旧存储中存在同名目录，请管理员先核对迁移，避免删除后旧视频重新出现")
    import shutil
    source_video = find_video(directory)
    try:
        if source_video is None:
            shutil.rmtree(directory)
        else:
            # Exact-frame previews intentionally cache an OpenCV VideoCapture.
            # Its open handle prevents unlinking the source on Windows, so
            # release only this video's decoder and keep it from being reopened
            # until the directory removal has finished.
            from .video_frames import frames
            with frames.removing(source_video):
                shutil.rmtree(directory)
    except OSError as exc:
        logger.exception("media.delete_failed media=%s user=%s", media_id, user["uid"])
        raise HTTPException(500, "删除素材文件失败，请管理员查看服务日志后重试") from exc
    logger.info("media.deleted media=%s user=%s", media_id, user["uid"])
    return {"deleted": True, "mediaId": media_id}


@app.get("/api/track/video/{media_id}")
def video(media_id: str, user: dict[str, Any] = Depends(current_video_user)) -> FileResponse:
    file = find_video(media_dir(media_id))
    if not file:
        raise HTTPException(404, "视频不存在")
    return FileResponse(file, media_type=_video_media_type(file), filename=file.name, content_disposition_type="inline", headers={'Cache-Control':'private, no-store'})



@app.get("/api/track/frame/{media_id}/{frame_index}")
def video_frame(media_id: str, frame_index: int, user: dict[str, Any] = Depends(current_user)) -> Response:
    """返回原始视频的精确第 frame_index 帧。\n\n    精确逐帧标注模式不再依赖浏览器 currentTime/seek 的实际呈现帧；\n    后端直接从用户上传的原始源视频解码指定帧，并以 JPEG 返回给前端。\n    """
    if frame_index < 0:
        raise HTTPException(400, "frame_index 不能小于 0")

    directory = media_dir(media_id)
    file = find_video(directory)
    if not file:
        raise HTTPException(404, "视频不存在")

    try:
        from .video_frames import frames, FrameReadError
        data = frames.read(file, frame_index)
        return Response(content=data, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=31536000, immutable'})
    except FrameReadError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"读取第 {frame_index} 帧失败：{exc}") from exc


class HeadBoxRequest(BaseModel):
    frameIndex: int
    x: float
    y: float


@app.post("/api/track/head-box/{media_id}")
def head_box(media_id: str, req: HeadBoxRequest, user: dict[str, Any] = Depends(current_user)):
    import math
    import cv2
    import numpy as np
    from . import head_click
    from .video_frames import frames, FrameReadError
    if req.frameIndex < 0 or not all(math.isfinite(v) for v in (req.x, req.y)):
        raise HTTPException(422, '帧号或点击坐标无效')
    file = find_video(media_dir(media_id))
    if not file:
        raise HTTPException(404, '视频不存在')
    started = time.monotonic()
    try:
        image = cv2.imdecode(np.frombuffer(frames.read(file, req.frameIndex), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(500, '源帧读取失败')
        h, w = image.shape[:2]
        if not (0 <= req.x < w and 0 <= req.y < h):
            raise HTTPException(422, '点击坐标超出原图')
        result = head_click.propose(image, req.x, req.y)
        logging.getLogger('review.annotation.head_click').info('proposal media=%s frame=%s bbox=%s elapsed_ms=%.0f', media_id, req.frameIndex, result['bbox'], (time.monotonic()-started)*1000)
        return {**result, 'frameIndex': req.frameIndex, 'width': w, 'height': h}
    except (head_click.ProposalError, FrameReadError) as exc:
        logging.getLogger('review.annotation.head_click').warning('proposal_rejected media=%s frame=%s reason=%s', media_id, req.frameIndex, exc)
        raise HTTPException(exc.status, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logging.getLogger('review.annotation.head_click').exception('proposal_failed media=%s frame=%s', media_id, req.frameIndex)
        raise HTTPException(503, '头部检测失败，请稍后重试或手动画框；管理员可查看 annotation.head_click 日志。') from exc


@app.get("/api/track/overlay/{media_id}")
def tracking_overlay(media_id: str, user: dict[str, Any] = Depends(current_user)) -> FileResponse:
    from .tracking_preview import prepare
    directory = media_dir(media_id)
    video = find_video(directory)
    if not video or not (directory / RESULT_FILE_NAME).is_file():
        raise HTTPException(404, '原视频或追踪结果不存在')
    file = prepare(video, directory / RESULT_FILE_NAME, lambda: tracking_is_busy(media_id))
    return FileResponse(file, media_type="video/mp4", filename=OVERLAY_FILE_NAME, content_disposition_type="inline")


@app.get("/api/track/sam3/health")
def sam3_health(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    engine = get_tracker_engine()
    return {"ok": True, "modelLoaded": engine.model is not None and engine.processor is not None, "model": engine.model_id, "device": str(engine.device), "dtype": str(engine.torch_dtype)}


# ---------------------------- dataset export ----------------------------

def _dataset_class_name(value: Any) -> str:
    """Map per-object display names to one shared training category.

    UI names include stable tracking instance IDs (for example ``sperm 1``).
    Those IDs distinguish objects during annotation but must not become COCO or
    YOLO categories, so ``sperm1``, ``sperm 2`` and ``sperm_3`` all map to
    ``sperm``.
    """
    name = str(value or "object").strip()
    normalized = re.sub(r"(?:\s*[-_#]?\s*\d+|\s*[（(]\s*\d+\s*[）)])$", "", name).strip()
    return normalized or name


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
