"""Read-only history of annotation review rounds for the records page.

A list view: it reads the database and the same cheap stamps as the media list, never the
tracking files. So the progress of an open round counts the confirmations on record; the
review page, which compares them with the current content, stays the authority on what is
still valid. Nothing here affects review state or the export gate.
"""

import json
import logging
from contextlib import closing

from fastapi import HTTPException

from . import annotation_review as review
from .db import connect
from .review_workflow import ReviewError

log = logging.getLogger("review.annotation_review")

# Not SELECT *: the frame snapshots of one round alone can run to megabytes.
LIST_COLUMNS = "id,media_id,round,state,started_by,started_at,passed_by,passed_at,passed_stamp,items_json,evidence_policy,changes_json"


def _names(c):
    return {r["id"]: r["username"] for r in c.execute("SELECT id,username FROM users")}


def _person(names, uid):
    return None if uid is None else dict(id=uid, name=names.get(uid) or f"用户 #{uid}")


def _is_legacy(row):
    return row["evidence_policy"] != review.CHECKLIST_POLICY


def _total(row):
    items = json.loads(row["items_json"])
    return len(items) if _is_legacy(row) else len(review.item_frames(items))


def _change_totals(row):
    if not row["changes_json"]:
        return None
    changes = json.loads(row["changes_json"])
    return dict(added=changes["added"], removed=changes["removed"], moved=changes["moved"], frames=len(changes["frames"]))


def _standing(row, newest_round, directory):
    """What a passed round means today; None while open or once its video is gone.

    Judged by the same stamp as the media list: a save without a content change reads as
    stale until the video is next opened. An unreadable workspace reads as stale as well.
    """
    if row["state"] != "passed":
        return None
    if row["round"] != newest_round:
        return "superseded"
    if directory is None:
        return None
    try:
        return "current" if row["passed_stamp"] == review.stamp(directory) else "stale"
    except (OSError, ValueError, HTTPException) as e:  # read_state reports a broken workspace as HTTP 500.
        log.warning("annotation_review.records_stamp_unreadable media=%s error=%s", row["media_id"], e)
        return "stale"


def rounds(find_media):
    """Every round, latest activity first. ``find_media(mediaId)`` gives (folder, name) of a video that still exists."""
    with closing(connect()) as c:
        rows = c.execute(f"SELECT {LIST_COLUMNS} FROM annotation_reviews").fetchall()
        names = _names(c)
        tally = {}
        for r in c.execute(
            "SELECT review_id,actor_id,COUNT(*) AS frames,SUM(result='empty') AS empty,MAX(checked_at) AS last_at "
            "FROM annotation_review_checks GROUP BY review_id,actor_id ORDER BY review_id,MIN(checked_at),actor_id"
        ):
            tally.setdefault(r["review_id"], []).append(r)
    media = {row["media_id"]: find_media(row["media_id"]) for row in rows}
    newest = {}
    for row in rows:
        newest[row["media_id"]] = max(newest.get(row["media_id"], 0), row["round"])
    out = []
    for row in rows:
        found, people = media[row["media_id"]], tally.get(row["id"], [])
        checked = sum(p["frames"] for p in people)
        last_check = max((p["last_at"] for p in people), default=row["started_at"])
        out.append(dict(
            reviewId=row["id"], mediaId=row["media_id"], mediaName=found[1] if found else row["media_id"], mediaDeleted=not found,
            round=row["round"], state=row["state"], standing=_standing(row, newest[row["media_id"]], found[0] if found else None),
            legacy=_is_legacy(row), startedBy=_person(names, row["started_by"]), startedAt=row["started_at"],
            passedBy=_person(names, row["passed_by"]), passedAt=row["passed_at"],
            lastActivityAt=max(last_check, row["passed_at"] or ""),
            # A frame a colleague changed mid-round joins the list without being in items_json.
            checked=checked, total=max(_total(row), checked), emptyFrames=sum(p["empty"] for p in people),
            reviewers=[dict(_person(names, p["actor_id"]), frames=p["frames"]) for p in people],
            changes=_change_totals(row),
        ))
    out.sort(key=lambda r: r["lastActivityAt"], reverse=True)
    return out


def change_spans(frames, names):
    """Per-frame changes, with neighbouring frames merged when object IDs and confirmer all match."""
    out = []
    for frame in sorted(frames, key=lambda f: f["frameIndex"]):
        i, same = frame["frameIndex"], (frame["added"], frame["removed"], frame["moved"], frame.get("checkedBy"))
        last = out[-1] if out else None
        if last and last["end"] == i - 1 and last["_same"] == same:
            last["end"] = i
        else:
            out.append(dict(start=i, end=i, added=frame["added"], removed=frame["removed"], moved=frame["moved"],
                            checkedBy=_person(names, frame.get("checkedBy")), _same=same))
    for span in out:
        del span["_same"]
    return out


def _confirmations(rows, legacy, names):
    groups = {}
    for r in rows:
        group = groups.setdefault((r["actor_id"], r["result"]), dict(frames=[], items=0, lastAt=r["checked_at"]))
        group["items"] += 1
        group["lastAt"] = max(group["lastAt"], r["checked_at"])
        if not legacy and r["item_id"].startswith("frame-"):
            group["frames"].append(int(r["item_id"][6:]))
    out = [dict(actor=_person(names, actor), result=result, frames=g["items"], spans=review.spans(g["frames"]), lastAt=g["lastAt"])
           for (actor, result), g in groups.items()]
    out.sort(key=lambda g: (g["spans"][0][0] if g["spans"] else 0, g["lastAt"]))
    return out


def detail(review_id):
    """Who confirmed which frames and, once passed, what changed. Loaded on demand: it can run to thousands of frames."""
    with closing(connect()) as c:
        row = c.execute("SELECT * FROM annotation_reviews WHERE id=?", (review_id,)).fetchone()
        if row is None:
            raise ReviewError("REVIEW_NOT_FOUND", "审查记录不存在", 404)
        names = _names(c)
        checks = c.execute("SELECT item_id,result,actor_id,checked_at FROM annotation_review_checks WHERE review_id=?", (review_id,)).fetchall()
    changes = json.loads(row["changes_json"]) if row["changes_json"] else None
    return dict(reviewId=review_id, legacy=_is_legacy(row), confirmations=_confirmations(checks, _is_legacy(row), names),
                changes=change_spans(changes["frames"], names) if changes else [])
