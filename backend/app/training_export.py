"""Training packages are built only from the latest reviewed and confirmed snapshot."""

import json
import logging
import math
import shutil
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Lock

import cv2
import yaml

from . import confirmation_workflow as confirmation
from . import quality_audit as audit
from .config import DATASET_EXPORT_DIR
from .db import connect
from .review_repository import compute_file_sha256
from .review_workflow import ReviewError, digest, now, packed

log = logging.getLogger("review.dataset")
_pool = None
_pool_lock = Lock()


def migrate(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS training_exports (
      id TEXT PRIMARY KEY, actor_id INTEGER NOT NULL REFERENCES users(id),
      request_key TEXT NOT NULL, request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
      state TEXT NOT NULL, completed_frames INTEGER NOT NULL DEFAULT 0,
      total_frames INTEGER NOT NULL, manifest_json TEXT, error_code TEXT, error_message TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(actor_id, request_key)
    );
    """)
    c.commit()
    audit.migrate(c)
    from .annotation_export import migrate as annotation_migrate
    annotation_migrate(c)
    from .annotation_review import migrate as review_migrate
    review_migrate(c)
    c.commit()


def recover_interrupted():
    with closing(connect()) as c, c:
        n = c.execute(
            "UPDATE training_exports SET state='failed',error_code='EXPORT_INTERRUPTED',error_message='服务重启中断了导出，请重新生成',updated_at=? WHERE state IN ('queued','running')",
            (now(),),
        ).rowcount
    if n:
        log.warning("dataset.interrupted count=%s", n)


def shutdown():
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool:
        pool.shutdown(wait=True)


def enqueue(export_id):
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="training-export"
            )
        _pool.submit(run, export_id)


def eligible(c, ids):
    """Check every requested final, including B state, C state and final-version head."""
    if not ids or len(ids) != len(set(ids)):
        raise ReviewError("INVALID_FINAL_VERSIONS", "请提供不重复的最终版本", 422)
    versions = []
    media_seen = set()
    for vid in ids:
        if vid.startswith('annexp_'):
            from .annotation_export import resolve
            v = resolve(c, vid)
            if v['media']['sha256'] in media_seen:
                raise ReviewError('DUPLICATE_MEDIA', '同一视频不能重复导出', 422)
            media_seen.add(v['media']['sha256'])
            versions.append(v)
            continue
        f = c.execute("SELECT * FROM final_versions WHERE id=?", (vid,)).fetchone()
        if not f:
            raise ReviewError("FINAL_VERSION_NOT_FOUND", "最终版本不存在", 404)
        s = confirmation.session_row(c, f["confirmation_id"])
        from .concentrated_review import info as concentrated_info
        direct = concentrated_info(c, s['review_session_id'])
        if direct and direct['final_version_id'] != vid:
            raise ReviewError('REVIEW_REQUIRED', '审查已重新开启或版本已更新，请使用当前已完成审查的最终版本')
        head = c.execute(
            "SELECT id FROM final_versions WHERE confirmation_id=? ORDER BY rowid DESC LIMIT 1",
            (s["id"],),
        ).fetchone()
        b = c.execute(
            "SELECT state FROM review_sessions WHERE id=?", (s["review_session_id"],)
        ).fetchone()
        if not b or b["state"] != "reviewed":
            raise ReviewError("REVIEW_REQUIRED", "视频尚未完成审查，不能导出训练数据集")
        if s["state"] != "confirmed" or not head or head[0] != vid:
            raise ReviewError(
                "CONFIRMATION_REQUIRED",
                "必须使用当前已完成对比确认的最终版本；重新确认期间暂停导出",
            )
        if (
            f["baseline_id"] != s["baseline_id"]
            or f["review_version_id"] != s["review_version_id"]
        ):
            raise ReviewError("INVALID_FINAL_VERSION", "最终版本与审查版本不一致")
        reason = confirmation.integrity(c, s)
        if reason:
            raise ReviewError("INVALID_FROZEN_VERSION", reason)
        pending = c.execute(
            """SELECT COUNT(*) FROM review_changes x LEFT JOIN decision_heads h
          ON h.change_id=x.id AND h.confirmation_id=? WHERE x.review_version_id=?
          AND (h.choice IS NULL OR h.choice NOT IN ('A','B'))""",
            (s["id"], s["review_version_id"]),
        ).fetchone()[0]
        if pending:
            raise ReviewError("UNDECIDED_CHANGES", "存在尚未确认的修改项，不能导出")
        media = c.execute(
            "SELECT m.* FROM media_revisions m JOIN annotation_baselines a ON a.media_revision_id=m.id WHERE a.id=?",
            (f["baseline_id"],),
        ).fetchone()
        if media["sha256"] in media_seen:
            raise ReviewError(
                "DUPLICATE_MEDIA",
                "同一个视频只能选取一个最终版本，避免训练和验证中重复样本",
                422,
            )
        media_seen.add(media["sha256"])
        n = c.execute(
            "SELECT COUNT(*),MIN(frame_index),MAX(frame_index) FROM final_version_frames WHERE final_version_id=?",
            (vid,),
        ).fetchone()
        if (
            n[0] != f["frame_count"]
            or n[1] != 0
            or n[2] != f["frame_count"] - 1
            or f["frame_count"] != media["frame_count"]
        ):
            raise ReviewError(
                "INCOMPLETE_FINAL_VERSION", "最终版本缺少完整帧快照，请重新确认后导出"
            )
        versions.append({"final": dict(f), "media": dict(media)})
    return versions


def snapshots(c, ids):
    versions = eligible(c, ids)
    classes = set()
    total_objects = empty_frames = 0
    for v in versions:
        media = v["media"]
        w, h = media["width"], media["height"]
        if w <= 0 or h <= 0:
            raise ReviewError("INVALID_MEDIA_SIZE", "视频尺寸无效")
        frames = v.get("directFrames") if "directFrames" in v else [
            {"frameIndex": r["frame_index"], "objects": json.loads(r["objects_json"])}
            for r in c.execute(
                "SELECT * FROM final_version_frames WHERE final_version_id=? ORDER BY frame_index",
                (v["final"]["id"],),
            )
        ]
        if digest(frames) != v["final"]["snapshot_hash"]:
            raise ReviewError("FINAL_SNAPSHOT_CHANGED", "最终快照校验失败，不能导出")
        for frame in frames:
            if not frame["objects"]:
                empty_frames += 1
            seen = set()
            for obj in frame["objects"]:
                box = obj.get("bbox")
                name = obj.get("classKey")
                if not isinstance(name, str) or not name.strip():
                    raise ReviewError("INVALID_CLASS", "最终版本存在空类别")
                if obj.get("objectId") in seen:
                    raise ReviewError("INVALID_FINAL_VERSION", "最终帧含有重复对象")
                seen.add(obj.get("objectId"))
                if (
                    not isinstance(box, list)
                    or len(box) != 4
                    or any(
                        type(x) not in (float, int) or not math.isfinite(x) for x in box
                    )
                    or not (0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h)
                ):
                    raise ReviewError(
                        "INVALID_FINAL_GEOMETRY",
                        "最终标注坐标无效或越界，不能静默裁剪后导出",
                    )
                classes.add(name)
                total_objects += 1
        v["frames"] = frames
        record = None if "directFrames" in v else c.execute("SELECT decision_snapshot_json FROM confirmation_final_records WHERE final_version_id=?", (v['final']['id'],)).fetchone()
        # Presence choices which result in no box are still part of the
        # exported provenance. They must never become fake training objects.
        v['decisionRecords'] = json.loads(record[0]) if record else []
    total_frames = sum(len(v["frames"]) for v in versions)
    if total_frames < 2:
        raise ReviewError(
            "INSUFFICIENT_FRAMES",
            "训练与验证集至少需要两帧，请合并其他已确认视频后导出",
            422,
        )
    if not classes:
        raise ReviewError(
            "NO_TRAINING_CLASSES",
            "所选视频均无目标类别，请与包含目标的已确认视频合并导出",
            422,
        )
    return versions, {
        "frameCount": total_frames,
        "objectCount": total_objects,
        "emptyFrames": empty_frames,
        "classNames": sorted(classes),
    }


def preview(ids, body=None):
    with closing(connect()) as c:
        c.execute("BEGIN")
        versions, summary = snapshots(c, ids)
        if body is None:
            return summary
        return {**summary, **split_plan(versions, body)}


def create(actor, key, body):
    request_hash = digest(body)
    with closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        old = c.execute(
            "SELECT * FROM training_exports WHERE actor_id=? AND request_key=?",
            (actor, key),
        ).fetchone()
        if old:
            if old["request_hash"] != request_hash:
                raise ReviewError(
                    "IDEMPOTENCY_KEY_REUSED", "同一个重试标识不能用于不同导出设置"
                )
            log.info("dataset.replay export=%s actor=%s key=%s", old["id"], actor, key)
            return status(old["id"], actor)
        from .annotation_export import authorize
        authorize(c, body["finalVersionIds"], actor)
        versions, summary = snapshots(c, body["finalVersionIds"])
        unknown = set(body.get("videoSplits") or {}) - set(body["finalVersionIds"])
        if unknown:
            raise ReviewError("INVALID_VIDEO_SPLIT", "手动划分包含未选择的视频", 422)
        split_plan(versions, body)  # Reject impossible manual splits before queueing.
        eid = "train_" + uuid.uuid4().hex
        c.execute(
            "INSERT INTO training_exports(id,actor_id,request_key,request_hash,request_json,state,total_frames,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                eid,
                actor,
                key,
                request_hash,
                packed(body),
                "queued",
                summary["frameCount"],
                now(),
                now(),
            ),
        )
        audit.capture(c, eid, actor, body, versions)
    log.info(
        "dataset.queued export=%s actor=%s versions=%s key=%s",
        eid,
        actor,
        body["finalVersionIds"],
        key,
    )
    try:
        enqueue(eid)
    except Exception:
        log.exception("dataset.enqueue_failed export=%s", eid)
        mark_failed(eid, "EXPORT_UNAVAILABLE", "暂时无法启动导出，请重新生成")
    return status(eid, actor)


def status(eid, actor):
    with closing(connect()) as c:
        c.execute("BEGIN")
        row = c.execute(
            "SELECT * FROM training_exports WHERE id=? AND actor_id=?", (eid, actor)
        ).fetchone()
        if not row:
            raise ReviewError("EXPORT_NOT_FOUND", "导出任务不存在", 404)
        result = {
            "exportId": row["id"],
            "state": row["state"],
            "completedFrames": row["completed_frames"],
            "totalFrames": row["total_frames"],
            "manifest": json.loads(row["manifest_json"])
            if row["manifest_json"]
            else None,
            "errorCode": row["error_code"],
            "errorMessage": row["error_message"],
            "downloadUrl": None,
        }
        try:
            eligible(c, json.loads(row["request_json"])["finalVersionIds"])
        except ReviewError as e:
            result.update(state="invalidated", errorCode=e.code, errorMessage=e.message)
        if result["state"] == "ready":
            result["downloadUrl"] = f"/api/datasets/exports/{eid}/download"
        return result


def download(eid, actor):
    result = status(eid, actor)
    if result["state"] != "ready":
        raise ReviewError(
            result["errorCode"] or "EXPORT_NOT_READY",
            result["errorMessage"] or "导出尚未完成",
        )
    path = DATASET_EXPORT_DIR / (eid + ".zip")
    if not path.is_file():
        raise ReviewError("EXPORT_FILE_MISSING", "导出文件已不存在，请重新生成", 404)
    log.info("dataset.download export=%s actor=%s", eid, actor)
    return path


def mark_failed(eid, code, message):
    with closing(connect()) as c, c:
        c.execute(
            "UPDATE training_exports SET state='failed',error_code=?,error_message=?,updated_at=? WHERE id=?",
            (code, message, now(), eid),
        )


SPLITS = ("train", "val", "test")


def split_settings(body):
    """Requests stored before video-level splitting keep their original frame-order meaning."""
    return {
        "mode": body.get("splitMode", "frame"),
        "train": body["splitRatio"],
        "test": body.get("testRatio", 0.0),
        "overrides": body.get("videoSplits") or {},
    }


def video_bucket(sha256):
    """Stable 0..1 position from the source content, so a video keeps its split across exports."""
    return int(sha256[:12], 16) / float(16**12)


def split_videos(versions, settings):
    """Assign every frame of a video to one split; neighbouring frames never leak across splits."""
    train, test = settings["train"], settings["test"]
    overrides = settings["overrides"]
    chosen = []
    for v in versions:
        fixed = overrides.get(v["final"]["id"])
        if fixed:
            chosen.append(fixed)
            continue
        u = video_bucket(v["media"]["sha256"])
        chosen.append("train" if u < train else "test" if u >= 1 - test else "val")
    positive = [any(f["objects"] for f in v["frames"]) for v in versions]
    movable = sorted(
        (i for i, v in enumerate(versions) if v["final"]["id"] not in overrides),
        key=lambda i: video_bucket(versions[i]["media"]["sha256"]),
    )
    if len(versions) > 1:
        # Small batches can hash unevenly; keep the package trainable and measurable.
        if not any(p and s == "train" for p, s in zip(positive, chosen)):
            i = next((i for i in movable if positive[i]), None)
            if i is None:
                raise ReviewError(
                    "SPLIT_WITHOUT_TRAINING_TARGETS",
                    "训练集没有含目标的视频，请调整手动指定的划分",
                    422,
                )
            chosen[i] = "train"
        if sum(p and s == "train" for p, s in zip(positive, chosen)) > 1 and not any(
            p and s == "val" for p, s in zip(positive, chosen)
        ):
            i = next(
                (i for i in reversed(movable) if positive[i] and chosen[i] == "train"),
                None,
            )
            if i is not None:
                chosen[i] = "val"
    return chosen


def assign_splits(versions, body):
    settings = split_settings(body)
    if settings["mode"] == "frame":
        return split_samples(versions, settings["train"]), None
    chosen = split_videos(versions, settings)
    return {
        (vi, f["frameIndex"]): chosen[vi]
        for vi, v in enumerate(versions)
        for f in v["frames"]
    }, chosen


def split_plan(versions, body):
    """Preview of the exact split the package will use."""
    assignments, chosen = assign_splits(versions, body)
    counts = {s: 0 for s in SPLITS}
    for split in assignments.values():
        counts[split] += 1
    videos = [
        {
            "finalVersionId": v["final"]["id"],
            "frameCount": len(v["frames"]),
            "objectCount": sum(len(f["objects"]) for f in v["frames"]),
            "split": chosen[vi] if chosen else None,
        }
        for vi, v in enumerate(versions)
    ]
    return {"splitMode": split_settings(body)["mode"], "splitFrames": counts, "videos": videos}


def split_samples(versions, ratio):
    samples = [(vi, f) for vi, v in enumerate(versions) for f in v["frames"]]
    cut = max(1, min(len(samples) - 1, int(len(samples) * ratio)))
    train = samples[:cut]
    val = samples[cut:]
    # A rare-target clip may start with entirely empty frames; training still needs one positive.
    if not any(f["objects"] for _, f in train):
        j = next(i for i, (_, f) in enumerate(val) if f["objects"])
        train[-1], val[j] = val[j], train[-1]
    if (
        not any(f["objects"] for _, f in val)
        and sum(bool(f["objects"]) for _, f in train) > 1
    ):
        j = next(i for i, (_, f) in enumerate(train) if f["objects"])
        val[0], train[j] = train[j], val[0]
    return {
        (vi, f["frameIndex"]): split
        for split, rows in [("train", train), ("val", val)]
        for vi, f in rows
    }


def build_package(eid, body, versions, summary, work, audit_snapshot, audit_sha):
    # Lazy import reuses the application's exact original-video resolver (never overlay/cached frames).
    from .main import find_video, media_dir

    class_ids = {name: i for i, name in enumerate(summary["classNames"])}
    assignments, chosen = assign_splits(versions, body)
    include_videos = body.get("includeSourceVideos", False)
    manifest = {
        "schemaVersion": 2,
        "sourceSystemId": audit_snapshot["sourceSystemId"],
        "auditReference": audit.reference(audit_snapshot, audit_sha),
        "exportId": eid,
        "format": body["format"],
        "createdAt": now(),
        "finalVersionIds": body["finalVersionIds"],
        "classNames": summary["classNames"],
        "requestedTrainRatio": body["splitRatio"],
        "requestedTestRatio": split_settings(body)["test"],
        "splitStrategy": "frame-order; keep positives in train and, when possible, val"
        if chosen is None
        else "video-level; whole videos by stable source hash or manual choice, keep positives in train and, when possible, val",
        "counts": {**summary, "train": 0, "val": 0, "test": 0},
        "sources": [],
        "samples": [],
    }
    if audit_snapshot.get('requiredFeatures'):
        manifest['requiredFeatures'] = audit_snapshot['requiredFeatures']
    coco = {
        split: {
            "images": [],
            "annotations": [],
            "categories": [
                {"id": i + 1, "name": name} for name, i in class_ids.items()
            ],
        }
        for split in SPLITS
    }
    done = ann_id = 0
    for vi, v in enumerate(versions):
        m = v["media"]
        video = find_video(media_dir(m["media_id"]))
        if video is None or not video.is_file():
            raise ReviewError(
                "SOURCE_VIDEO_MISSING", "原始视频不存在，无法提取训练图片", 404
            )
        if compute_file_sha256(video) != m["sha256"]:
            raise ReviewError(
                "SOURCE_VIDEO_CHANGED", "原始视频已变化，与审查时的视频不一致"
            )
        edited = None
        if "directFrames" in v:
            from .annotation_review import review_edited
            with closing(connect()) as c:
                edited = review_edited(c, m["media_id"])
        manifest["sources"].append(
            {
                "finalVersionId": v["final"]["id"],
                "confirmationId": v["final"]["confirmation_id"],
                "baselineId": v["final"]["baseline_id"],
                "reviewVersionId": v["final"]["review_version_id"],
                "mediaId": m["media_id"],
                "mediaRevisionId": m["id"],
                "reviewSessionId": audit_snapshot["sources"][vi]["reviewSessionId"],
                "sourceSha256": m["sha256"],
                "width": m["width"],
                "height": m["height"],
                "frameCount": m["frame_count"],
                "snapshotHash": v["final"]["snapshot_hash"],
                "annotationOnly": "directFrames" in v,
                "split": chosen[vi] if chosen else None,
                "videoFile": None,
            }
        )
        cap = cv2.VideoCapture(str(video))
        try:
            if not cap.isOpened():
                raise ReviewError("VIDEO_DECODE_FAILED", "无法打开原始视频")
            for frame in v["frames"]:
                fi = frame["frameIndex"]
                ok, image = cap.read()
                if not ok or image is None:
                    raise ReviewError(
                        "FRAME_DECODE_FAILED",
                        f"原始视频第 {fi + 1} 帧解码失败，已停止导出",
                    )
                h, w = image.shape[:2]
                if (w, h) != (m["width"], m["height"]):
                    raise ReviewError(
                        "SOURCE_DIMENSIONS_CHANGED", "解码图像尺寸与最终版本不符"
                    )
                split = assignments[vi, fi]
                stem = f"{eid}__v{vi:03d}__frame_{fi:06d}"
                rel = f"images/{split}/{stem}.jpg"
                image_path = work / rel
                image_path.parent.mkdir(parents=True, exist_ok=True)
                # OpenCV imwrite cannot handle some Windows Unicode paths.
                try:
                    encoded_ok, encoded = cv2.imencode(
                        ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95]
                    )
                    if not encoded_ok:
                        raise ValueError("JPEG encoding failed")
                    image_path.write_bytes(encoded.tobytes())
                except (OSError, ValueError, cv2.error) as exc:
                    raise ReviewError(
                        "IMAGE_WRITE_FAILED", f"第 {fi + 1} 帧图片写入失败", 503
                    ) from exc
                done += 1
                manifest["counts"][split] += 1
                sample = {
                    "sampleId": stem,
                    "mediaRevisionId": m["id"],
                    "imageSha256": audit.sha(encoded.tobytes()),
                    "objects": [],
                    "finalVersionId": v["final"]["id"],
                    "frameIndex": fi,
                    "split": split,
                    "image": rel,
                    "label": f"labels/{split}/{stem}.txt"
                    if body["format"] != "coco"
                    else None,
                    "objectCount": len(frame["objects"]),
                }
                manifest["samples"].append(sample)
                coco[split]["images"].append(
                    {
                        "id": done,
                        "file_name": f"../{rel}",
                        "width": w,
                        "height": h,
                        "frameIndex": fi,
                        "finalVersionId": v["final"]["id"],
                    }
                )
                lines = []
                for line_no, obj in enumerate(frame["objects"], 1):
                    sample["objects"].append(
                        {
                            "objectId": obj["objectId"],
                            "yoloLine": line_no if body["format"] != "coco" else None,
                            # True when a reviewer added or changed this box; None for legacy A/B/F sources.
                            "reviewEdited": None if edited is None else obj["objectId"] in edited.get(fi, ()),
                            "cocoAnnotationId": ann_id + 1
                            if body["format"] != "yolo"
                            else None,
                        }
                    )
                    x1, y1, x2, y2 = obj["bbox"]
                    bw, bh = x2 - x1, y2 - y1
                    cid = class_ids[obj["classKey"]]
                    lines.append(
                        f"{cid} {(x1 + x2) / (2 * w):.10f} {(y1 + y2) / (2 * h):.10f} {bw / w:.10f} {bh / h:.10f}"
                    )
                    ann_id += 1
                    coco[split]["annotations"].append(
                        {
                            "id": ann_id,
                            "image_id": done,
                            "category_id": cid + 1,
                            "bbox": [x1, y1, bw, bh],
                            "area": bw * bh,
                            "iscrowd": 0,
                            "annotationId": obj["annotationId"],
                            "resolution": obj["resolution"],
                        }
                    )
                if sample["label"]:
                    label = work / sample["label"]
                    label.parent.mkdir(parents=True, exist_ok=True)
                    label.write_text(
                        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
                    )
                if done % 20 == 0 or done == summary["frameCount"]:
                    with closing(connect()) as c, c:
                        eligible(c, body["finalVersionIds"])
                        c.execute(
                            "UPDATE training_exports SET completed_frames=?,updated_at=? WHERE id=?",
                            (done, now(), eid),
                        )
                    log.info(
                        "dataset.progress export=%s completed=%s total=%s",
                        eid,
                        done,
                        summary["frameCount"],
                    )
        finally:
            cap.release()
        if compute_file_sha256(video) != m["sha256"]:
            raise ReviewError(
                "SOURCE_VIDEO_CHANGED", "导出过程中原始视频发生变化，已停止导出"
            )
        if include_videos:
            rel = f"videos/v{vi:03d}__{video.name}"
            (work / "videos").mkdir(exist_ok=True)
            try:
                shutil.copyfile(video, work / rel)
            except OSError as exc:
                raise ReviewError("VIDEO_COPY_FAILED", "原始视频复制失败", 503) from exc
            if compute_file_sha256(work / rel) != m["sha256"]:
                raise ReviewError("SOURCE_VIDEO_CHANGED", "复制的原始视频校验失败，已停止导出")
            manifest["sources"][-1]["videoFile"] = rel
        # Preserve A/B/final decisions for provenance, separate from training label files.
        provenance = work / "provenance"
        provenance.mkdir(exist_ok=True)
        (provenance / f"{v['final']['id']}.json").write_text(
            packed({"finalVersionId": v["final"]["id"], "frames": v["frames"], "decisionRecords": v['decisionRecords']}),
            encoding="utf-8",
        )
    if body["format"] != "coco":
        # No server-only absolute path: Ultralytics resolves an omitted root relative to this yaml.
        config = {
            "train": "images/train",
            "val": "images/val",
            **({"test": "images/test"} if manifest["counts"]["test"] else {}),
            "nc": len(class_ids),
            "names": {cid: name for name, cid in class_ids.items()},
        }
        (work / "data.yaml").write_text(
            "# 解压后将 data.yaml 的绝对路径传给 YOLO。\n"
            + yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    if body["format"] != "yolo":
        annotations = work / "annotations"
        annotations.mkdir(exist_ok=True)
        for split, data in coco.items():
            if split == "test" and not data["images"]:
                continue
            (annotations / f"instances_{split}.json").write_text(
                packed(data), encoding="utf-8"
            )
    (work / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (work / "README.md").write_text(
        """# 已完成审查与对比确认的训练数据集

图片来自原始视频；标签仅来自导出清单中的最终版本，未修改对象沿用 A，空帧保留空标签。
YOLO 标签为 class_id cx cy w h，坐标按实际图像尺寸归一化，类别编号从 0 开始。
解压整个 ZIP 后，向 YOLO 传入本目录下 data.yaml 的绝对路径；无需访问导出服务器的目录。
例如：`yolo detect train model=yolov8n.pt data=/绝对路径/数据集/data.yaml epochs=100`
纯 COCO 导出使用 annotations/instances_train.json、instances_val.json（有测试集时还有 instances_test.json），图片路径相对该 JSON 文件。

划分方式见 manifest.json 的 splitStrategy：
- video-level：同一视频的所有帧只进入训练、验证、测试中的一个，验证/测试指标反映模型在没见过的视频上的效果。
  每个视频按原文件内容固定落入同一集合，重复导出或分批导出时不会换组；manifest 的 sources[].split 记录每个视频的去向。
  单个视频的包只含一个集合，用于之后与其他视频合并；可用 scripts/merge_training_exports.py 合并多个包。
- frame-order：同一视频内按帧序切分，相邻帧几乎相同，验证指标会偏高，仅用于调试。
勾选导出原视频时，videos/ 保存与审查时 SHA-256 一致的原文件，sources[].videoFile 指向对应文件。
manifest.json 记录逐图片 sampleId、原始帧、视频、最终版本及 auditReference；文件名包含数据集身份。
provenance/ 保留完整 A/B/最终框。完整人员与操作历史固定在服务端，通过运维审计导出命令获取。
manifest.json 的 samples[].objects[].reviewEdited 为 true 表示该框由审查员新增或修改过（追踪原结果有误），可用于加权训练或误差分析。
""",
        encoding="utf-8",
    )
    return manifest


def run(eid):
    work = DATASET_EXPORT_DIR / ".work" / eid
    partial = DATASET_EXPORT_DIR / (eid + ".zip.partial")
    target = DATASET_EXPORT_DIR / (eid + ".zip")
    published = False
    claimed = False
    try:
        with closing(connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM training_exports WHERE id=?", (eid,)
            ).fetchone()
            if not row or row["state"] != "queued":
                return
            body = json.loads(row["request_json"])
            versions, summary = snapshots(c, body["finalVersionIds"])
            c.execute(
                "UPDATE training_exports SET state='running',updated_at=? WHERE id=?",
                (now(), eid),
            )
        claimed = True
        work.mkdir(parents=True, exist_ok=False)
        log.info(
            "dataset.started export=%s actor=%s total=%s",
            eid,
            row["actor_id"],
            summary["frameCount"],
        )
        with closing(connect()) as c:
            audit_snapshot, audit_sha = audit.load(c, eid)
        expected = {
            (v["final"]["id"], v["final"]["snapshot_hash"], v["media"]["sha256"])
            for v in versions
        }
        recorded = {
            (s["finalVersionId"], s["snapshotHash"], s["sourceSha256"])
            for s in audit_snapshot["sources"]
        }
        if expected != recorded or body != audit_snapshot["dataset"]["settings"]:
            raise ReviewError("AUDIT_SNAPSHOT_MISMATCH", "审计快照与训练来源不一致")
        manifest = build_package(
            eid, body, versions, summary, work, audit_snapshot, audit_sha
        )
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(work.rglob("*")):
                if path.is_file():
                    # Videos are already compressed; storing them avoids a slow, useless deflate pass.
                    stored = path.relative_to(work).parts[0] == "videos"
                    archive.write(
                        path,
                        path.relative_to(work),
                        zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED,
                    )
        with closing(connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            eligible(
                c, body["finalVersionIds"]
            )  # Confirmation may have reopened while extracting.
            partial.replace(target)
            published = True
            c.execute(
                "UPDATE training_exports SET state='ready',manifest_json=?,completed_frames=total_frames,updated_at=? WHERE id=?",
                (packed(manifest), now(), eid),
            )
        log.info(
            "dataset.ready export=%s frames=%s bytes=%s",
            eid,
            summary["frameCount"],
            target.stat().st_size,
        )
    except Exception as e:
        log.exception(
            "dataset.failed export=%s code=%s", eid, getattr(e, "code", "EXPORT_FAILED")
        )
        if published:
            target.unlink(missing_ok=True)
        mark_failed(
            eid,
            getattr(e, "code", "EXPORT_FAILED"),
            e.message
            if isinstance(e, ReviewError)
            else "导出失败，请根据导出任务号查看日志后重试",
        )
    finally:
        if claimed:
            partial.unlink(missing_ok=True)
            if work.is_dir():
                shutil.rmtree(work)
