"""Annotation review: every frame checked by a person, a pass record, and the export gate.

Reviewers correct boxes in the same workspace the annotator uses; there is no second
copy to compare and no automatic doubt finding. A first round lists every frame of
the video; a re-review after a pass lists only the frames whose content changed.
The reviewer looks at each listed frame, fixes it in place, and confirms it (frames
with no annotation at all are confirmed as empty). Passing records what changed since
the round started, and export requires a pass on the current content.
"""

import json
import logging
import threading
import uuid
from collections import OrderedDict
from contextlib import closing

from . import annotation_completion as annotation
from .db import connect
from .review_workflow import ReviewError, digest, now, packed

log = logging.getLogger("review.annotation_review")

RESULTS = {"ok", "empty"}
# Stored in annotation_reviews.evidence_policy. Open rounds built by an older policy
# (the doubt/sampling checklist) are rebuilt as a frame checklist when next read.
CHECKLIST_POLICY = "frame-review-v1"


def migrate(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS annotation_reviews (
      id TEXT PRIMARY KEY, media_id TEXT NOT NULL, round INTEGER NOT NULL,
      state TEXT NOT NULL, started_by INTEGER NOT NULL, started_at TEXT NOT NULL,
      start_revision TEXT NOT NULL, start_frames_json TEXT NOT NULL,
      items_json TEXT NOT NULL, escalated INTEGER NOT NULL DEFAULT 0,
      passed_by INTEGER, passed_at TEXT, passed_revision TEXT, passed_frames_json TEXT,
      passed_stamp TEXT, changes_json TEXT,
      UNIQUE(media_id, round)
    );
    CREATE TABLE IF NOT EXISTS annotation_review_checks (
      review_id TEXT NOT NULL REFERENCES annotation_reviews(id), item_id TEXT NOT NULL,
      result TEXT NOT NULL, actor_id INTEGER NOT NULL, checked_at TEXT NOT NULL,
      PRIMARY KEY(review_id, item_id)
    );
    -- Read-only history of the removed "not a sperm" decisions; no longer written.
    CREATE TABLE IF NOT EXISTS annotation_review_exclusions (
      id TEXT PRIMARY KEY, media_id TEXT NOT NULL, review_id TEXT NOT NULL,
      item_id TEXT NOT NULL, source TEXT NOT NULL,
      frames_json TEXT NOT NULL,
      actor_id INTEGER NOT NULL, created_at TEXT NOT NULL,
      revoked_at TEXT, revoked_by INTEGER
    );
    CREATE INDEX IF NOT EXISTS annotation_review_exclusions_active
      ON annotation_review_exclusions(media_id, revoked_at);
    """)
    columns={r[1] for r in c.execute('PRAGMA table_info(annotation_reviews)')}
    for name in ('evidence_policy','checklist_history_json','empty_frames_json'):
        if name not in columns:c.execute(f'ALTER TABLE annotation_reviews ADD COLUMN {name} TEXT')


# ---------------------------------------------------------------- content


def content(frames, frame_count):
    """Canonical frame list: objects per known frame, None for frames never annotated."""
    return [
        None if i not in frames else sorted(frames[i].values(), key=lambda o: o["objectId"])
        for i in range(frame_count)
    ]


def revision(frames_list):
    return digest(frames_list)


def stamp(directory):
    """Cheap change indicator for list views; the content digest stays authoritative."""
    from .annotation_state import read_state
    tracker = directory / "tracker_results.json"
    st = tracker.stat() if tracker.exists() else None
    return packed([int(read_state(directory).get("revision", 0)), st and st.st_size, st and st.st_mtime_ns])


_LOAD_CACHE = OrderedDict()
_LOAD_CACHE_LOCK = threading.Lock()
_LOAD_CACHE_SIZE = 8


def clear_load_cache():
    with _LOAD_CACHE_LOCK:_LOAD_CACHE.clear()


def load_cached(media_id, directory, info):
    """Reuse the parsed frame list only while workspace revision and file stamp match."""
    key = (media_id, stamp(directory))
    with _LOAD_CACHE_LOCK:
        if key in _LOAD_CACHE:
            _LOAD_CACHE.move_to_end(key)
            return _LOAD_CACHE[key]
    value = load(directory, info)
    with _LOAD_CACHE_LOCK:
        _LOAD_CACHE[key] = value
        _LOAD_CACHE.move_to_end(key)
        while len(_LOAD_CACHE) > _LOAD_CACHE_SIZE:
            _LOAD_CACHE.popitem(last=False)
    return value


def load(directory, info):
    _, frames = annotation.source(directory, None, info, require_author=False)
    return content(frames, info["frameCount"])


# ---------------------------------------------------------------- checklist


def spans(frames):
    out = []
    for i in sorted(frames):
        if out and out[-1][1] == i - 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return out


def changed_frames(old, new):
    return [i for i in range(len(new)) if i >= len(old) or old[i] != new[i]]


def target_frames(frames_list, previous):
    """Every frame on a first round; only the changed frames after a pass."""
    return list(range(len(frames_list))) if previous is None else changed_frames(previous, frames_list)


def build_items(frames_list, previous):
    return [dict(kind="frames", startFrame=s, endFrame=e) for s, e in spans(target_frames(frames_list, previous))]


def item_frames(items):
    return [i for item in items for i in range(item["startFrame"], item["endFrame"] + 1)]


def frame_key(frame):
    return f"frame-{frame}"


# ---------------------------------------------------------------- state


def latest(c, media_id):
    return c.execute("SELECT * FROM annotation_reviews WHERE media_id=? ORDER BY round DESC LIMIT 1", (media_id,)).fetchone()


def last_passed(c, media_id):
    return c.execute("SELECT * FROM annotation_reviews WHERE media_id=? AND state='passed' ORDER BY round DESC LIMIT 1", (media_id,)).fetchone()


def checks(c, review_id):
    return {r["item_id"]: dict(result=r["result"], actorId=r["actor_id"], checkedAt=r["checked_at"])
            for r in c.execute("SELECT * FROM annotation_review_checks WHERE review_id=?", (review_id,))}


def frame_checks(c, review_id):
    """{frameIndex: check} for a frame-checklist round."""
    return {int(k[6:]): v for k, v in checks(c, review_id).items() if k.startswith("frame-")}


def review_edited(c, media_id):
    """Objects a reviewer added or changed in any passed round: {frameIndex: {objectId}}.

    Training exports mark these boxes so model work can weight or study what the
    tracker got wrong (decision 0002). Annotator edits before review are not included.
    """
    out={}
    for row in c.execute("SELECT changes_json FROM annotation_reviews WHERE media_id=? AND state='passed' AND changes_json IS NOT NULL",(media_id,)):
        for frame in json.loads(row['changes_json']).get('frames',[]):
            out.setdefault(frame['frameIndex'],set()).update(frame.get('added',[])+frame.get('moved',[]))
    return out


def upgrade(c, row, frames_list):
    """Rebuild an open round of an older checklist policy as a frame checklist.

    The old items and their checks are archived; none of them counts for the new
    list. Completed rounds are history and never rewritten.
    """
    if row is None or row['state']!='open' or row['evidence_policy']==CHECKLIST_POLICY:return row
    passed=last_passed(c,row['media_id'])
    previous=json.loads(passed['passed_frames_json']) if passed else None
    history=json.loads(row['checklist_history_json'] or '[]')
    history.append(dict(at=now(),policy=row['evidence_policy'],items=json.loads(row['items_json']),checks=checks(c,row['id'])))
    items=build_items(frames_list,previous)
    c.execute('UPDATE annotation_reviews SET items_json=?,evidence_policy=?,checklist_history_json=? WHERE id=?',
              (packed(items),CHECKLIST_POLICY,packed(history),row['id']))
    log.info('annotation_review.checklist_rebuilt media=%s review=%s old_policy=%s frames=%s',
             row['media_id'],row['id'],row['evidence_policy'],len(item_frames(items)))
    return latest(c,row['media_id'])


def round_data(c, row):
    if row is None:
        return None
    if row["evidence_policy"] == CHECKLIST_POLICY:
        targets = item_frames(json.loads(row["items_json"]))
        done = frame_checks(c, row["id"])
    else:  # A passed round of the old checklist: only its record is shown.
        targets, done = [], {}
    checked = []
    for i in targets:
        result = done.get(i, {}).get("result")
        if result is None:
            continue
        if checked and checked[-1][1] == i - 1 and checked[-1][2] == result:
            checked[-1][1] = i
        else:
            checked.append([i, i, result])
    latest_check = c.execute("SELECT actor_id,checked_at FROM annotation_review_checks WHERE review_id=? ORDER BY checked_at DESC,rowid DESC LIMIT 1",
                             (row["id"],)).fetchone()
    return dict(reviewId=row["id"], round=row["round"], state=row["state"], startedBy=row["started_by"],
                startedAt=row["started_at"], frames=spans(targets), checkedFrames=checked,
                lastActorId=latest_check["actor_id"] if latest_check else row["started_by"],
                lastActivityAt=latest_check["checked_at"] if latest_check else row["started_at"],
                checked=sum(1 for i in targets if i in done), total=len(targets),
                passedBy=row["passed_by"], passedAt=row["passed_at"],
                changes=json.loads(row["changes_json"]) if row["changes_json"] else None)


def status(media_id, directory, info):
    try:
        frames_list = load_cached(media_id, directory, info)
    except ReviewError as e:
        if e.code != "SOURCE_UNAVAILABLE":
            raise
        # Nothing annotated yet: not an error for the review page, just not ready.
        return dict(mediaId=media_id, status="none", currentRevision=None, unknownFrames=info["frameCount"],
                    review=None, lastPassed=None, notReady=e.message)
    current = revision(frames_list)
    with closing(connect()) as c, c:
        row = upgrade(c, latest(c, media_id), frames_list)
        passed = last_passed(c, media_id)
        if row is None:
            state = "none"
        elif row["state"] == "open":
            state = "open"
        elif passed and passed["passed_revision"] == current:
            state = "passed"
            fresh = stamp(directory)
            if passed["passed_stamp"] != fresh:  # A save without content change keeps the pass.
                c.execute("UPDATE annotation_reviews SET passed_stamp=? WHERE id=?", (fresh, passed["id"]))
        else:
            state = "stale"
        return dict(mediaId=media_id, status=state, currentRevision=current,
                    unknownFrames=sum(f is None for f in frames_list),
                    review=round_data(c, row),
                    lastPassed=None if passed is None else dict(reviewId=passed["id"], round=passed["round"],
                                                                passedBy=passed["passed_by"], passedAt=passed["passed_at"]))


def summary(media_dirs):
    """Status for the media list without reading tracking files; 'changed' means re-check on open."""
    out = {}
    with closing(connect()) as c:
        for media_id, directory in media_dirs:
            row = latest(c, media_id)
            if row is None:
                out[media_id] = "none"
            elif row["state"] == "open":
                out[media_id] = "open"
            else:
                passed = last_passed(c, media_id)
                try:
                    same = passed is not None and passed["passed_stamp"] == stamp(directory)
                except OSError:
                    same = False
                out[media_id] = "passed" if same else "stale"
    return out


# ---------------------------------------------------------------- actions


def start(media_id, directory, info, actor):
    frames_list = load_cached(media_id, directory, info)
    with closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        row = latest(c, media_id)
        if row is not None and row["state"] == "open":
            return round_data(c, upgrade(c, row, frames_list))
        passed = last_passed(c, media_id)
        previous = json.loads(passed["passed_frames_json"]) if passed else None
        if passed and passed["passed_revision"] == revision(frames_list):
            raise ReviewError("REVIEW_ALREADY_PASSED", "当前标注已经审查通过，没有需要重新审查的改动", 409)
        rid = "arev_" + uuid.uuid4().hex
        items = build_items(frames_list, previous)
        c.execute(
            "INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json,evidence_policy) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, media_id, (row["round"] + 1) if row else 1, "open", actor, now(), revision(frames_list), packed(frames_list), packed(items), CHECKLIST_POLICY),
        )
        log.info("annotation_review.started media=%s review=%s actor=%s frames=%s rereview=%s", media_id, rid, actor, len(item_frames(items)), previous is not None)
        return round_data(c, latest(c, media_id))


def open_round(c, media_id, review_id):
    row = latest(c, media_id)
    if row is None or row["id"] != review_id:
        raise ReviewError("REVIEW_NOT_CURRENT", "这一轮审查已不是当前轮次，请刷新", 409)
    if row["state"] != "open":
        raise ReviewError("REVIEW_CLOSED", "这一轮审查已结束", 409)
    return row


def check(media_id, review_id, frame, result, actor, current_frames):
    """Record that a person looked at one frame: ``ok`` for a frame with annotations
    (as it is now, after any correction), ``empty`` for a frame without any."""
    if result not in RESULTS:
        raise ReviewError("INVALID_RESULT", "检查结果无效", 422)
    with closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        row = upgrade(c, open_round(c, media_id, review_id), current_frames)
        if frame not in set(item_frames(json.loads(row["items_json"]))):
            raise ReviewError("ITEM_NOT_FOUND", f"第 {frame + 1} 帧不在本轮审查范围内", 404)
        unknown = current_frames[frame] is None
        if result == "empty" and not unknown:
            if current_frames[frame]:
                raise ReviewError("INVALID_RESULT", "这一帧已有标注，不能确认为无目标", 422)
            result = "ok"  # Already a known empty frame: nothing to declare.
        if result == "ok" and unknown:
            raise ReviewError("INVALID_RESULT", "这一帧没有任何标注，请补标或确认无目标", 422)
        c.execute(
            "INSERT INTO annotation_review_checks VALUES (?,?,?,?,?) ON CONFLICT(review_id,item_id) DO UPDATE SET result=excluded.result,actor_id=excluded.actor_id,checked_at=excluded.checked_at",
            (review_id, frame_key(frame), result, actor, now()),
        )
        return round_data(c, latest(c, media_id))


def diff(old, new):
    frames, totals = [], dict(added=0, removed=0, moved=0)
    for i, (a, b) in enumerate(zip(old, new)):
        if a == b:
            continue
        before = {o["objectId"]: o for o in a or []}
        after = {o["objectId"]: o for o in b or []}
        change = dict(frameIndex=i,
                      added=sorted(set(after) - set(before)),
                      removed=sorted(set(before) - set(after)),
                      moved=sorted(k for k in set(before) & set(after) if before[k] != after[k]))
        for key in totals:
            totals[key] += len(change[key])
        frames.append(change)
    return dict(totals, frames=frames)


def confirmed_empty(c, review_id):
    """Frames a passed round declared empty (still without annotations at pass time)."""
    row = c.execute("SELECT items_json,empty_frames_json FROM annotation_reviews WHERE id=?", (review_id,)).fetchone()
    if row["empty_frames_json"] is not None:
        return set(json.loads(row["empty_frames_json"]))
    done = checks(c, review_id)  # Rounds of the old checklist: confirmed unknown spans.
    out = set()
    for item in json.loads(row["items_json"]):
        if item["kind"] == "unknown" and done.get(item["id"], {}).get("result") == "empty":
            out.update(range(item["startFrame"], item["endFrame"] + 1))
    return out


def pass_review(media_id, directory, info, review_id, actor):
    frames_list = load(directory, info)
    with closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        row = upgrade(c, open_round(c, media_id, review_id), frames_list)
        targets = item_frames(json.loads(row["items_json"]))
        done = frame_checks(c, review_id)
        missing = [i for i in targets if i not in done]
        if missing:
            raise ReviewError("REVIEW_INCOMPLETE", f"还有 {len(missing)} 帧没有检查（第 {missing[0] + 1} 帧起）", 409)
        # Unlisted frames are unchanged since the last pass; its empty confirmations carry over.
        passed = last_passed(c, media_id)
        carried = confirmed_empty(c, passed["id"]) if passed else set()
        listed = set(targets)
        empty = {i for i, f in enumerate(frames_list) if f is None and
                 (done.get(i, {}).get("result") == "empty" if i in listed else i in carried)}
        unknown = [i for i, f in enumerate(frames_list) if f is None and i not in empty]
        if unknown:
            raise ReviewError("UNKNOWN_FRAMES", f"还有 {len(unknown)} 帧没有标注，也没有确认为无目标（第 {unknown[0] + 1} 帧起）", 409)
        changes = diff(json.loads(row["start_frames_json"]), frames_list)
        for change in changes["frames"]:
            change["checkedBy"] = done.get(change["frameIndex"], {}).get("actorId")
        changes["reviewers"] = sorted({d["actorId"] for i, d in done.items() if i in listed})
        c.execute(
            "UPDATE annotation_reviews SET state='passed',passed_by=?,passed_at=?,passed_revision=?,passed_frames_json=?,passed_stamp=?,changes_json=?,empty_frames_json=? WHERE id=?",
            (actor, now(), revision(frames_list), packed(frames_list), stamp(directory), packed(changes), packed(sorted(empty)), review_id),
        )
        log.info("annotation_review.passed media=%s review=%s actor=%s frames=%s added=%s removed=%s moved=%s", media_id, review_id, actor, len(targets), changes["added"], changes["removed"], changes["moved"])
        return round_data(c, latest(c, media_id))


def require_passed(c, media_id, frames, frame_count):
    """Export gate: the frames to be exported must equal the last passed review."""
    passed = last_passed(c, media_id)
    row = latest(c, media_id)
    if passed is None:
        raise ReviewError("REVIEW_REQUIRED", "该视频还没有审查通过，不能导出训练数据集", 409)
    if row["state"] == "open":
        raise ReviewError("REVIEW_REQUIRED", "该视频正在重新审查，审查通过后才能导出", 409)
    if passed["passed_revision"] != revision(content(frames, frame_count)):
        raise ReviewError("REVIEW_REQUIRED", "审查通过后标注又有改动，请重新审查后再导出", 409)
    return confirmed_empty(c, passed["id"])
