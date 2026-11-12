"""Prelabel task: runs without SAM3, publishes tracks through the normal result API."""
import time

import pytest

from app import head_click, main
from .test_review_workflow import task
from .test_media_reimport import imports, upload


def _wait(client, task_id):
    for _ in range(200):
        status = client.get('/api/track/status/' + task_id).json()
        if status['status'] in {'success', 'failed'}:
            return status
        time.sleep(.02)
    raise AssertionError('prelabel never finished')


@pytest.fixture
def fake_detector(monkeypatch):
    calls = []

    def detect_batch(images, conf=None, caller='prelabel'):
        calls.append(len(images))
        # One confident head at (30, 20) on every frame of the 64x48 fixture.
        return [[[25, 15, 35, 25, .9], [50, 40, 54, 44, .2]] for _ in images]
    monkeypatch.setattr(head_click, 'detect_batch', detect_batch)
    monkeypatch.setattr(main, 'SAM3_ENABLED', False)
    return calls


def test_prelabel_publishes_tracks_without_sam3_and_refuses_to_overwrite(imports, fake_detector):
    client, video = imports
    mid = upload(client, video).json()['mediaId']
    assert client.get('/api/prelabel/' + mid).json() == {'state': 'none'}
    started = client.post('/api/prelabel/' + mid, headers={'Idempotency-Key': 'pre-1'})
    assert started.status_code == 202, started.text
    replay = client.post('/api/prelabel/' + mid, headers={'Idempotency-Key': 'pre-1'})
    assert replay.json()['taskId'] == started.json()['taskId']
    status = _wait(client, started.json()['taskId'])
    assert status['status'] == 'success', status
    assert status['tracks'] == 1 and fake_detector == [3]
    result = client.get('/api/track/result/' + mid).json()
    assert result['state'] == 'available' and result['count'] == 3
    boxes = [a for f in result['frames'] for a in f['annotations']]
    assert {a['objectId'] for a in boxes} == {1} and boxes[0]['bbox'] == [25, 15, 35, 25]
    summary = client.get('/api/prelabel/' + mid).json()
    assert summary['state'] == 'available' and summary['checkFrames'][0] == {'frame': 0, 'kind': 'fixed'}
    assert summary['tracks'][0]['objectId'] == 1
    again = client.post('/api/prelabel/' + mid, headers={'Idempotency-Key': 'pre-2'})
    assert again.status_code == 409 and again.json()['detail']['code'] == 'PRELABEL_EXISTS'


def test_prelabel_failure_publishes_nothing(imports, monkeypatch):
    client, video = imports
    mid = upload(client, video).json()['mediaId']
    def broken(images, conf=None, caller='prelabel'):
        raise head_click.ProposalError('头部检测权重缺失，自动预标未完成', 503)
    monkeypatch.setattr(head_click, 'detect_batch', broken)
    status = _wait(client, client.post('/api/prelabel/' + mid).json()['taskId'])
    assert status['status'] == 'failed' and '权重缺失' in status['message']
    assert client.get('/api/track/result/' + mid).json()['state'] == 'not_generated'


def test_extend_returns_the_head_on_every_frame_and_rejects_boxed_heads(imports, fake_detector):
    client, video = imports
    mid = upload(client, video).json()['mediaId']
    _wait(client, client.post('/api/prelabel/' + mid).json()['taskId'])
    # The confident head is already a track; clicking it is refused.
    taken = client.post(f'/api/prelabel/{mid}/extend', json={'frameIndex': 1, 'x': 30, 'y': 20})
    assert taken.status_code == 409 and taken.json()['detail']['code'] == 'ALREADY_BOXED'
    # The weak head (0.2) never became a track; one click adds it on all three frames.
    added = client.post(f'/api/prelabel/{mid}/extend', json={'frameIndex': 1, 'x': 52, 'y': 42})
    assert added.status_code == 200, added.text
    assert [(b['frameIndex'], b['kind']) for b in added.json()['boxes']] == [(0, 'low'), (1, 'seed'), (2, 'low')]
    assert client.get('/api/track/workspace/' + mid).json().get('manualAnnotations', []) == []  # Nothing written.
    assert client.post(f'/api/prelabel/{mid}/extend', json={'frameIndex': 9, 'x': 5, 'y': 5}).status_code == 404
    assert client.post(f'/api/prelabel/{mid}/extend', json={'frameIndex': 0, 'x': 999, 'y': 5}).status_code == 422


def test_check_frames_record_who_scanned_them(imports, fake_detector):
    client, video = imports
    mid = upload(client, video).json()['mediaId']
    assert client.put(f'/api/prelabel/{mid}/scans/0').status_code == 409  # No prelabel yet.
    _wait(client, client.post('/api/prelabel/' + mid).json()['taskId'])
    frames = [c['frame'] for c in client.get('/api/prelabel/' + mid).json()['checkFrames']]
    assert frames == [0, 2]
    done = client.put(f'/api/prelabel/{mid}/scans/2')
    assert done.status_code == 200, done.text
    scanned = {c['frame']: c for c in done.json()['checkFrames']}
    assert scanned[2]['scannedBy'] == 1 and scanned[2]['scannedAt'] and 'scannedBy' not in scanned[0]
    assert client.put(f'/api/prelabel/{mid}/scans/1').status_code == 404  # Not a check frame.


def test_review_can_start_on_a_prelabelled_video_nobody_edited(imports, fake_detector):
    client, video = imports
    mid = upload(client, video).json()['mediaId']
    _wait(client, client.post('/api/prelabel/' + mid).json()['taskId'])
    state = client.get('/api/annotation-review/media/' + mid).json()
    assert state.get('notReady') is None and state['unknownFrames'] == 0


def test_reset_annotations_discards_the_prelabel_and_its_scans(imports, fake_detector):
    client, video = imports
    mid = upload(client, video).json()['mediaId']
    _wait(client, client.post('/api/prelabel/' + mid).json()['taskId'])
    assert client.put(f'/api/prelabel/{mid}/scans/0').status_code == 200
    revision = client.get('/api/track/workspace/' + mid).json().get('revision', 0)
    reset = client.post(f'/api/annotation-review/media/{mid}/reset-annotations', json={'expectedRevision': revision, 'confirmDiscard': True},
                        headers={'Idempotency-Key': 'reset-prelabel'})
    assert reset.status_code == 200, reset.text
    assert client.get('/api/prelabel/' + mid).json() == {'state': 'none'}
    assert client.get('/api/track/result/' + mid).json()['state'] == 'not_generated'
    # Prelabelling again starts clean: no scan survives from the discarded run.
    _wait(client, client.post('/api/prelabel/' + mid).json()['taskId'])
    assert all('scannedBy' not in c for c in client.get('/api/prelabel/' + mid).json()['checkFrames'])
