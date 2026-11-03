"""Annotation review frame checklist, pass record, staleness and the export gate."""

import json

import pytest
from app import annotation_review as review, db
from app.review_workflow import ReviewError
from .test_review_completion import INFO, source
from .test_review_workflow import task

MID = "complete-media"


def obj(oid, x=10):
    return {"objectId": oid, "bbox": [x, 10, x + 8, 18], "classKey": "sperm"}


def frames_of(state):
    return [i for start, end in state["frames"] for i in range(start, end + 1)]


def check_all(source, state, uid=1):
    current = review.load(source, INFO)
    for i in frames_of(state):
        state = review.check(MID, state["reviewId"], i, "ok" if current[i] else "empty", uid, current)
    return state


def edit_manual(source, x):
    state = json.loads((source / "workspace_state.json").read_text())
    state["manualAnnotations"][0]["bbox"]["x"] = x
    state["revision"] = state.get("revision", 0) + 1  # As every real workspace save does.
    state["updatedBy"] = 2  # Someone else's save must not matter for review or export.
    (source / "workspace_state.json").write_text(json.dumps(state))


def test_first_round_lists_every_frame_and_rereview_only_the_changed_ones():
    frames = [[obj(1)], None, [], [obj(1)], [obj(1), obj(2, 30)]]
    assert review.build_items(frames, None) == [dict(kind="frames", startFrame=0, endFrame=4)]
    later = json.loads(json.dumps(frames))
    later[1] = [obj(3)]
    later[3][0]["bbox"] = [11, 10, 19, 18]
    later[4] = later[4][:1]
    assert review.item_frames(review.build_items(later, frames)) == [1, 3, 4]
    assert review.build_items(frames, frames) == []


def test_full_round_records_changes_and_gates_export(source):
    state = review.start(MID, source, INFO, 1)
    assert review.start(MID, source, INFO, 2)["reviewId"] == state["reviewId"]  # One open round per video.
    assert state["frames"] == [[0, 2]] and (state["checked"], state["total"]) == (0, 3)
    with pytest.raises(ReviewError) as e:
        review.pass_review(MID, source, INFO, state["reviewId"], 1)
    assert e.value.code == "REVIEW_INCOMPLETE" and "3 帧" in e.value.message
    # The reviewer fixes a box directly in the shared workspace, then confirms every frame.
    edit_manual(source, 30)
    state = check_all(source, state, uid=2)
    assert state["checked"] == 3 and state["checkedFrames"][-1] == [2, 2, "empty"]
    passed = review.pass_review(MID, source, INFO, state["reviewId"], 2)
    assert passed["state"] == "passed" and passed["passedBy"] == 2
    assert passed["changes"]["moved"] == 1 and passed["changes"]["frames"][0]["frameIndex"] == 0
    assert passed["changes"]["frames"][0]["checkedBy"] == 2 and passed["changes"]["reviewers"] == [2]
    status = review.status(MID, source, INFO)
    assert status["status"] == "passed" and status["unknownFrames"] == 1
    assert review.summary([(MID, source)]) == {MID: "passed"}
    with pytest.raises(ReviewError) as e:
        review.start(MID, source, INFO, 1)
    assert e.value.code == "REVIEW_ALREADY_PASSED"
    from app.annotation_completion import source as read
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        assert review.require_passed(c, MID, frames, 3) == {2}
    # Any later edit invalidates the pass until a re-review covers it.
    edit_manual(source, 40)
    assert review.status(MID, source, INFO)["status"] == "stale"
    assert review.summary([(MID, source)]) == {MID: "stale"}
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        with pytest.raises(ReviewError, match="重新审查"):
            review.require_passed(c, MID, frames, 3)
    again = review.start(MID, source, INFO, 1)
    assert again["round"] == 2 and again["frames"] == [[0, 0]]
    assert review.status(MID, source, INFO)["status"] == "open"
    # Frame 2 was confirmed empty last round and has not changed: it carries over.
    again = check_all(source, again)
    review.pass_review(MID, source, INFO, again["reviewId"], 1)
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        assert review.require_passed(c, MID, frames, 3) == {2}


def test_check_accepts_only_round_frames_and_the_result_that_fits_the_frame(source):
    state = review.start(MID, source, INFO, 1)
    rid, current = state["reviewId"], review.load(source, INFO)
    assert current[0] and current[2] is None
    with pytest.raises(ReviewError) as e:
        review.check(MID, rid, 0, "empty", 1, current)
    assert e.value.code == "INVALID_RESULT"
    with pytest.raises(ReviewError) as e:
        review.check(MID, rid, 2, "ok", 1, current)
    assert e.value.code == "INVALID_RESULT"
    with pytest.raises(ReviewError) as e:
        review.check(MID, rid, 0, "fixed", 1, current)
    assert e.value.code == "INVALID_RESULT"
    with pytest.raises(ReviewError) as e:
        review.check(MID, "arev_other", 0, "ok", 1, current)
    assert e.value.code == "REVIEW_NOT_CURRENT"
    # A frame that is known to be empty is simply checked; it needs no declaration.
    known_empty = list(current)
    known_empty[1] = []
    state = review.check(MID, rid, 1, "empty", 1, known_empty)
    assert state["checkedFrames"] == [[1, 1, "ok"]]
    # Re-confirming a frame updates the record instead of counting twice.
    state = review.check(MID, rid, 0, "ok", 3, current)
    state = review.check(MID, rid, 0, "ok", 3, current)
    assert state["checked"] == 2 and state["checkedFrames"] == [[0, 1, "ok"]]


def test_rereview_rejects_frames_outside_the_changes(source):
    state = check_all(source, review.start(MID, source, INFO, 1))
    review.pass_review(MID, source, INFO, state["reviewId"], 1)
    edit_manual(source, 30)
    again = review.start(MID, source, INFO, 1)
    with pytest.raises(ReviewError) as e:
        review.check(MID, again["reviewId"], 1, "ok", 1, review.load(source, INFO))
    assert e.value.code == "ITEM_NOT_FOUND"


def test_pass_rejects_frames_that_are_still_unknown(source):
    state = review.start(MID, source, INFO, 1)
    current = review.load(source, INFO)
    for i in (0, 1):
        review.check(MID, state["reviewId"], i, "ok", 1, current)
    # Frame 2 checked while it had a box, then the box was removed again: still unknown.
    boxed = list(current)
    boxed[2] = [obj(1)]
    review.check(MID, state["reviewId"], 2, "ok", 1, boxed)
    with pytest.raises(ReviewError) as e:
        review.pass_review(MID, source, INFO, state["reviewId"], 1)
    assert e.value.code == "UNKNOWN_FRAMES"


def test_review_api_round_trip(source, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import annotation_review_routes as routes
    from app.auth import sign_jwt
    monkeypatch.setattr(routes, "media_context", lambda mid, allow_tracking=False: (source, source / "video.avi", INFO))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    base = f"/api/annotation-review/media/{MID}"
    assert client.get(base).status_code == 401
    client.headers["Authorization"] = "Bearer " + sign_jwt({"uid": 1})
    assert client.get(base).json()["status"] == "none"
    state = client.post(base + "/start").json()
    rid = state["review"]["reviewId"]
    assert state["status"] == "open" and state["review"]["checked"] == 0 and state["review"]["frames"] == [[0, 2]]
    for frame, result in ((0, "ok"), (1, "ok"), (2, "empty")):
        response = client.put(f"{base}/frames/{frame}", json={"reviewId": rid, "result": result})
        assert response.status_code == 200, response.text
    assert client.put(f"{base}/frames/0", json={"reviewId": rid, "result": "maybe"}).status_code == 422
    assert client.put(f"{base}/frames/9", json={"reviewId": rid, "result": "ok"}).json()["code"] == "ITEM_NOT_FOUND"
    assert client.post(base + "/pass", json={"reviewId": "arev_other"}).json()["code"] == "REVIEW_NOT_CURRENT"
    done = client.post(base + "/pass", json={"reviewId": rid})
    assert done.status_code == 200 and done.json()["status"] == "passed"
    assert client.put(f"{base}/frames/0", json={"reviewId": rid, "result": "ok"}).json()["code"] == "REVIEW_CLOSED"


def test_video_without_saved_workspace_is_not_ready_rather_than_failing(source):
    (source / "workspace_state.json").unlink()
    state = review.status(MID, source, INFO)
    assert state["status"] == "none" and "先在标注页" in state["notReady"]
    with pytest.raises(ReviewError) as e:
        review.start(MID, source, INFO, 1)
    assert e.value.code == "SOURCE_UNAVAILABLE"


def test_round_reports_last_actor_and_activity(source):
    state = review.start(MID, source, INFO, 1)
    assert state['lastActorId'] == 1 and state['lastActivityAt'] == state['startedAt']
    updated = review.check(MID, state['reviewId'], 0, 'ok', 3, review.load(source, INFO))
    assert updated['lastActorId'] == 3 and updated['lastActivityAt']


def test_status_cache_uses_revision_stamp_and_pass_reads_fresh(source, monkeypatch):
    review.clear_load_cache()
    original = review.annotation.source
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(review.annotation, 'source', counted)
    review.status(MID, source, INFO)
    review.status(MID, source, INFO)
    assert len(calls) == 1
    edit_manual(source, 31)
    review.status(MID, source, INFO)
    assert len(calls) == 2
    review.clear_load_cache()


def legacy_round(rid, state, frames, items, round_no=1, **extra):
    with db.connect() as c:
        review.migrate(c)
        columns = dict(id=rid, media_id=MID, round=round_no, state=state, started_by=1, started_at="2026-10-01T00:00:00Z",
                       start_revision=review.revision(frames), start_frames_json=json.dumps(frames),
                       items_json=json.dumps(items), evidence_policy="actionable-review-v7-old", **extra)
        c.execute(f"INSERT INTO annotation_reviews({','.join(columns)}) VALUES ({','.join('?' * len(columns))})", tuple(columns.values()))


def test_open_round_of_the_old_checklist_is_rebuilt_as_frames_without_its_checks(source):
    current = review.load(source, INFO)
    items = [dict(id="doubt-1", kind="doubt", category="missing", startFrame=0, endFrame=0, frame=0),
             dict(id="unknown-2", kind="unknown", startFrame=2, endFrame=2, frame=2)]
    legacy_round("arev_legacy", "open", current, items)
    with db.connect() as c:
        c.execute("INSERT INTO annotation_review_checks VALUES (?,?,?,?,?)", ("arev_legacy", "doubt-1", "ok", 1, "2026-10-01T00:01:00Z"))
    state = review.status(MID, source, INFO)
    assert state["status"] == "open"
    assert state["review"]["frames"] == [[0, 2]] and state["review"]["checked"] == 0
    with db.connect() as c:
        row = review.latest(c, MID)
        assert row["evidence_policy"] == review.CHECKLIST_POLICY
        history = json.loads(row["checklist_history_json"])
        assert history[0]["items"] == items and "doubt-1" in history[0]["checks"]
    state = check_all(source, review.start(MID, source, INFO, 1))
    assert review.pass_review(MID, source, INFO, state["reviewId"], 1)["state"] == "passed"


def test_passed_round_of_the_old_checklist_still_gates_export_with_its_empty_frames(source):
    current = review.load(source, INFO)
    legacy_round("arev_old_pass", "passed", current, [dict(id="unknown-1", kind="unknown", startFrame=2, endFrame=2, frame=2)],
                 passed_by=1, passed_at="2026-10-01T00:02:00Z", passed_revision=review.revision(current),
                 passed_frames_json=json.dumps(current), passed_stamp=review.stamp(source))
    with db.connect() as c:
        c.execute("INSERT INTO annotation_review_checks VALUES (?,?,?,?,?)", ("arev_old_pass", "unknown-1", "empty", 1, "2026-10-01T00:01:00Z"))
    state = review.status(MID, source, INFO)
    assert state["status"] == "passed" and state["review"]["frames"] == []
    from app.annotation_completion import source as read
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        assert review.require_passed(c, MID, frames, 3) == {2}
    # A re-review after an edit lists only the change; the old empty confirmation carries over.
    edit_manual(source, 30)
    again = check_all(source, review.start(MID, source, INFO, 1))
    assert again["frames"] == [[0, 0]]
    review.pass_review(MID, source, INFO, again["reviewId"], 1)
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        assert review.require_passed(c, MID, frames, 3) == {2}
