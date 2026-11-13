"""Annotation review by target: one answer per track, pass conditions, staleness and the export gate."""

import json

import pytest
from app import annotation_review as review, db
from app.review_workflow import ReviewError
from .test_review_completion import INFO, source
from .test_review_workflow import task

MID = "complete-media"


def obj(oid, x=10):
    return {"objectId": oid, "bbox": [x, 10, x + 8, 18], "classKey": "sperm"}


def answer_all(source, state, uid=1, result="tail"):
    current = review.load(source, INFO)
    for oid in state["targets"]:
        state = review.check_target(MID, state["reviewId"], oid, result, uid, current)
    return state


def finish(source, state, uid=1):
    """Confirm frames without any annotation as empty and record the playthrough."""
    current = review.load(source, INFO)
    for start, end in state["unknownFrames"]:
        for i in range(start, end + 1):
            state = review.check(MID, state["reviewId"], i, "empty", uid, current)
    return review.record_playthrough(MID, state["reviewId"], uid, current)


def workspace(source):
    return json.loads((source / "workspace_state.json").read_text())


def save_workspace(source, state):
    state["revision"] = state.get("revision", 0) + 1  # As every real workspace save does.
    state["updatedBy"] = 2  # Someone else's save must not matter for review or export.
    (source / "workspace_state.json").write_text(json.dumps(state))


def edit_manual(source, x):
    state = workspace(source)
    state["manualAnnotations"][0]["bbox"]["x"] = x
    save_workspace(source, state)


def delete_target(source, oid):
    state = workspace(source)
    state["deletedObjectIds"] = sorted(set(state.get("deletedObjectIds", [])) | {oid})
    save_workspace(source, state)


def test_first_round_lists_every_target_and_rereview_only_the_changed_ones():
    frames = [[obj(1)], None, [], [obj(1)], [obj(1), obj(2, 30)]]
    assert review.build_target_items(frames, None) == [dict(kind="target", objectId=1), dict(kind="target", objectId=2)]
    later = json.loads(json.dumps(frames))
    later[1] = [obj(3)]  # A new target.
    later[4] = later[4][:1]  # Target 2 deleted: nothing left to judge.
    assert review.item_targets(review.build_target_items(later, frames)) == [3]
    later[3][0]["bbox"] = [11, 10, 19, 18]  # One box of target 1 moved: the whole track is asked again.
    assert review.item_targets(review.build_target_items(later, frames)) == [1, 3]
    assert review.build_target_items(frames, frames) == []


def test_full_round_records_changes_verdicts_and_gates_export(source):
    state = review.start(MID, source, INFO, 1)
    assert review.start(MID, source, INFO, 2)["reviewId"] == state["reviewId"]  # One open round per video.
    assert state["policy"] == review.TARGET_POLICY and state["checked"] == 0 and state["total"] == len(state["targets"]) > 0
    assert state["unknownFrames"] == [[2, 2]]
    with pytest.raises(ReviewError) as e:
        review.pass_review(MID, source, INFO, state["reviewId"], 1)
    assert e.value.code == "REVIEW_INCOMPLETE" and "目标" in e.value.message
    # The reviewer fixes a box directly in the shared workspace, then answers every target.
    edit_manual(source, 30)
    state = review.status(MID, source, INFO)["review"]
    state = answer_all(source, state, uid=2)
    assert state["checked"] == state["total"] and set(state["results"].values()) == {"tail"}
    with pytest.raises(ReviewError) as e:
        review.pass_review(MID, source, INFO, state["reviewId"], 2)
    assert e.value.code == "REVIEW_PLAYTHROUGH_REQUIRED"
    state = finish(source, state, uid=2)
    assert state["playthroughBy"] == 2 and state["emptyFrames"] == [[2, 2]]
    passed = review.pass_review(MID, source, INFO, state["reviewId"], 2)
    assert passed["state"] == "passed" and passed["passedBy"] == 2
    assert passed["changes"]["moved"] == 1 and passed["changes"]["reviewers"] == [2]
    assert set(passed["changes"]["targets"].values()) == {"tail"}
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
        assert set(review.verdicts(c, MID).values()) == {"tail"}
    # Any later edit invalidates the pass until a re-review covers it.
    edit_manual(source, 40)
    assert review.status(MID, source, INFO)["status"] == "stale"
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        with pytest.raises(ReviewError, match="重新审查"):
            review.require_passed(c, MID, frames, 3)
    # The re-review asks only about the changed target; unchanged answers and empty frames carry over.
    again = review.start(MID, source, INFO, 1)
    assert again["targets"] == [1]
    again = finish(source, answer_all(source, again, result="head"))
    review.pass_review(MID, source, INFO, again["reviewId"], 1)
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        assert review.require_passed(c, MID, frames, 3) == {2}
        assert review.verdicts(c, MID)[1] == "head"


def test_an_answer_vouches_for_the_track_as_it_was(source):
    state = answer_all(source, review.start(MID, source, INFO, 1))
    state = finish(source, state)
    edit_manual(source, 33)  # Changed after the answer: asked again.
    status = review.status(MID, source, INFO)["review"]
    assert 1 in status["changedTargets"] and status["checked"] == status["total"] - 1
    with pytest.raises(ReviewError) as e:
        review.pass_review(MID, source, INFO, state["reviewId"], 1)
    assert e.value.code == "REVIEW_TARGET_CHANGED"
    review.check_target(MID, state["reviewId"], 1, "tail", 1, review.load(source, INFO))
    assert review.pass_review(MID, source, INFO, state["reviewId"], 1)["state"] == "passed"


def test_not_a_sperm_must_actually_be_deleted_and_is_never_exported(source):
    state = review.start(MID, source, INFO, 1)
    current = review.load(source, INFO)
    for oid in state["targets"]:
        state = review.check_target(MID, state["reviewId"], oid, "no" if oid == 1 else "unsure", 1, current)
    state = finish(source, state)
    with pytest.raises(ReviewError) as e:
        review.pass_review(MID, source, INFO, state["reviewId"], 1)
    assert e.value.code == "REVIEW_INCOMPLETE" and "还没有删除" in e.value.message
    delete_target(source, 1)  # What the page does right after the answer.
    status = review.status(MID, source, INFO)["review"]
    assert 1 not in status["targets"] and status["results"]["1"] == "no"
    passed = review.pass_review(MID, source, INFO, state["reviewId"], 1)
    assert passed["state"] == "passed" and passed["changes"]["targets"]["1"] == "no"
    with db.connect() as c:
        assert 1 not in review.verdicts(c, MID)


def test_a_target_added_during_the_round_is_judged_too(source):
    state = review.start(MID, source, INFO, 1)
    ws = workspace(source)
    ws["manualAnnotations"].append({"source": "manual", "objectId": 7, "frameIndex": 1, "name": "sperm 7", "bbox": {"x": 50, "y": 50, "width": 5, "height": 5}})
    save_workspace(source, ws)  # E.g. a reviewer adds a missed head, or splits a track.
    status = review.status(MID, source, INFO)["review"]
    assert 7 in status["targets"] and status["total"] == state["total"] + 1


def test_answers_are_validated_and_bound_to_the_round(source):
    state = review.start(MID, source, INFO, 1)
    current = review.load(source, INFO)
    with pytest.raises(ReviewError) as e:
        review.check_target(MID, state["reviewId"], 1, "maybe", 1, current)
    assert e.value.code == "INVALID_RESULT"
    with pytest.raises(ReviewError) as e:
        review.check_target(MID, state["reviewId"], 99, "tail", 1, current)
    assert e.value.code == "ITEM_NOT_FOUND"
    with pytest.raises(ReviewError) as e:
        review.check_target(MID, "arev_other", 1, "tail", 1, current)
    assert e.value.code == "REVIEW_NOT_CURRENT"
    # Frames are confirmed only where nothing at all is annotated.
    with pytest.raises(ReviewError) as e:
        review.check(MID, state["reviewId"], 0, "empty", 1, current)
    assert e.value.code == "ITEM_NOT_FOUND"
    assert review.check(MID, state["reviewId"], 2, "empty", 1, current)["emptyFrames"] == [[2, 2]]


def test_unknown_frames_still_block_until_confirmed_empty(source):
    state = answer_all(source, review.start(MID, source, INFO, 1))
    state = review.record_playthrough(MID, state["reviewId"], 1, review.load(source, INFO))
    with pytest.raises(ReviewError) as e:
        review.pass_review(MID, source, INFO, state["reviewId"], 1)
    assert e.value.code == "UNKNOWN_FRAMES"


def test_review_waits_until_every_check_frame_was_looked_at(source):
    checks = [{"frame": 0}, {"frame": 1}]
    # A round someone opened before the prelabel scan existed (older behaviour).
    early = review.start(MID, source, INFO, 1)
    (source / "detector_tracks.json").write_text(json.dumps({"policy": "detector-track-v1", "checkFrames": [{"frame": 0, "kind": "fixed"}, {"frame": 1, "kind": "fixed"}], "tracks": []}))
    with pytest.raises(ReviewError) as e:
        review.start(MID, source, INFO, 1)
    assert e.value.code == "REVIEW_SCAN_INCOMPLETE" and "2 张" in e.value.message
    state = review.status(MID, source, INFO)
    assert state["status"] == "none" and state["stage"] == "scanning" and state["scanRemaining"] == 2 and state["review"] is None
    assert review.summary([(MID, source)])[MID] == "none"  # Listed as 查漏中, not 审查中.
    review.record_scan(MID, 0, 1, checks)
    assert review.status(MID, source, INFO)["scanRemaining"] == 1
    review.record_scan(MID, 1, 1, checks)
    resumed = review.status(MID, source, INFO)
    assert resumed["status"] == "open" and resumed["review"]["reviewId"] == early["reviewId"]  # The early round is kept.
    state = finish(source, answer_all(source, review.start(MID, source, INFO, 1)))
    assert review.pass_review(MID, source, INFO, state["reviewId"], 1)["state"] == "passed"


def test_repeat_answers_measure_consistency_and_never_count(source):
    state = review.start(MID, source, INFO, 1)
    current = review.load(source, INFO)
    oid = state["targets"][0]
    state = review.check_target(MID, state["reviewId"], oid, "tail", 1, current, repeat=True)
    assert state["checked"] == 0 and state["repeats"] == {str(oid): "tail"}
    state = review.check_target(MID, state["reviewId"], oid, "unsure", 1, current)
    assert state["consistency"] == {"pairs": 1, "same": 0}


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
    assert state["status"] == "open" and state["review"]["checked"] == 0
    for oid in state["review"]["targets"]:
        response = client.put(f"{base}/targets/{oid}", json={"reviewId": rid, "result": "tail"})
        assert response.status_code == 200, response.text
    assert client.put(f"{base}/targets/1", json={"reviewId": rid, "result": "maybe"}).status_code == 422
    assert client.put(f"{base}/targets/99", json={"reviewId": rid, "result": "tail"}).json()["code"] == "ITEM_NOT_FOUND"
    assert client.put(f"{base}/frames/2", json={"reviewId": rid, "result": "empty"}).status_code == 200
    assert client.post(base + "/pass", json={"reviewId": rid}).json()["code"] == "REVIEW_PLAYTHROUGH_REQUIRED"
    assert client.post(base + "/playthrough", json={"reviewId": rid}).status_code == 200
    assert client.post(base + "/pass", json={"reviewId": "arev_other"}).json()["code"] == "REVIEW_NOT_CURRENT"
    done = client.post(base + "/pass", json={"reviewId": rid})
    assert done.status_code == 200 and done.json()["status"] == "passed"
    assert client.put(f"{base}/targets/1", json={"reviewId": rid, "result": "tail"}).json()["code"] == "REVIEW_CLOSED"


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
    updated = review.check_target(MID, state['reviewId'], state['targets'][0], 'tail', 2, review.load(source, INFO))
    assert updated['lastActorId'] == 2 and updated['lastActorName'] == "B" and updated['lastActivityAt']


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


def legacy_round(rid, state, frames, items, round_no=1, policy="actionable-review-v7-old", **extra):
    with db.connect() as c:
        review.migrate(c)
        columns = dict(id=rid, media_id=MID, round=round_no, state=state, started_by=1, started_at="2026-10-01T00:00:00Z",
                       start_revision=review.revision(frames), start_frames_json=json.dumps(frames),
                       items_json=json.dumps(items), evidence_policy=policy, **extra)
        c.execute(f"INSERT INTO annotation_reviews({','.join(columns)}) VALUES ({','.join('?' * len(columns))})", tuple(columns.values()))


@pytest.mark.parametrize("policy,items", [
    (review.CHECKLIST_POLICY, [dict(kind="frames", startFrame=0, endFrame=2)]),
    ("actionable-review-v7-old", [dict(id="doubt-1", kind="doubt", category="missing", startFrame=0, endFrame=0, frame=0)]),
])
def test_open_round_of_an_older_checklist_is_rebuilt_as_targets_without_its_checks(source, policy, items):
    current = review.load(source, INFO)
    legacy_round("arev_legacy", "open", current, items, policy=policy)
    with db.connect() as c:
        c.execute("INSERT INTO annotation_review_checks(review_id,item_id,result,actor_id,checked_at) VALUES (?,?,?,?,?)", ("arev_legacy", "frame-0", "ok", 1, "2026-10-01T00:01:00Z"))
    state = review.status(MID, source, INFO)
    assert state["status"] == "open"
    assert state["review"]["policy"] == review.TARGET_POLICY and state["review"]["checked"] == 0 and state["review"]["targets"]
    with db.connect() as c:
        row = review.latest(c, MID)
        assert row["evidence_policy"] == review.TARGET_POLICY
        history = json.loads(row["checklist_history_json"])
        assert history[0]["items"] == items and "frame-0" in history[0]["checks"]
    state = finish(source, answer_all(source, review.start(MID, source, INFO, 1)))
    assert review.pass_review(MID, source, INFO, state["reviewId"], 1)["state"] == "passed"


def test_passed_round_of_the_frame_checklist_still_gates_export_with_its_empty_frames(source):
    current = review.load(source, INFO)
    legacy_round("arev_old_pass", "passed", current, [dict(kind="frames", startFrame=0, endFrame=2)], policy=review.CHECKLIST_POLICY,
                 passed_by=1, passed_at="2026-10-01T00:02:00Z", passed_revision=review.revision(current),
                 passed_frames_json=json.dumps(current), passed_stamp=review.stamp(source), empty_frames_json="[2]")
    state = review.status(MID, source, INFO)
    assert state["status"] == "passed" and state["review"]["frames"] == [[0, 2]]
    from app.annotation_completion import source as read
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        assert review.require_passed(c, MID, frames, 3) == {2}
        assert review.verdicts(c, MID) == {}  # Frame rounds gave no per-target answers.
    # A re-review after an edit lists only the changed target; the old empty confirmation carries over.
    edit_manual(source, 30)
    again = review.start(MID, source, INFO, 1)
    assert again["targets"] == [1] and again["unknownFrames"] == [[2, 2]]
    again = review.record_playthrough(MID, again["reviewId"], 1, review.load(source, INFO))
    review.pass_review(MID, source, INFO, answer_all(source, again)["reviewId"], 1)
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        assert review.require_passed(c, MID, frames, 3) == {2}


def test_stage_separates_untouched_annotating_and_ready_videos(tmp_path):
    (tmp_path / "workspace_state.json").write_text(json.dumps({"manualAnnotations": []}))
    assert review.stage(tmp_path) == "unstarted"
    (tmp_path / "workspace_state.json").write_text(json.dumps({"manualAnnotations": [{"objectId": 1}]}))
    assert review.stage(tmp_path) == "annotating"
    (tmp_path / "media.json").write_text(json.dumps({"importedFrom": {"exportId": "x"}}))
    assert review.stage(tmp_path) == "ready"  # An imported package arrives finished.
    (tmp_path / "media.json").write_text("{}")
    (tmp_path / "tracker_results.json").write_text("")
    assert review.stage(tmp_path) == "ready"


def test_people_shows_who_passed_and_who_is_reviewing(source):
    dirs = [(MID, source)]
    assert review.people(dirs, {MID: "none"})[MID]["actorId"] == 1
    state = finish(source, answer_all(source, review.start(MID, source, INFO, 2), uid=2), uid=2)
    assert review.people(dirs, {MID: "open"})[MID]["actorName"] == "B"
    review.pass_review(MID, source, INFO, state["reviewId"], 2)
    who = review.people(dirs, {MID: "passed"})[MID]
    assert who["passedBy"] == 2 and who["actorName"] == "B" and who["passedAt"]
