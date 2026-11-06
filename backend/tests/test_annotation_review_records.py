"""The review history behind the records page: rounds, people, what changed, what still stands."""

import json

import pytest
from app import annotation_review as review, annotation_review_records as records, db
from app.review_workflow import ReviewError
from .test_annotation_review import MID, check_all, edit_manual, frames_of
from .test_review_completion import INFO, source
from .test_review_workflow import task

B, C = 2, 3


def known(source):
    """The lookup the route gives: only videos that still exist have an answer."""
    return lambda media_id: (source, "样本.avi") if media_id == MID else None


def only_row(source):
    rows = records.rounds(known(source))
    assert len(rows) == 1
    return rows[0]


def test_a_video_never_reviewed_has_no_record(source):
    assert records.rounds(known(source)) == []


def test_open_round_records_who_started_and_who_confirmed_what(source):
    state = review.start(MID, source, INFO, B)
    row = only_row(source)
    assert row["reviewId"] == state["reviewId"] and row["mediaId"] == MID and row["mediaName"] == "样本.avi"
    assert (row["round"], row["state"], row["standing"]) == (1, "open", None)
    assert row["startedBy"] == {"id": B, "name": "B"} and row["passedBy"] is None and row["passedAt"] is None
    assert (row["checked"], row["total"], row["emptyFrames"]) == (0, 3, 0)
    assert row["reviewers"] == [] and row["changes"] is None
    assert row["lastActivityAt"] == row["startedAt"]

    current = review.load(source, INFO)
    review.check(MID, state["reviewId"], 0, "ok", B, current)
    review.check(MID, state["reviewId"], 1, "ok", C, current)
    review.check(MID, state["reviewId"], 2, "empty", C, current)
    row = only_row(source)
    assert (row["checked"], row["total"], row["emptyFrames"]) == (3, 3, 1)
    assert row["reviewers"] == [{"id": B, "name": "B", "frames": 1}, {"id": C, "name": "C", "frames": 2}]
    assert row["lastActivityAt"] >= row["startedAt"] and row["state"] == "open"


def test_passed_round_records_passer_and_changes(source):
    state = review.start(MID, source, INFO, B)
    edit_manual(source, 30)  # The reviewer corrects a box before confirming.
    state = check_all(source, state, uid=B)
    review.pass_review(MID, source, INFO, state["reviewId"], C)
    row = only_row(source)
    assert (row["state"], row["standing"]) == ("passed", "current")
    assert row["passedBy"] == {"id": C, "name": "C"} and row["passedAt"]
    assert row["changes"] == {"added": 0, "removed": 0, "moved": 1, "frames": 1}
    assert row["lastActivityAt"] == row["passedAt"]


def test_standing_follows_later_edits_and_later_rounds(source):
    first = check_all(source, review.start(MID, source, INFO, B), uid=B)
    review.pass_review(MID, source, INFO, first["reviewId"], B)

    edit_manual(source, 30)
    assert only_row(source)["standing"] == "stale"  # The pass happened; it no longer covers the content.

    second = review.start(MID, source, INFO, C)
    newest, oldest = records.rounds(known(source))
    assert (newest["reviewId"], newest["round"], newest["state"]) == (second["reviewId"], 2, "open")
    assert (newest["checked"], newest["total"]) == (0, 1)  # A re-review lists only what changed.
    assert (oldest["reviewId"], oldest["standing"]) == (first["reviewId"], "superseded")

    second = check_all(source, second, uid=C)
    review.pass_review(MID, source, INFO, second["reviewId"], C)
    newest, oldest = records.rounds(known(source))
    assert (newest["round"], newest["standing"]) == (2, "current")
    assert oldest["standing"] == "superseded" and oldest["changes"] == {"added": 0, "removed": 0, "moved": 0, "frames": 0}


def test_saving_without_a_real_change_is_judged_like_the_media_list(source):
    state = check_all(source, review.start(MID, source, INFO, B), uid=B)
    review.pass_review(MID, source, INFO, state["reviewId"], B)
    workspace = json.loads((source / "workspace_state.json").read_text())
    workspace["revision"] = workspace.get("revision", 0) + 1
    (source / "workspace_state.json").write_text(json.dumps(workspace))
    assert review.summary([(MID, source)]) == {MID: "stale"}
    assert only_row(source)["standing"] == "stale"
    # Opening the video compares content and refreshes the stamp, in the list and here alike.
    assert review.status(MID, source, INFO)["status"] == "passed"
    assert only_row(source)["standing"] == "current"


def test_the_history_of_a_deleted_video_stays_readable(source):
    state = check_all(source, review.start(MID, source, INFO, B), uid=B)
    review.pass_review(MID, source, INFO, state["reviewId"], B)
    row = only_row(source)
    gone = records.rounds(lambda media_id: None)[0]
    assert gone["mediaDeleted"] is True and row["mediaDeleted"] is False
    assert gone["mediaName"] == MID and gone["standing"] is None and gone["passedBy"] == row["passedBy"]
    assert records.detail(state["reviewId"])["confirmations"]


def test_rounds_are_ordered_by_latest_activity_across_videos(source):
    first = check_all(source, review.start(MID, source, INFO, B), uid=B)
    with db.connect() as c:
        c.execute(
            "INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json,evidence_policy) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("arev_later", "other", 1, "open", B, "2999-01-01T00:00:00Z", "r", "[]", "[]", review.CHECKLIST_POLICY),
        )
    rows = records.rounds(known(source))
    assert [r["reviewId"] for r in rows] == ["arev_later", first["reviewId"]]
    assert rows[0]["mediaDeleted"] is True and rows[0]["total"] == 0


def test_a_person_removed_from_the_accounts_is_still_named_by_id(source):
    state = review.start(MID, source, INFO, 99)
    current = review.load(source, INFO)
    review.check(MID, state["reviewId"], 0, "ok", 99, current)
    row = only_row(source)
    assert row["startedBy"] == {"id": 99, "name": "用户 #99"}
    assert row["reviewers"] == [{"id": 99, "name": "用户 #99", "frames": 1}]


def test_detail_shows_each_persons_frames_as_spans(source):
    state = review.start(MID, source, INFO, B)
    current = review.load(source, INFO)
    review.check(MID, state["reviewId"], 0, "ok", B, current)
    review.check(MID, state["reviewId"], 1, "ok", C, current)
    review.check(MID, state["reviewId"], 2, "empty", B, current)
    detail = records.detail(state["reviewId"])
    assert detail["reviewId"] == state["reviewId"] and detail["legacy"] is False and detail["changes"] == []
    by_person = [(c["actor"]["name"], c["result"], c["frames"], c["spans"]) for c in detail["confirmations"]]
    assert by_person == [("B", "ok", 1, [[0, 0]]), ("C", "ok", 1, [[1, 1]]), ("B", "empty", 1, [[2, 2]])]
    assert all(c["lastAt"] for c in detail["confirmations"])


def test_detail_after_pass_names_what_changed_and_who_confirmed_it(source):
    state = review.start(MID, source, INFO, B)
    edit_manual(source, 30)
    state = check_all(source, state, uid=C)
    review.pass_review(MID, source, INFO, state["reviewId"], C)
    assert records.detail(state["reviewId"])["changes"] == [
        {"start": 0, "end": 0, "added": [], "removed": [], "moved": [1], "checkedBy": {"id": C, "name": "C"}},
    ]


def test_change_frames_merge_into_spans_only_when_nothing_differs():
    names = {B: "B", C: "C"}
    frame = lambda i, moved=(1,), by=B: dict(frameIndex=i, added=[], removed=[], moved=list(moved), checkedBy=by)
    spans = records.change_spans([frame(4), frame(5), frame(6, moved=(2,)), frame(7, moved=(2,), by=C), frame(9, moved=(2,), by=C)], names)
    assert [(s["start"], s["end"], s["moved"], s["checkedBy"]["name"]) for s in spans] == [
        (4, 5, [1], "B"), (6, 6, [2], "B"), (7, 7, [2], "C"), (9, 9, [2], "C"),
    ]
    assert records.change_spans([dict(frameIndex=1, added=[3], removed=[], moved=[], checkedBy=None)], names)[0]["checkedBy"] is None


def test_detail_of_an_unknown_round_is_not_found(source):
    with pytest.raises(ReviewError) as e:
        records.detail("arev_missing")
    assert (e.value.code, e.value.status) == ("REVIEW_NOT_FOUND", 404)


def test_a_round_from_the_old_checklist_is_shown_without_frame_numbers(source):
    items = [{"id": "s1", "kind": "sample", "startFrame": 0, "endFrame": 0}, {"id": "u1", "kind": "unknown", "startFrame": 2, "endFrame": 2}]
    changes = {"added": 1, "removed": 0, "moved": 0, "frames": [], "reviewers": [B]}
    with db.connect() as c:
        c.execute(
            "INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json,evidence_policy,passed_by,passed_at,passed_revision,changes_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("arev_old", MID, 1, "passed", B, "2026-01-01T00:00:00Z", "r", "[]", json.dumps(items), "actionable-review-v7-old", B, "2026-01-02T00:00:00Z", "r", json.dumps(changes)),
        )
        c.execute("INSERT INTO annotation_review_checks(review_id,item_id,result,actor_id,checked_at) VALUES ('arev_old','s1','ok',?,'2026-01-01T01:00:00Z')", (B,))
        c.execute("INSERT INTO annotation_review_checks(review_id,item_id,result,actor_id,checked_at) VALUES ('arev_old','u1','empty',?,'2026-01-01T02:00:00Z')", (C,))
    row = only_row(source)
    assert row["legacy"] is True and (row["checked"], row["total"], row["emptyFrames"]) == (2, 2, 1)
    assert row["changes"] == {"added": 1, "removed": 0, "moved": 0, "frames": 0}
    detail = records.detail("arev_old")
    assert detail["legacy"] is True and all(c["spans"] == [] for c in detail["confirmations"])
    assert {(c["actor"]["name"], c["result"], c["frames"]) for c in detail["confirmations"]} == {("B", "ok", 1), ("C", "empty", 1)}


def test_listing_never_parses_tracking_results(source, monkeypatch):
    state = check_all(source, review.start(MID, source, INFO, B), uid=B)
    review.pass_review(MID, source, INFO, state["reviewId"], B)
    (source / "tracker_results.json").write_text("this is no longer readable tracking output")
    monkeypatch.setattr(review, "load", lambda *a, **k: pytest.fail("the list must not parse tracking results"))
    assert only_row(source)["state"] == "passed"


def test_listing_leaves_the_frame_snapshots_in_the_database(source, monkeypatch):
    # They can reach megabytes per round; one real page of rounds must not hold them all in memory.
    assert "frames_json" not in records.LIST_COLUMNS and "*" not in records.LIST_COLUMNS


def test_one_unreadable_workspace_does_not_hide_the_whole_history(source):
    state = check_all(source, review.start(MID, source, INFO, B), uid=B)
    review.pass_review(MID, source, INFO, state["reviewId"], B)
    (source / "workspace_state.json").write_text("{not json")
    assert only_row(source)["standing"] == "stale"


def test_records_api_requires_login_and_names_the_video(source, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import annotation_review_routes as routes, main
    from app.auth import sign_jwt

    monkeypatch.setattr(main, "media_dir", lambda media_id: source if media_id == MID else source / "missing")
    (source / "media.json").write_text(json.dumps({"videoName": "video.avi"}))
    state = check_all(source, review.start(MID, source, INFO, B), uid=B)
    review.pass_review(MID, source, INFO, state["reviewId"], B)
    with db.connect() as c:
        c.execute(
            "INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json,evidence_policy) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("arev_gone", "gone-media", 1, "open", B, "2026-01-01T00:00:00Z", "r", "[]", "[]", review.CHECKLIST_POLICY),
        )
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    assert client.get("/api/annotation-review/records").status_code == 401
    assert client.get(f"/api/annotation-review/records/{state['reviewId']}").status_code == 401
    client.headers["Authorization"] = "Bearer " + sign_jwt({"uid": 1})
    data = client.get("/api/annotation-review/records").json()
    assert data["total"] == 2
    by_media = {row["mediaId"]: row for row in data["items"]}
    assert by_media[MID]["mediaName"] == "video.avi" and by_media[MID]["standing"] == "current"
    assert by_media["gone-media"]["mediaDeleted"] is True
    response = client.get(f"/api/annotation-review/records/{state['reviewId']}")
    assert response.status_code == 200 and response.json()["confirmations"][0]["actor"]["name"] == "B"
    missing = client.get("/api/annotation-review/records/arev_missing")
    assert missing.status_code == 404 and missing.json()["code"] == "REVIEW_NOT_FOUND"
