"""Real upload duplicates, destructive reset guards and storage recovery."""
import json
from pathlib import Path
import uuid

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app import db, main, annotation_state, media_reimport, review_workflow as review
from app.auth import sign_jwt
from .test_review_workflow import task
from .test_review_completion import source, finish as submit_a


@pytest.fixture
def imports(task, tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'TRACK_DATA_DIR', tmp_path / 'media')
    monkeypatch.setattr(main, 'LEGACY_TRACK_DATA_DIR', tmp_path / 'legacy')
    monkeypatch.setattr(main, 'TASKS', {})
    video = tmp_path / 'fixture.avi'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 30, (64, 48))
    for fi in range(3): writer.write(np.full((48, 64, 3), 40 + fi, dtype=np.uint8))
    writer.release()
    client = TestClient(main.app)
    client.headers.update({'Authorization': 'Bearer ' + sign_jwt({'uid': 1}), 'X-Review-Contract': '2'})
    return client, video.read_bytes()


def upload(client, content, name='same.avi'):
    return client.post('/api/track/upload', files={'file': (name, content, 'video/x-msvideo')})


def seed_workspace(client, mid):
    state = {'manualAnnotations': [{'objectId': 1, 'source': 'manual', 'frameIndex': 0, 'bbox': {'x': 10, 'y': 10, 'width': 10, 'height': 10}}], 'manualBaselines': [], 'expectedRevision': 0}
    result = client.put('/api/track/workspace/'+mid, json=state, headers={'Idempotency-Key': 'initial-workspace'})
    assert result.status_code == 200
    directory = main.media_dir(mid)
    (directory / 'tracker_results.json').write_text(json.dumps({'frame_index': 0, 'objects': [{'object_id': 1, 'bbox': [2, 2, 8, 8]}]}) + '\n')
    (directory / 'annotations_frame_000000.json').write_text('{}')
    (directory / 'tracker_overlay.mp4').write_bytes(b'overlay')
    return directory


def reset(client, mid, revision=1, key='reset-request'):
    return client.post('/api/annotation-review/media/'+mid+'/reset-annotations', json={'expectedRevision': revision, 'confirmDiscard': True}, headers={'Idempotency-Key': key})


def test_identical_bytes_renamed_are_duplicate_and_cancel_does_not_create_media(imports):
    client, content = imports
    first = upload(client, content).json(); mid = first['mediaId']
    directory = seed_workspace(client, mid)
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    result = upload(client, content, 'renamed.avi')
    assert result.status_code == 409
    assert result.json()['code'] == 'DUPLICATE_VIDEO'
    assert result.json()['duplicate']['mediaId'] == mid
    assert result.json()['duplicate']['canOverwrite']
    assert result.json()['duplicate']['workspaceRevision'] == 1
    assert [p.name for p in main.iter_media_dirs()] == [mid]
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == before
    assert not list(main.TRACK_DATA_DIR.glob('_upload-*'))


def test_same_filename_different_content_is_not_duplicate(imports):
    client, content = imports
    first = upload(client, content).json()
    # Appending bytes leaves video decodable while changing its exact identity.
    second = upload(client, content+b'distinct-video-content')
    assert second.status_code == 201
    assert second.json()['mediaId'] != first['mediaId']


def test_overwrite_clears_all_active_annotation_files_and_db_and_replays(imports):
    client, content = imports
    mid = upload(client, content).json()['mediaId']
    directory = seed_workspace(client, mid)
    with db.connect() as c:
        c.execute("INSERT INTO annotations(user_id,media_id,frame_index) VALUES (?,?,0)", (1, mid))
    original = (directory / 'same.avi').read_bytes()
    result = reset(client, mid)
    assert result.status_code == 200, result.text
    assert reset(client, mid).json() == result.json()
    state = client.get('/api/track/workspace/'+mid).json()
    assert state['revision'] == 2 and state['manualAnnotations'] == [] and state['currentFrame'] == 0
    assert state['generationId']
    assert not (directory / 'tracker_results.json').exists()
    assert not list(directory.glob('annotations_frame_*.json'))
    assert not (directory / 'tracker_overlay.mp4').exists()
    assert (directory / 'same.avi').read_bytes() == original
    kept = list((directory.parent / '_trash').iterdir())  # The discarded work stays recoverable by hand.
    assert len(kept) == 1 and kept[0].name.startswith(mid + '-overwritten-')
    assert json.loads((kept[0] / 'workspace_state.json').read_text())['manualAnnotations']
    assert (kept[0] / 'tracker_overlay.mp4').read_bytes() == b'overlay'
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM annotations WHERE media_id=?', (mid,)).fetchone()[0] == 0
    # An older tab cannot republish old boxes, including unversioned clients.
    assert client.put('/api/track/workspace/'+mid, json={'expectedRevision': 1}, headers={'Idempotency-Key': 'stale'}).status_code == 409
    assert client.put('/api/track/workspace/'+mid, json={}).status_code == 428
    assert reset(client, mid, revision=2).status_code == 409


def test_busy_or_changed_revision_does_not_remove_files(imports, monkeypatch):
    client, content = imports
    mid = upload(client, content).json()['mediaId']; directory = seed_workspace(client, mid)
    before = (directory / 'workspace_state.json').read_bytes()
    assert reset(client, mid, revision=0).status_code == 409
    monkeypatch.setattr(main, 'TASKS', {'task': {'status': 'queued', 'mediaId': mid}})
    assert reset(client, mid).status_code == 409
    assert (directory / 'workspace_state.json').read_bytes() == before
    assert (directory / 'tracker_results.json').is_file()


def test_reset_move_failure_rolls_back_files_and_retries_same_key(imports, monkeypatch, caplog):
    client, content = imports
    mid = upload(client, content).json()['mediaId']; directory = seed_workspace(client, mid)
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    original = Path.replace
    def failure(path, target):
        if path.name == 'tracker_results.json' and '_annotation_resets' in str(target):
            raise PermissionError('injected move failure')
        return original(path, target)
    with monkeypatch.context() as m:
        m.setattr(Path, 'replace', failure)
        response = reset(client, mid)
    assert response.status_code == 503
    assert 'media.reset_failed' in caplog.text
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == before
    assert reset(client, mid).status_code == 200


def test_restart_recovers_uncommitted_reset_journal(imports):
    client, content = imports
    mid = upload(client, content).json()['mediaId']; directory = seed_workspace(client, mid)
    original = (directory / 'workspace_state.json').read_bytes()
    folder = directory.parent / '_annotation_resets' / 'crashed'; folder.mkdir(parents=True)
    (folder / 'journal.json').write_text(json.dumps({'mediaId': mid, 'uid': 1, 'key': 'crashed', 'hash': 'unused', 'files': ['workspace_state.json']}))
    (directory / 'workspace_state.json').replace(folder / 'workspace_state.json')
    (directory / 'workspace_state.json').write_text('{}')
    media_reimport.recover(directory.parent)
    assert (directory / 'workspace_state.json').read_bytes() == original
    assert not folder.exists()


def test_old_seed_manual_and_tracking_requests_cannot_restore_discarded_generation(imports, monkeypatch):
    client, content = imports
    mid = upload(client, content).json()['mediaId']; directory = seed_workspace(client, mid)
    result = reset(client, mid).json()
    generation = result['generationId']
    monkeypatch.setattr(main, 'SAM3_ENABLED', True)
    seed = {'mediaId': mid, 'frameIndex': 0, 'annotations': [{'object_id': 1, 'source': 'manual', 'bbox': [2, 2, 8, 8]}]}
    manual = {'mediaId': mid, 'mediaType': 'video', 'mediaWidth': 64, 'mediaHeight': 48,
              'objects': [{'source': 'manual', 'objectId': 1, 'frameIndex': 0, 'bbox': {'x': 10, 'y': 10, 'width': 10, 'height': 10}}]}
    # A late old-client request must not recreate even a DB query record.
    assert client.post('/api/annotation/annotations/manual', json=manual).status_code == 409
    assert client.post('/api/track/annotations', json=seed).status_code == 409
    assert client.post('/api/track', json={**seed, 'startFrame': 0}).status_code == 409
    future = json.dumps({'frame_index': 2, 'objects': []}) + '\n'
    (directory / 'tracker_results.json').write_text(future)
    assert client.post('/api/track/rewind', json={'mediaId': mid, 'startFrame': 0}).status_code == 409
    assert (directory / 'tracker_results.json').read_text() == future
    assert client.post('/api/annotation/annotations/manual', json={**manual, 'generationId': generation}).status_code == 201
    assert client.post('/api/track/annotations', json={**seed, 'generationId': generation}).status_code == 201
    assert (directory / 'annotations_frame_000000.json').is_file()


def test_old_review_history_no_longer_locks_overwrite(imports):
    """Retired A/B/C/F tasks stay as immutable history and do not block re-annotation."""
    client, content = imports
    mid = upload(client, content).json()['mediaId']; directory = seed_workspace(client, mid)
    from app.review_repository import create_review_session, freeze_baseline, upsert_media_revision
    media = upsert_media_revision(mid, 'd' * 64, 10, 64, 48, 30, 3)
    baseline = freeze_baseline(media, mid, 1, [dict(frameIndex=i, coverage='empty', objects=[]) for i in range(3)])
    sid = create_review_session(baseline['id'])['id']
    review.write('claim', sid, 2, 'old-claim', {})
    assert upload(client, content).json()['duplicate']['canOverwrite']
    assert reset(client, mid).status_code == 200
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM baseline_frames WHERE baseline_id=?', (baseline['id'],)).fetchone()[0] == 3
