"""Small ingress adapter: saved A workspace + tracking -> complete frozen A + B task.

Reviewers never upload a substitute A. Unknown frames require explicit confirmation
by the workspace's last author. Mutable UI state is excluded from source revisions.
"""

import hashlib
import json
import logging
import re
import uuid
from contextlib import closing
from . import review_workflow as w
from .db import connect
from .annotation_state import is_deleted, RESULT_META_FILE, normalize_tracking_row


def integer(value, label):
    if type(value) is not int or value < 0:
        raise w.ReviewError("INVALID_SOURCE", f"{label} 缺少有效整数", 422)
    return value


def parse_rows(raw, info):
    """Tracking rows as published, with endpoint aliases projected; review evidence kept."""
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    rows = (
        data
        if isinstance(data, list)
        else data.get("frames", data.get("results", [data]))
    )
    from .endpoint_tracking import project_endpoints
    return project_endpoints([normalize_tracking_row(r) for r in rows],width=info['width'],height=info['height'])


def source(directory, uid, info, require_author=True, with_rows=False):
    """Effective frames of the saved workspace. The legacy submission path requires the
    last saver; reviewed export is gated by the annotation review instead."""
    from .tracking_restart import recover_directory
    recover_directory(directory)
    from .annotation_state import recover_pause_publication
    recover_pause_publication(directory)
    tracker = directory / "tracker_results.json"
    if not tracker.exists() and (directory / RESULT_META_FILE).exists():
        logging.getLogger('review.annotation').error('annotation.completion_results_missing media=%s', directory.name)
        raise w.ReviewError(
            'TRACKING_RESULTS_MISSING', '已生成的追踪结果文件缺失，无法送审；请管理员检查存储或恢复备份', 409
        )
    try:
        raw = tracker.read_text(encoding="utf-8") if tracker.exists() else ""
        try:
            workspace = json.loads(
                (directory / "workspace_state.json").read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            if not (tracker.exists() and (directory / "detector_tracks.json").exists()):
                raise
            workspace = {}  # Detector prelabel published results before anyone saved an edit.
    except (OSError, ValueError) as e:
        raise w.ReviewError(
            "SOURCE_UNAVAILABLE", "这个视频还没有保存的标注，请先在标注页标注", 422
        ) from e
    if require_author and workspace.get("updatedBy") != uid:
        raise w.ReviewError(
            "NOT_ANNOTATION_AUTHOR", "只有当前已保存工作区的标注员可以送审", 403
        )
    manifest = {
        "manualAnnotations": workspace.get("manualAnnotations", []),
        "deletedTrackingIds": workspace.get("deletedTrackingIds", []),
        "deletedObjectIds": workspace.get("deletedObjectIds", []),
        "deletedFrameObjects": workspace.get("deletedFrameObjects", []),
        "deletedAnnotationFrames": workspace.get("deletedAnnotationFrames", []),
        "updatedBy": uid,
        "trackingHash": hashlib.sha256(raw.encode()).hexdigest(),
        "media": info,
    }
    try:
        rows = parse_rows(raw, info)
        policies=sorted({(r.get('reviewEvidence') or {}).get('endpointCheck',{}).get('associationPolicy') for r in rows} - {None})
        if policies:manifest['endpointProjectionPolicy']=policies
        frames = {}
        terminal_maps = {}
        terminal_checks = {}
        from .endpoint_tracking import terminal_aliases
        for row in rows:
            row = normalize_tracking_row(row)
            fi = integer(
                row.get(
                    "source_frame_index", row.get("frame_index", row.get("frameIndex"))
                ),
                "Tracking 帧号",
            )
            if fi >= info["frameCount"] or fi in frames:
                raise ValueError("Tracking 帧重复或越界")
            if "objects" not in row or not isinstance(row["objects"], list):
                raise ValueError("Tracking 对象列表缺失")
            objects = {}
            terminal_maps[fi] = terminal_aliases(row)
            terminal_checks[fi] = (row.get('reviewEvidence') or {}).get('endpointCheck') or {}
            for obj in row["objects"]:
                oid = integer(
                    obj.get("object_id", obj.get("objectId")), "Tracking 对象 ID"
                )
                if oid == 0 or oid in objects:
                    raise ValueError("Tracking 对象 ID 重复或无效")
                if is_deleted(workspace, fi, oid):
                    continue
                objects[oid] = make_object(obj, oid, obj.get("bbox"), info)
            frames[fi] = objects
        seen = set()
        for obj in manifest["manualAnnotations"]:
            if obj.get("source") != "manual":
                raise ValueError("工作区人工框来源无效")
            fi = integer(obj.get("frameIndex"), "人工帧号")
            oid = integer(obj.get("objectId"), "人工对象 ID")
            if fi >= info["frameCount"] or oid == 0 or (fi, oid) in seen:
                raise ValueError("人工对象 ID 重复或帧号越界")
            seen.add((fi, oid))
            if is_deleted(workspace, fi, oid):
                frames.setdefault(fi, {})
                continue
            box = obj.get("bbox")
            if not isinstance(box, dict):
                raise ValueError("送审仅支持框，点标注请先转换为框")
            x, y = box["x"] * info["width"] / 100, box["y"] * info["height"] / 100
            b = [
                x,
                y,
                x + box["width"] * info["width"] / 100,
                y + box["height"] * info["height"] / 100,
            ]
            check = terminal_checks.get(fi, {})
            same_anchor=bool(obj.get('endpointAnchorHash')) and obj.get('endpointAnchorHash')==check.get('anchorHash')
            original_id=obj.get('endpointOriginalObjectId',oid) if same_anchor else oid
            canonical = terminal_maps.get(fi, {}).get(original_id, oid)
            if not is_deleted(workspace, fi, canonical):
                frames.setdefault(fi, {})[canonical] = make_object(obj, canonical, b, info)
        # Tail annotation IDs are independent local anchors. Keep originals in
        # the manifest, apply only unique trajectory aliases to the frozen A.
        for fi, aliases in terminal_maps.items():
            for tail_id, canonical in aliases.items():
                if is_deleted(workspace, fi, tail_id):
                    frames.get(fi, {}).pop(canonical, None)
        for fi in workspace.get("deletedAnnotationFrames", []):
            if integer(fi, "已删除标注帧号") >= info["frameCount"]:
                raise ValueError("已删除标注帧号越界")
            frames.setdefault(fi, {})
        for deleted in workspace.get("deletedFrameObjects", []):
            fi = integer(deleted.get("frameIndex"), "单帧删除帧号")
            if fi >= info["frameCount"]:
                raise ValueError("单帧删除帧号越界")
            frames.setdefault(fi, {})
        return (w.digest(manifest), frames, rows) if with_rows else (w.digest(manifest), frames)
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        raise w.ReviewError("INVALID_SOURCE", f"标注来源格式无效：{e}", 422) from e


def make_object(obj, oid, box, info):
    # Preserve classes while stripping UI instance numbering, as the exporter does.
    name = str(
        obj.get("classKey") or obj.get("name") or obj.get("label") or "sperm"
    ).strip()
    label = (
        re.sub(r"(?:\s*[-_#]?\s*\d+|\s*[（(]\s*\d+\s*[）)])$", "", name).strip() or name
    )
    a = [{"objectId": oid, "bbox": [-1, -1, -1, -1]}]
    if not isinstance(box, list):
        raise ValueError("框坐标缺失")
    patch = w.normalize_patch(
        a, [{"objectId": oid, "bbox": box}], info["width"], info["height"]
    )
    return {"objectId": oid, "bbox": patch[0]["bbox"], "classKey": label}


def ranges(indices):
    out = []
    for i in indices:
        if out and out[-1]["end"] == i - 1:
            out[-1]["end"] = i
        else:
            out.append({"start": i, "end": i})
    return out


def preview(directory, uid, info):
    revision, frames = source(directory, uid, info)
    missing = [i for i in range(info["frameCount"]) if i not in frames]
    return dict(
        sourceRevision=revision,
        frameCount=info["frameCount"],
        objectFrames=sum(bool(x) for x in frames.values()),
        emptyFrames=sum(not x for x in frames.values()),
        unknownFrames=len(missing),
        unknownFrameRanges=ranges(missing),
    )


def complete(directory, video, media_id, uid, key, body, info):
    h = w.digest(["complete-a", media_id, body])
    # Replay before re-reading a mutable workspace or doing expensive video hashing.
    with closing(connect()) as c:
        prior = c.execute(
            "SELECT * FROM review_write_receipts WHERE user_id=? AND request_key=?",
            (uid, key),
        ).fetchone()
        if prior:
            if prior["request_hash"] != h:
                raise w.ReviewError(
                    "IDEMPOTENCY_KEY_REUSED", "重试标识不能用于不同操作"
                )
            return json.loads(prior["response_json"])
    revision, frames = source(directory, uid, info)
    if revision != body["expectedSourceRevision"]:
        raise w.ReviewError(
            "SOURCE_REVISION_CONFLICT", "标注来源已变化，请重新预览后送审"
        )
    if body["confirmComplete"] is not True:
        raise w.ReviewError("CONFIRM_COMPLETION_REQUIRED", "请确认已完成视频标注", 422)
    for r in body["explicitEmptyFrameRanges"]:
        start, end = r["start"], r["end"]
        if start < 0 or end < start or end >= info["frameCount"]:
            raise w.ReviewError("INVALID_EMPTY_RANGE", "空帧范围超出视频", 422)
        for i in range(start, end + 1):
            if frames.get(i):
                raise w.ReviewError(
                    "NONEMPTY_FRAME", "不能把已有对象的帧声明为空帧", 422
                )
            frames[i] = {}
    if len(frames) != info["frameCount"]:
        raise w.ReviewError(
            "INCOMPLETE_ANNOTATION", "仍有未知帧，请补齐标注或明确声明无对象帧", 422
        )
    frozen = [
        dict(
            frameIndex=i,
            coverage="objects" if frames[i] else "empty",
            objects=sorted(frames[i].values(), key=lambda x: x["objectId"]),
        )
        for i in range(info["frameCount"])
    ]
    sha = hashlib.sha256()
    with video.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    with closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute(
            "SELECT * FROM review_write_receipts WHERE user_id=? AND request_key=?",
            (uid, key),
        ).fetchone()
        if prior:
            if prior["request_hash"] != h:
                raise w.ReviewError(
                    "IDEMPOTENCY_KEY_REUSED", "重试标识不能用于不同操作"
                )
            return json.loads(prior["response_json"])
        if source(directory, uid, info)[0] != revision:
            raise w.ReviewError(
                "SOURCE_REVISION_CONFLICT", "读取过程中标注来源变化，请重新预览"
            )
        mid = "mrev_" + sha.hexdigest()[:16]
        c.execute(
            "INSERT OR IGNORE INTO media_revisions(id,media_id,sha256,file_size,width,height,fps,frame_count) VALUES (?,?,?,?,?,?,?,?)",
            (
                mid,
                media_id,
                sha.hexdigest(),
                video.stat().st_size,
                info["width"],
                info["height"],
                info["fps"],
                info["frameCount"],
            ),
        )
        snapshot = w.digest(frozen)
        existing = c.execute(
            """SELECT s.id FROM review_sessions s JOIN annotation_baselines a ON a.id=s.baseline_id
            WHERE a.media_revision_id=? AND a.submitted_by=? AND a.snapshot_hash=? AND s.state NOT IN ('withdrawn','returned_to_annotation') ORDER BY a.created_at DESC LIMIT 1""",
            (mid, uid, snapshot),
        ).fetchone()
        if existing:
            sid = existing[0]
        else:
            # A changed workspace must not silently create a parallel task while
            # another reviewer is working. Withdrawal/explicit return retires the
            # old task first; their immutable snapshots remain available.
            active = c.execute('''SELECT s.id FROM review_sessions s
                JOIN annotation_baselines a ON a.id=s.baseline_id
                JOIN media_revisions m ON m.id=a.media_revision_id
                WHERE m.media_id=? AND s.state NOT IN ('withdrawn','returned_to_annotation') LIMIT 1''', (media_id,)).fetchone()
            if active:
                raise w.ReviewError('MEDIA_ALREADY_SUBMITTED', '该视频已有送审任务，请先撤回送审或由当前审查员退回重新标注')
            bid = "abl_" + uuid.uuid4().hex
            sid = "rs_" + uuid.uuid4().hex
            c.execute(
                "INSERT INTO annotation_baselines(id,media_revision_id,submitted_by,snapshot_hash,frame_count,object_count) VALUES (?,?,?,?,?,?)",
                (
                    bid,
                    mid,
                    uid,
                    snapshot,
                    len(frozen),
                    sum(len(f["objects"]) for f in frozen),
                ),
            )
            c.executemany(
                "INSERT INTO baseline_frames VALUES (?,?,?,?,?)",
                [
                    (
                        bid,
                        f["frameIndex"],
                        f["coverage"],
                        w.digest(f["objects"]),
                        w.packed(f["objects"]),
                    )
                    for f in frozen
                ],
            )
            c.execute(
                "INSERT INTO review_sessions(id,baseline_id,frame_count) VALUES (?,?,?)",
                (sid, bid, len(frozen)),
            )
            c.executemany(
                "INSERT INTO review_frames(session_id,frame_index) VALUES (?,?)",
                [(sid, f["frameIndex"]) for f in frozen],
            )
        if body.get('concentratedReview') and not existing:
            from .concentrated_review import register
            from .concentrated_review import read_rows
            rows = read_rows(directory / 'tracker_results.json')
            register(c, sid, rows, revision)
        result = {"session": w.session_data(c, w.session_row(c, sid), uid)}
        c.execute(
            "INSERT INTO review_write_receipts VALUES (?,?,?,?,?)",
            (uid, key, h, w.packed(result), w.now()),
        )
        c.commit()
    w.log.info(
        "annotation.completed media=%s session=%s actor=%s key=%s",
        media_id,
        sid,
        uid,
        key,
    )
    return result
