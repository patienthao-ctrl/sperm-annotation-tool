"""Media deletion uses isolated files/DB, including real I/O and error logs."""
import logging
import cv2
import numpy as np
import shutil

import pytest
from fastapi.testclient import TestClient
from app import main, media_reimport
from app.auth import sign_jwt
from app.video_frames import frames
from .test_review_workflow import task


@pytest.fixture
def media(tmp_path, monkeypatch, task):
    frames.close()
    monkeypatch.setattr(main, 'TRACK_DATA_DIR', tmp_path / 'media')
    monkeypatch.setattr(main, 'LEGACY_TRACK_DATA_DIR', tmp_path / 'legacy')
    monkeypatch.setattr(main, 'TASKS', {})
    root = main.TRACK_DATA_DIR / 'delete-fixture'
    root.mkdir(parents=True)
    writer = cv2.VideoWriter(str(root / 'video.avi'), cv2.VideoWriter_fourcc(*'MJPG'), 10, (64, 48))
    writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.write(np.full((48, 64, 3), 64, dtype=np.uint8))
    writer.release()
    (root / 'workspace_state.json').write_text('{}')
    client = TestClient(main.app)
    client.headers['Authorization'] = 'Bearer ' + sign_jwt({'uid': 1})
    try:
        yield client, root
    finally:
        client.close()
        frames.close()


def decoder_key(root):
    return str((root / 'video.avi').resolve())


def test_delete_is_persistent_and_does_not_remove_same_named_other_media(media, caplog):
    client, root = media
    sibling = root.with_name(root.name + '_001')
    shutil.copytree(root, sibling)
    with caplog.at_level(logging.INFO, logger='review.media'):
        response = client.delete('/api/track/media/' + root.name)
    assert response.status_code == 200 and response.json()['deleted'] is True
    assert not root.exists() and sibling.exists()
    # A fresh request/client does not restore the video.
    reloaded = TestClient(main.app)
    reloaded.headers.update(client.headers)
    assert [m['mediaId'] for m in reloaded.get('/api/track/media').json()['items']] == [sibling.name]
    assert 'media.deleted' in caplog.text and root.name in caplog.text


def test_delete_releases_cached_exact_frame_decoder(media):
    client, root = media
    key = decoder_key(root)

    assert client.get(f'/api/track/frame/{root.name}/0').status_code == 200
    assert key in frames.decoders

    response = client.delete('/api/track/media/' + root.name)

    assert response.status_code == 200
    assert not root.exists()
    assert key not in frames.decoders


def test_delete_releases_only_target_exact_frame_decoder(media):
    client, root = media
    sibling = root.with_name('keep-fixture')
    shutil.copytree(root, sibling)
    target_key = decoder_key(root)
    sibling_key = decoder_key(sibling)

    assert client.get(f'/api/track/frame/{root.name}/0').status_code == 200
    assert client.get(f'/api/track/frame/{sibling.name}/0').status_code == 200
    assert target_key in frames.decoders and sibling_key in frames.decoders

    response = client.delete('/api/track/media/' + root.name)

    assert response.status_code == 200
    assert target_key not in frames.decoders
    assert sibling_key in frames.decoders
    assert client.get(f'/api/track/frame/{sibling.name}/1').status_code == 200
    assert sibling.exists()


def test_failed_filesystem_delete_returns_error_and_log_not_false_success(media, monkeypatch, caplog):
    client, root = media
    key = decoder_key(root)
    assert client.get(f'/api/track/frame/{root.name}/0').status_code == 200
    assert key in frames.decoders

    def denied(*args, **kwargs):
        raise PermissionError('fixture read-only directory')

    monkeypatch.setattr(media_reimport, 'move_to_trash', denied)
    response = client.delete('/api/track/media/' + root.name)
    assert response.status_code == 500
    assert root.exists()
    assert key not in frames.decoders
    assert 'media.delete_failed' in caplog.text and 'fixture read-only directory' in caplog.text
    # The removal lock is released after the failure, and an uncached frame
    # opens a fresh decoder instead of leaving this media permanently blocked.
    assert client.get(f'/api/track/frame/{root.name}/1').status_code == 200
    assert key in frames.decoders


def test_review_baseline_keeps_source_immutable(media):
    client, root = media
    protected = root.with_name('test-video')
    shutil.copytree(root, protected)
    response = client.delete('/api/track/media/test-video')
    assert response.status_code == 409 and '审查基准' in response.json()['detail']
    assert protected.exists()


def test_tracking_blocks_delete(media, monkeypatch):
    client, root = media
    monkeypatch.setattr(main, 'TASKS', {'test': {'status': 'running', 'mediaId': root.name}})
    assert client.delete('/api/track/media/' + root.name).status_code == 409
    assert root.exists()


def test_tracking_another_video_does_not_block_delete(media, monkeypatch):
    client, root = media
    monkeypatch.setattr(main, 'TASKS', {'test': {'status': 'running', 'mediaId': 'other-video'}})
    assert client.delete('/api/track/media/' + root.name).status_code == 200
    assert not root.exists()


def test_legacy_copy_cannot_reappear_after_deleting_shadowing_directory(media):
    client, root = media
    legacy = main.LEGACY_TRACK_DATA_DIR / root.name
    shutil.copytree(root, legacy)
    response = client.delete('/api/track/media/' + root.name)
    assert response.status_code == 409 and '同名目录' in response.json()['detail']
    assert root.exists() and legacy.exists()


def test_legacy_only_media_can_be_deleted(media):
    client, root = media
    legacy = main.LEGACY_TRACK_DATA_DIR / 'legacy-only'
    shutil.copytree(root, legacy)
    assert client.delete('/api/track/media/legacy-only').status_code == 200
    assert not legacy.exists() and root.exists()


def test_delete_requires_auth(media):
    client, root = media
    client.headers.pop('Authorization')
    assert client.delete('/api/track/media/' + root.name).status_code == 401
    assert root.exists()


@pytest.mark.parametrize('media_id', ['.', '..', '../delete-fixture', 'sub/delete-fixture'])
def test_delete_rejects_noncanonical_ids_without_touching_storage(media, media_id):
    client, root = media
    with pytest.raises(main.HTTPException) as error:
        main.delete_media(media_id, {'uid': 1})
    assert error.value.status_code == 400
    assert root.exists()


def test_deleted_media_stays_recoverable_and_leaves_the_library(media):
    client, root = media
    video = (root / 'video.avi').read_bytes()
    assert client.delete('/api/track/media/' + root.name).status_code == 200
    kept = list((main.TRACK_DATA_DIR / '_trash').iterdir())
    assert len(kept) == 1 and kept[0].name.startswith(root.name + '-')
    assert (kept[0] / 'video.avi').read_bytes() == video and (kept[0] / 'workspace_state.json').exists()
    assert client.get('/api/track/media').json()['items'] == []
    # Moving it back by hand restores the medium.
    kept[0].replace(root)
    assert [item['mediaId'] for item in client.get('/api/track/media').json()['items']] == [root.name]


def test_trashed_video_does_not_block_a_fresh_upload(media):
    client, root = media
    video = (root / 'video.avi').read_bytes()
    assert client.delete('/api/track/media/' + root.name).status_code == 200
    response = client.post('/api/track/upload', files={'file': ('video.avi', video, 'video/x-msvideo')})
    assert response.status_code == 201, response.text
