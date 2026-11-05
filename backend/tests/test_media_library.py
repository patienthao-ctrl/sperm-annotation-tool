"""Library folders: one path per video, set on import, moved and renamed without touching files."""
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app import main, media_library as library
from app.auth import sign_jwt
from app.review_workflow import ReviewError
from .test_review_workflow import task  # noqa: F401


@pytest.fixture
def client(tmp_path, monkeypatch, task):  # noqa: F811
    monkeypatch.setattr(main, 'TRACK_DATA_DIR', tmp_path / 'media')
    monkeypatch.setattr(main, 'LEGACY_TRACK_DATA_DIR', tmp_path / 'legacy')
    monkeypatch.setattr(main, 'TASKS', {})
    http = TestClient(main.app)
    http.headers['Authorization'] = 'Bearer ' + sign_jwt({'uid': 1})
    return http


def video(tmp_path, shade):
    path = tmp_path / f'{shade}.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (64, 48))
    writer.write(np.full((48, 64, 3), shade, dtype=np.uint8))
    writer.release()
    return path.read_bytes()


def upload(client, content, name, folder=None):
    return client.post('/api/track/upload', files={'file': (name, content, 'video/x-msvideo')}, data={} if folder is None else {'folderPath': folder})


def listed(client):
    return {item['mediaId']: item for item in client.get('/api/track/media').json()['items']}


@pytest.mark.parametrize('raw,expected', [('', ''), (' a / b/ ', 'a/b'), ('a\\b', 'a/b'), ('//a//', 'a')])
def test_paths_are_normalized(raw, expected):
    assert library.normalize(raw) == expected


@pytest.mark.parametrize('raw', ['a/../b', './a', '/'.join('abcdefghij'), 'x' * 101])
def test_bad_paths_are_refused(raw):
    with pytest.raises(ReviewError) as e:
        library.normalize(raw)
    assert e.value.code == 'INVALID_FOLDER_PATH'


def test_upload_places_the_video_and_old_clients_land_uncategorized(client, tmp_path):
    first = upload(client, video(tmp_path, 10), 'a.avi', '第一批/样本A').json()['mediaId']
    second = upload(client, video(tmp_path, 90), 'b.avi').json()['mediaId']
    items = listed(client)
    assert items[first]['folderPath'] == '第一批/样本A' and items[second]['folderPath'] == ''
    assert items[first]['sizeBytes'] > 0
    assert upload(client, video(tmp_path, 150), 'c.avi', 'a/../b').status_code == 422


def test_duplicate_upload_does_not_move_the_existing_video(client, tmp_path):
    content = video(tmp_path, 10)
    mid = upload(client, content, 'a.avi', '原来').json()['mediaId']
    assert upload(client, content, 'other.avi', '新的').status_code == 409
    assert listed(client)[mid]['folderPath'] == '原来'


def test_move_rename_merge_and_uncategorize(client, tmp_path):
    a = upload(client, video(tmp_path, 10), 'a.avi', '批/样本A').json()['mediaId']
    b = upload(client, video(tmp_path, 60), 'b.avi', '批').json()['mediaId']
    c = upload(client, video(tmp_path, 120), 'c.avi', '批次二').json()['mediaId']
    renamed = client.post('/api/library/folder/rename', json={'oldPath': '批', 'newPath': '十月/批'})
    assert renamed.status_code == 200 and renamed.json()['moved'] == 2
    items = listed(client)
    assert items[a]['folderPath'] == '十月/批/样本A' and items[b]['folderPath'] == '十月/批'
    assert items[c]['folderPath'] == '批次二'  # A similar name is a different folder.
    assert client.post('/api/library/folder/rename', json={'oldPath': '十月', 'newPath': '十月/子'}).status_code == 422
    assert client.put('/api/library/folder', json={'mediaIds': [a, c], 'path': '合并'}).json()['moved'] == 2
    assert client.put('/api/library/folder', json={'mediaIds': [c], 'path': ''}).status_code == 200
    items = listed(client)
    assert items[a]['folderPath'] == '合并' and items[c]['folderPath'] == ''
    assert client.put('/api/library/folder', json={'mediaIds': ['missing'], 'path': 'x'}).status_code == 404
    assert (main.media_dir(a) / 'a.avi').exists()  # Files never move.


def test_folder_routes_need_a_login(client):
    client.headers.pop('Authorization')
    assert client.put('/api/library/folder', json={'mediaIds': ['x'], 'path': 'y'}).status_code == 401


def test_reused_id_does_not_inherit_the_old_folder(client, tmp_path):
    mid = upload(client, video(tmp_path, 10), 'a.avi', '旧').json()['mediaId']
    assert client.delete('/api/track/media/' + mid).status_code == 200
    again = upload(client, video(tmp_path, 200), 'a.avi').json()['mediaId']
    assert listed(client)[again]['folderPath'] == ''


def test_undecodable_upload_does_not_reveal_server_paths(client):
    response = upload(client, b'not a video', 'broken.avi', 'x')
    assert response.status_code == 400 and '/' not in response.json()['detail'] and 'broken' not in response.json()['detail']
