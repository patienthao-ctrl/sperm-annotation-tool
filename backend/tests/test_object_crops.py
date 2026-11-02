"""Crop sheet for reviewing one object frame by frame (decision 0002)."""
import cv2
import numpy as np
from .test_media_reimport import imports, upload
from .test_review_workflow import task


def test_crop_sheet_lays_out_tiles_and_reports_unreadable_frames(imports):
    client, video = imports
    mid = upload(client, video).json()['mediaId']
    items = [{'frameIndex': fi, 'bbox': [20, 14, 30, 24]} for fi in (0, 1, 2)] + [{'frameIndex': 99, 'bbox': [20, 14, 30, 24]}]
    response = client.post('/api/track/object-crops/' + mid, json={'items': items, 'size': 64})
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert response.headers['X-Crop-Columns'] == '4' and response.headers['X-Crop-Missing'] == '3'
    sheet = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert sheet.shape[:2] == (64, 256)
    # Real pixels in readable tiles (source grey ~40), never a fabricated image for the missing one.
    assert abs(float(sheet[2:10, 2:10].mean()) - 40) < 12 and abs(float(sheet[2:10, 194:202].mean()) - 32) < 6
    # The drawn box is green on the readable tile.
    assert sheet[:, :64, 1].max() > 150


def test_crop_sheet_rejects_bad_requests_and_needs_login(imports):
    client, video = imports
    mid = upload(client, video).json()['mediaId']
    path = '/api/track/object-crops/' + mid
    assert client.post(path, json={'items': []}).status_code == 422
    assert client.post(path, json={'items': [{'frameIndex': 0, 'bbox': [5, 5, 4, 9]}]}).status_code == 422
    assert client.post(path, json={'items': [{'frameIndex': 0, 'bbox': [1, 1, 5, 5]}] * 61}).status_code == 422
    assert client.post('/api/track/object-crops/missing-media', json={'items': [{'frameIndex': 0, 'bbox': [1, 1, 5, 5]}]}).status_code == 404
    client.headers.pop('Authorization')
    assert client.post(path, json={'items': [{'frameIndex': 0, 'bbox': [1, 1, 5, 5]}]}).status_code == 401
