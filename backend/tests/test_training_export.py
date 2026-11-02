"""Real-video package checks and workflow bypass/failure regression tests."""

import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml
from app import confirmation_workflow as confirm
from app import db, main
from app import review_workflow as review
from app import training_export as export
from app.auth import sign_jwt
from app.review_repository import (
    compute_file_sha256,
    create_review_session,
    freeze_baseline,
    upsert_media_revision,
)
from app.training_export_routes import legacy_router, router
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .test_confirmation_workflow import confirmation as confirmation
from .test_confirmation_workflow import finish, write
from .test_review_workflow import task as task


def video_file(directory, w=640, h=480, n=3, seed=50):
    directory.mkdir(parents=True, exist_ok=True)
    video = directory / "original.avi"
    out = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 30, (w, h))
    for i in range(n):
        out.write(np.full((h, w, 3), seed + 30 * i, dtype=np.uint8))
    out.release()
    (directory / "media.json").write_text(json.dumps({"videoName": video.name}))
    return video


@pytest.fixture
def frozen(confirmation, tmp_path, monkeypatch):
    monkeypatch.setattr(export, "DATASET_EXPORT_DIR", tmp_path / "datasets")
    monkeypatch.setattr(main, "TRACK_DATA_DIR", tmp_path / "media")
    monkeypatch.setattr(main, "LEGACY_TRACK_DATA_DIR", tmp_path / "legacy")
    monkeypatch.setattr(export, "enqueue", lambda eid: None)
    video = video_file(main.TRACK_DATA_DIR / "test-video")
    s = confirm.get_session(confirmation, 3)
    with db.connect() as c:
        export.migrate(c)
        c.execute(
            "UPDATE media_revisions SET sha256=?,file_size=?",
            (compute_file_sha256(video), video.stat().st_size),
        )
        for table, col, key in [
            ("baseline_frames", "baseline_id", s["baselineId"]),
            ("review_version_frames", "version_id", s["reviewVersionId"]),
        ]:
            for fi in [0, 1]:
                row = c.execute(
                    f"SELECT objects_json FROM {table} WHERE {col}=? AND frame_index=?",
                    (key, fi),
                ).fetchone()
                objs = json.loads(row[0])
                if fi == 1:
                    objs[0]["classKey"] = "CD4"
                else:
                    objs.append(
                        {
                            "objectId": 2,
                            "annotationId": "stable-unchanged",
                            "bbox": [100.125, 110.25, 130.5, 140.625],
                            "classKey": "rare: 'sperm' #α",
                        }
                    )
                c.execute(
                    f"UPDATE {table} SET objects_json=? WHERE {col}=? AND frame_index=?",
                    (json.dumps(objs), key, fi),
                )
    final = finish(confirmation)["session"]["finalVersionId"]
    return {"cid": confirmation, "vid": final, "video": video, "root": tmp_path}


def create(frozen, fmt="yolo", key=None, ids=None):
    return export.create(
        3,
        key or uuid.uuid4().hex,
        {"finalVersionIds": ids or [frozen["vid"]], "format": fmt, "splitRatio": 0.8},
    )


def ready(frozen, fmt="yolo", ids=None):
    job = create(frozen, fmt, ids=ids)
    export.run(job["exportId"])
    state = export.status(job["exportId"], 3)
    assert state["state"] == "ready", state
    return state


@pytest.mark.parametrize("fmt", ["yolo", "coco", "both"])
def test_zip_contains_real_frames_and_final_labels(frozen, fmt):
    job = ready(frozen, fmt)
    with zipfile.ZipFile(export.download(job["exportId"], 3)) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json"))
        images = [x for x in names if x.endswith(".jpg")]
        samples = {s["frameIndex"]: s for s in manifest["samples"]}
        assert (
            len(images) == 3
            and manifest["counts"]["train"] == 2
            and manifest["counts"]["val"] == 1
        )
        assert (
            manifest["counts"]["emptyFrames"] == 1
            and manifest["counts"]["objectCount"] == 3
        )
        assert len(set(images)) == 3
        decoded = cv2.imdecode(
            np.frombuffer(z.read(samples[1]["image"]), np.uint8),
            cv2.IMREAD_COLOR,
        )
        assert decoded.shape[:2] == (480, 640) and 75 <= decoded.mean() <= 85
        assert (
            len(json.loads(z.read("provenance/" + frozen["vid"] + ".json"))["frames"])
            == 3
        )
        if fmt != "coco":
            config = yaml.safe_load(z.read("data.yaml"))
            assert "path" not in config
            assert config["train"] == "images/train" and config["val"] == "images/val"
            assert config["names"] == {0: "CD4", 1: "rare: 'sperm' #α", 2: "sperm"}
            assert z.read(samples[2]["label"]) == b""
            row = list(map(float, z.read(samples[1]["label"]).split()))
            assert row == pytest.approx(
                [0, 23.333 / 640, 30 / 480, 20 / 640, 20 / 480], abs=1e-10
            )
            lines = z.read(samples[0]["label"]).decode().splitlines()
            assert list(map(float, lines[0].split())) == pytest.approx(
                [2, 20 / 640, 30 / 480, 20 / 640, 20 / 480], abs=1e-10
            )
            assert (
                len(lines) == 2
            )  # includes unchanged object, preserving subpixel geometry
            assert len(
                [x for x in names if x.startswith("labels/") and x.endswith(".txt")]
            ) == len(images)
        else:
            assert "data.yaml" not in names
        if fmt != "yolo":
            co = json.loads(z.read("annotations/instances_train.json"))
            val = json.loads(z.read("annotations/instances_val.json"))
            co["images"] += val["images"]
            co["annotations"] += val["annotations"]
            co["annotations"].sort(key=lambda obj: obj["id"])
            assert len(co["images"]) == 3 and len(co["annotations"]) == 3
            assert co["annotations"][2]["bbox"] == pytest.approx([13.333, 20, 20, 20])
            for item in co["images"]:
                assert (
                    (Path("annotations") / item["file_name"]).as_posix().replace(
                        "annotations/../", ""
                    )
                    in names
                )
        else:
            assert "annotations/instances_train.json" not in names
    assert not list((export.DATASET_EXPORT_DIR / ".work").iterdir())


def test_current_head_and_completed_review_are_required(frozen):
    cid = frozen["cid"]
    job = ready(frozen)
    write(cid, "reopen")
    for action in [
        lambda: create(frozen),
        lambda: export.preview([frozen["vid"]]),
        lambda: export.download(job["exportId"], 3),
    ]:
        with pytest.raises(review.ReviewError) as e:
            action()
        assert e.value.code == "CONFIRMATION_REQUIRED"
    assert export.status(job["exportId"], 3)["state"] == "invalidated"
    new = write(cid, "finish")["session"]["finalVersionId"]
    with pytest.raises(review.ReviewError):
        create(frozen)
    assert export.preview([new])["frameCount"] == 3
    with db.connect() as c:
        c.execute("UPDATE review_sessions SET state='in_progress'")
    with pytest.raises(review.ReviewError) as e:
        export.preview([new])
    assert e.value.code == "REVIEW_REQUIRED"


def test_unconfirmed_changes_and_incomplete_snapshots_cannot_export(frozen):
    with db.connect() as c:
        c.execute(
            "DELETE FROM decision_heads WHERE confirmation_id=?", (frozen["cid"],)
        )
    with pytest.raises(review.ReviewError) as e:
        create(frozen)
    assert e.value.code == "UNDECIDED_CHANGES"


def test_snapshot_hash_detects_tampered_coordinates(frozen):
    with db.connect() as c:
        c.execute(
            "UPDATE final_version_frames SET objects_json='[]' WHERE frame_index=0"
        )
    with pytest.raises(review.ReviewError) as e:
        create(frozen)
    assert e.value.code == "FINAL_SNAPSHOT_CHANGED"


def test_migration_does_not_accept_legacy_final_without_full_frames(frozen):
    with db.connect() as c:
        c.execute("DELETE FROM final_version_frames WHERE frame_index=2")
    with pytest.raises(review.ReviewError) as e:
        create(frozen)
    assert e.value.code == "INCOMPLETE_FINAL_VERSION"


def test_idempotent_create_queues_once_and_rejects_changed_settings(
    frozen, monkeypatch
):
    enqueued = []
    monkeypatch.setattr(export, "enqueue", enqueued.append)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: create(frozen, key="lost-response"), range(2))
        )
    assert results[0]["exportId"] == results[1]["exportId"] and len(enqueued) == 1
    with pytest.raises(review.ReviewError) as e:
        create(frozen, "both", key="lost-response")
    assert e.value.code == "IDEMPOTENCY_KEY_REUSED"
    export.run(results[0]["exportId"])
    assert create(frozen, key="lost-response")["state"] == "ready"


@pytest.mark.parametrize(
    "failure",
    ["missing", "hash", "decode", "dimensions", "image_write", "reopen", "publish"],
)
def test_export_fails_without_publishing_partial_training_zip(
    frozen, monkeypatch, failure, caplog
):
    job = create(frozen)
    eid = job["exportId"]
    if failure == "missing":
        frozen["video"].unlink()
    if failure == "hash":
        frozen["video"].write_bytes(b"changed video")
    if failure in ("decode", "dimensions"):
        real = cv2.VideoCapture

        class BrokenCapture:
            def __init__(self, path):
                self.cap = real(path)
                self.n = 0

            def isOpened(self):
                return self.cap.isOpened()

            def read(self):
                self.n += 1
                if failure == "decode" and self.n == 2:
                    return False, None
                ok, img = self.cap.read()
                return ok, img[:400] if failure == "dimensions" else img

            def release(self):
                self.cap.release()

        monkeypatch.setattr(cv2, "VideoCapture", BrokenCapture)
    if failure == "image_write":
        monkeypatch.setattr(cv2, "imencode", lambda *a, **kw: (False, None))
    if failure == "reopen":
        original = cv2.imencode
        called = False

        def save(*args, **kwargs):
            nonlocal called
            if not called:
                called = True
                write(frozen["cid"], "reopen")
            return original(*args, **kwargs)

        monkeypatch.setattr(cv2, "imencode", save)
    if failure == "publish":
        with db.connect() as c:
            c.execute(
                "CREATE TRIGGER publish_fail BEFORE UPDATE ON training_exports WHEN NEW.state='ready' BEGIN SELECT RAISE(ABORT,'injected publication failure'); END"
            )
    export.run(eid)
    result = export.status(eid, 3)
    assert result["state"] in ("failed", "invalidated") and result["errorCode"]
    assert "dataset.failed" in caplog.text
    assert not (export.DATASET_EXPORT_DIR / (eid + ".zip")).exists()
    assert not (export.DATASET_EXPORT_DIR / (eid + ".zip.partial")).exists()
    assert not (export.DATASET_EXPORT_DIR / ".work" / eid).exists()
    with pytest.raises(review.ReviewError):
        export.download(eid, 3)


def test_an_already_claimed_job_cannot_remove_another_workers_files(frozen):
    job = create(frozen)
    eid = job["exportId"]
    work = export.DATASET_EXPORT_DIR / ".work" / eid
    work.mkdir(parents=True)
    (work / "in-progress.txt").write_text("active")
    with db.connect() as c:
        c.execute("UPDATE training_exports SET state='running' WHERE id=?", (eid,))
    export.run(eid)
    assert (work / "in-progress.txt").exists()


def test_restart_marks_interrupted_job_failed(frozen):
    job = create(frozen)
    export.recover_interrupted()
    assert export.status(job["exportId"], 3)["errorCode"] == "EXPORT_INTERRUPTED"


def test_source_metadata_and_raw_workspace_do_not_override_final(frozen):
    (frozen["video"].parent / "workspace_state.json").write_text(
        '{"manualAnnotations":[]}'
    )
    (frozen["video"].parent / "tracker_results.json").write_text(
        "invalid mutable tracking data"
    )
    assert ready(frozen)["manifest"]["counts"]["objectCount"] == 3


def confirmed_video(name, seed=80, n=2):
    """A second fully reviewed and confirmed video with one sperm box per frame."""
    directory = main.TRACK_DATA_DIR / name
    video = video_file(directory, w=120, h=90, n=n, seed=seed)
    media = upsert_media_revision(
        name, compute_file_sha256(video), video.stat().st_size, 120, 90, 30, n
    )
    frames = [
        {
            "frameIndex": i,
            "coverage": "objects",
            "objects": [{"objectId": 1, "bbox": [1, 2, 11, 12], "classKey": "sperm"}],
        }
        for i in range(n)
    ]
    a = freeze_baseline(media, name, 1, frames)
    sid = create_review_session(a["id"])["id"]
    review.write("claim", sid, 2, name + "-claim", {})
    for fi in range(n):
        review.write(
            "submit", sid, 2, name + str(fi), {"expectedFrameRevision": 0, "patch": []}, fi
        )
    r = review.write(
        "finish",
        sid,
        2,
        "finish-" + name,
        {"expectedSessionRevision": review.get_session(sid, 2)["revision"]},
    )
    cid = r["confirmationSessionId"]
    write(cid, "claim")
    return write(cid, "finish")["session"]["finalVersionId"]


def test_multiple_video_versions_are_all_exported_without_filename_collisions(frozen):
    vid = confirmed_video("second")
    job = ready(frozen, ids=[frozen["vid"], vid])
    assert (
        job["manifest"]["counts"]["frameCount"] == 5
        and len(job["manifest"]["sources"]) == 2
    )
    with zipfile.ZipFile(export.download(job["exportId"], 3)) as z:
        assert len([x for x in z.namelist() if x.endswith(".jpg")]) == 5
        row = list(map(float, z.read(next(s["label"] for s in job["manifest"]["samples"] if s["finalVersionId"] == vid and s["frameIndex"] == 1)).split()))
        assert row[1:] == pytest.approx([6 / 120, 7 / 90, 10 / 120, 10 / 90], abs=1e-10)


def test_split_has_no_overlap_and_moves_positive_sample_into_training():
    versions = [
        {
            "frames": [
                {"frameIndex": i, "objects": [1] if i == 4 else []} for i in range(5)
            ]
        }
    ]
    splits = export.split_samples(versions, 0.8)
    assert (
        len(splits) == 5
        and splits[0, 4] == "train"
        and list(splits.values()).count("val") == 1
    )


def test_authenticated_routes_block_old_export_and_annotation_payloads(frozen):
    app = FastAPI()
    app.include_router(router)
    app.include_router(legacy_router)
    client = TestClient(app)
    assert client.post("/api/export/dataset", json={}).status_code == 401
    client.headers["Authorization"] = "Bearer " + sign_jwt({"uid": 3})
    legacy = client.post(
        "/api/export/dataset", json={"annotations": [{"bbox": [0, 0, 10, 10]}]}
    )
    assert (
        legacy.status_code == 410
        and legacy.json()["code"] == "FINAL_CONFIRMATION_REQUIRED"
    )
    url = "/api/datasets/exports"
    body = {"finalVersionIds": [frozen["vid"]], "format": "yolo", "splitRatio": 0.8}
    assert client.post(url, json=body).status_code == 428
    client.headers.update({"X-Review-Contract": "2", "Idempotency-Key": "api-create"})
    for patch in [
        {"annotations": []},
        {"mediaId": "raw-video"},
        {"format": "invalid"},
        {"splitRatio": 0},
        {"splitRatio": 1},
        {"splitRatio": True},
    ]:
        assert client.post(url, json={**body, **patch}).status_code == 422
    created = client.post(url, json=body)
    assert created.status_code == 200
    eid = created.json()["exportId"]
    assert client.get(url + "/" + eid + "/download").status_code == 409
    export.run(eid)
    download = client.get(url + "/" + eid + "/download")
    assert (
        download.status_code == 200
        and download.headers["content-type"] == "application/zip"
    )
    assert download.headers["X-Request-ID"]
    assert client.get(url + "/old-ungated-package/download").status_code == 404
    client.headers["Authorization"] = "Bearer " + sign_jwt({"uid": 1})
    assert (
        client.get(url + "/" + eid).status_code == 404
        and client.get(url + "/" + eid + "/download").status_code == 404
    )
    client.headers.pop("Authorization")
    assert client.get(url + "/" + eid + "/download").status_code == 401


def test_split_keeps_validation_positive_when_two_positive_frames_exist():
    versions = [
        {
            "frames": [
                {"frameIndex": i, "objects": [1] if i < 2 else []} for i in range(5)
            ]
        }
    ]
    splits = export.split_samples(versions, 0.8)
    assert splits[0, 0] != splits[0, 1]
    assert sum(s == "train" for s in splits.values()) == 4


def fake_versions(*videos):
    """(sha256, frame positives) pairs as the split functions see them."""
    return [
        {
            "final": {"id": f"v{i}"},
            "media": {"sha256": sha},
            "frames": [{"frameIndex": fi, "objects": [1] if p else []} for fi, p in enumerate(frames)],
        }
        for i, (sha, frames) in enumerate(videos)
    ]


def video_settings(**patch):
    return {"splitRatio": 0.7, "splitMode": "video", "testRatio": 0.15, **patch}


def test_video_split_keeps_whole_videos_together_and_is_stable():
    shas = [f"{i:02x}" * 32 for i in range(0, 256, 9)]
    versions = fake_versions(*[(sha, [True, False, True]) for sha in shas])
    assignments, chosen = export.assign_splits(versions, video_settings())
    for vi, v in enumerate(versions):
        assert {assignments[vi, f["frameIndex"]] for f in v["frames"]} == {chosen[vi]}
    assert set(chosen) == {"train", "val", "test"}
    # A video's split depends only on its own content, not on which other videos are exported.
    alone = [export.assign_splits(fake_versions((sha, [True])), video_settings())[1][0] for sha in shas]
    assert alone == chosen
    assert export.video_bucket("00" * 32) == 0 and export.video_bucket("ff" * 32) < 1


def test_video_split_moves_positive_videos_and_respects_manual_choice():
    # Both hash into validation (bucket ~0.8); training still needs a positive video.
    versions = fake_versions(("cc" * 32, [True]), ("cd" * 32, [True]))
    chosen = export.split_videos(versions, export.split_settings(video_settings()))
    assert sorted(chosen) == ["train", "val"]
    # Both hash into training; one positive video is moved to validation.
    versions = fake_versions(("00" * 32, [True]), ("01" * 32, [True]), ("02" * 32, [False]))
    assert export.split_videos(versions, export.split_settings(video_settings())).count("val") == 1
    versions = fake_versions(("00" * 32, [True]), ("01" * 32, [True]), ("02" * 32, [True]))
    manual = export.split_settings(video_settings(videoSplits={"v0": "test", "v1": "val"}))
    assert export.split_videos(versions, manual) == ["test", "val", "train"]
    with pytest.raises(review.ReviewError) as e:
        export.split_videos(
            fake_versions(("00" * 32, [True]), ("01" * 32, [False])),
            export.split_settings(video_settings(videoSplits={"v0": "val"})),
        )
    assert e.value.code == "SPLIT_WITHOUT_TRAINING_TARGETS"
    # A single video is a fragment for later merging: it goes whole to its own split.
    one = fake_versions(("ff" * 32, [True]))
    assert export.split_videos(one, export.split_settings(video_settings())) == ["test"]


def test_video_level_package_with_source_videos(frozen):
    vid = confirmed_video("second")
    third = confirmed_video("third", seed=120, n=3)
    ids = [frozen["vid"], vid, third]
    plan = export.preview(ids, {**video_settings(), "finalVersionIds": ids})
    assert [v["finalVersionId"] for v in plan["videos"]] == ids
    assert sum(plan["splitFrames"].values()) == plan["frameCount"] == 8
    body = {
        "finalVersionIds": ids,
        "format": "both",
        **video_settings(videoSplits={third: "test"}),
        "includeSourceVideos": True,
    }
    job = export.create(3, uuid.uuid4().hex, body)
    export.run(job["exportId"])
    job = export.status(job["exportId"], 3)
    assert job["state"] == "ready", job
    manifest = job["manifest"]
    assert manifest["splitStrategy"].startswith("video-level")
    by_video = {}
    for sample in manifest["samples"]:
        by_video.setdefault(sample["finalVersionId"], set()).add(sample["split"])
    assert all(len(splits) == 1 for splits in by_video.values())
    sources = {s["finalVersionId"]: s for s in manifest["sources"]}
    assert sources[third]["split"] == "test" and by_video[third] == {"test"}
    assert {s["split"] for s in sources.values()} == {"train", "val", "test"}
    with zipfile.ZipFile(export.download(job["exportId"], 3)) as z:
        names = z.namelist()
        assert yaml.safe_load(z.read("data.yaml"))["test"] == "images/test"
        assert "annotations/instances_test.json" in names
        for s in sources.values():
            info = z.getinfo(s["videoFile"])
            assert info.compress_type == zipfile.ZIP_STORED
            import hashlib
            assert hashlib.sha256(z.read(s["videoFile"])).hexdigest() == s["sourceSha256"]
            assert s["videoFile"].endswith("original.avi")


def test_frame_mode_has_no_test_split_or_video_files(frozen):
    job = ready(frozen)
    assert job["manifest"]["counts"]["test"] == 0
    assert all(s["split"] is None and s["videoFile"] is None for s in job["manifest"]["sources"])
    with zipfile.ZipFile(export.download(job["exportId"], 3)) as z:
        assert "test" not in yaml.safe_load(z.read("data.yaml"))
        assert not [n for n in z.namelist() if n.startswith("videos/")]


def test_split_options_are_validated_and_legacy_requests_keep_their_hash(frozen):
    from app import training_export_routes as routes
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    client.headers.update({
        "Authorization": "Bearer " + sign_jwt({"uid": 3}),
        "X-Review-Contract": "2",
        "Idempotency-Key": "split-options",
    })
    body = {"finalVersionIds": [frozen["vid"]], "format": "yolo", "splitRatio": 0.8}
    for patch in [
        {"splitMode": "video", "testRatio": 0.2},
        {"splitMode": "video", "testRatio": -0.1},
        {"splitMode": "frame", "testRatio": 0.1},
        {"splitMode": "video", "videoSplits": {frozen["vid"]: "holdout"}},
        {"includeSourceVideos": "yes"},
    ]:
        assert client.post("/api/datasets/exports", json={**body, **patch}).status_code == 422
    unknown = {**body, "splitMode": "video", "videoSplits": {"not-selected": "val"}}
    response = client.post("/api/datasets/exports", json=unknown)
    assert response.status_code == 422 and response.json()["code"] == "INVALID_VIDEO_SPLIT"
    assert routes.settings(routes.Export(**body)) == {**body}
    preview = client.post("/api/datasets/exports/preview", json={"finalVersionIds": body["finalVersionIds"], "splitRatio": 0.8, "splitMode": "video", "testRatio": 0.1})
    assert preview.status_code == 200 and preview.json()["splitMode"] == "video", preview.json()
    assert preview.json()["videos"][0]["split"] in {"train", "val", "test"}


def merge_script():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "scripts" / "merge_training_exports.py"
    spec = importlib.util.spec_from_file_location("merge_training_exports", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_merge_script_resplits_old_frame_packages_by_video(frozen, tmp_path):
    merge = merge_script()
    for sha in ["00" * 32, "7f" * 32, "a1b2c3d4e5f6" * 5 + "0000"]:
        assert merge.video_bucket(sha) == export.video_bucket(sha)
    second = confirmed_video("second")
    first_zip = export.download(ready(frozen)["exportId"], 3)
    second_zip = export.download(ready(frozen, ids=[second])["exportId"], 3)
    out = tmp_path / "merged"
    counts = merge.main([str(first_zip), str(second_zip), "--output", str(out), "--assign", "second=val"])
    assert sum(c["frames"] for c in counts.values()) == 5 and counts["val"]["videos"] == 1
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest['reviewDecisions']['count']==0 and not (out/'review/decisions.jsonl').exists()
    by_split = {s["split"]: s for s in manifest["sources"]}
    assert set(by_split) == {"train", "val"} and by_split["val"]["mediaId"] == "second"
    config = yaml.safe_load((out / "data.yaml").read_text())
    assert config["names"] == {0: "CD4", 1: "rare: 'sperm' #α", 2: "sperm"}
    val_labels = sorted((out / "labels" / "val").glob("*.txt"))
    assert len(val_labels) == 2 and all(p.read_text().startswith("2 ") for p in val_labels)
    assert len(list((out / "images" / "train").glob("*.jpg"))) == 3
    with pytest.raises(SystemExit):
        merge.main([str(first_zip), "--output", str(out)])  # Output must be empty.


@pytest.mark.parametrize('newer_first',[False,True])
def test_merge_review_decisions_follow_selected_video_without_duplicates(tmp_path,newer_first):
    merge=merge_script()
    sha_a,sha_b='01'*32,'aa'*32
    old_a=json.dumps(dict(sourceSha256=sha_a,frameIndex=0,bbox=[1,1,3,3],category='missing',source='reverse',result='fixed',reviewId='old-a',itemId='a'))
    old_b=json.dumps(dict(sourceSha256=sha_b,frameIndex=0,bbox=[2,2,4,4],category='static',source='detector',result='not_target',reviewId='old-b',itemId='b'))
    new_a=json.dumps(dict(sourceSha256=sha_a,frameIndex=0,bbox=[3,3,5,5],category='missing',source='reverse',result='fixed',reviewId='new-a',itemId='a2'))

    def package(name,created,videos,decisions):
        root=tmp_path/name;root.mkdir()
        (root/'data.yaml').write_text('names: [sperm]\n')
        sources=[];samples=[]
        for mid,sha in videos:
            image=f'{name}-{mid}.jpg';label=f'{name}-{mid}.txt';version=f'{name}-{mid}'
            ok,jpeg=cv2.imencode('.jpg',np.zeros((8,8,3),dtype=np.uint8));assert ok
            (root/image).write_bytes(jpeg.tobytes());(root/label).write_text('0 0.5 0.5 0.25 0.25\n')
            sources.append(dict(mediaId=mid,sourceSha256=sha,finalVersionId=version))
            samples.append(dict(image=image,label=label,finalVersionId=version,objectCount=1))
        (root/'review').mkdir();(root/'review/decisions.jsonl').write_text('\n'.join(decisions)+'\n')
        (root/'manifest.json').write_text(json.dumps(dict(exportId=name,createdAt=created,sources=sources,samples=samples,reviewDecisions=dict(file='review/decisions.jsonl',count=len(decisions)))))
        return root

    mixed=package('mixed','2026-11-01T00:00:00Z',[('a',sha_a),('b',sha_b)],[old_a,old_b])
    latest=package('latest','2026-11-01T00:01:00Z',[('a',sha_a)],[new_a])
    out=tmp_path/'merged'
    inputs=[latest,mixed] if newer_first else [mixed,latest]
    merge.main([*(str(p) for p in inputs),'--output',str(out),'--assign','a=train','--assign','b=val'])
    lines=(out/'review/decisions.jsonl').read_text().splitlines()
    assert len(lines)==2 and set(lines)=={new_a,old_b}
    manifest=json.loads((out/'manifest.json').read_text())
    assert manifest['reviewDecisions']==dict(file='review/decisions.jsonl',count=2)
    assert len(list((out/'images').glob('*/*.jpg')))==2
    (mixed/'review/decisions.jsonl').unlink()
    with pytest.raises(SystemExit,match='判断文件缺失'):
        merge.main([str(mixed),'--output',str(tmp_path/'missing-decisions')])
    (mixed/'review/decisions.jsonl').write_text(old_b+'\n')
    with pytest.raises(SystemExit,match='行数与清单不一致'):
        merge.main([str(mixed),'--output',str(tmp_path/'incomplete-decisions')])


def train_eval_script():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "scripts" / "detector_train_eval.py"
    spec = importlib.util.spec_from_file_location("detector_train_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_train_eval_pins_benchmark_videos_to_test_and_records_the_comparison(frozen, tmp_path, monkeypatch):
    script = train_eval_script()
    second = confirmed_video("second")
    first_zip = export.download(ready(frozen)["exportId"], 3)
    second_zip = export.download(ready(frozen, ids=[second])["exportId"], 3)
    with zipfile.ZipFile(second_zip) as z:
        benchmark_sha = json.loads(z.read("manifest.json"))["sources"][0]["sourceSha256"]
    bench = tmp_path / "bench.txt"
    bench.write_text(f"# fixed test videos\n{benchmark_sha}\n")
    baseline = tmp_path / "baseline.pt"; baseline.write_bytes(b"baseline")
    out = tmp_path / "dry"
    record = script.main([str(first_zip), str(second_zip), "--benchmark", str(bench), "--output", str(out), "--baseline", str(baseline), "--dry-run"])
    assert record["splits"]["test"]["videos"] == 1 and record["splits"]["test"]["frames"] == 2
    assert record["splits"]["train"]["videos"] + record["splits"]["val"]["videos"] == 1
    merged = json.loads((out / "dataset" / "manifest.json").read_text())
    assert {s["sourceSha256"] for s in merged["sources"] if s["split"] == "test"} == {benchmark_sha}
    assert "verdict" not in record and json.loads((out / "model-record.json").read_text())["dryRun"] is True

    # Training and evaluation are stubbed: this checks orchestration, not a real model.
    def fake_train(init, data, output, epochs, imgsz, device):
        best = output / "train" / "weights" / "best.pt"
        best.parent.mkdir(parents=True); best.write_bytes(b"candidate")
        return best
    scores = {"eval-baseline": dict(mAP50=.5, mAP50_95=.30, precision=.6, recall=.70),
              "eval-candidate": dict(mAP50=.6, mAP50_95=.36, precision=.7, recall=.72)}
    monkeypatch.setattr(script, "train", fake_train)
    monkeypatch.setattr(script, "evaluate", lambda weights, data, output, name, imgsz, device: scores[name])
    record = script.main([str(first_zip), str(second_zip), "--benchmark", str(bench), "--output", str(tmp_path / "run"), "--baseline", str(baseline)])
    assert record["verdict"] == "better" and record["candidate"]["sha256"] == script.sha256(tmp_path / "run" / "train" / "weights" / "best.pt")
    assert record["baseline"]["sha256"] == script.sha256(baseline)
    assert script.verdict(scores["eval-baseline"], dict(scores["eval-candidate"], recall=.6)) == "not_better"
    with pytest.raises(SystemExit):
        bad = tmp_path / "bad.txt"; bad.write_text("not-a-sha\n")
        script.read_benchmark(bad)
