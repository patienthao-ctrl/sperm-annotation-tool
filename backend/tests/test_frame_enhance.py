"""One-button enhancement: the training review recipe, served and cached beside raw frames."""
import cv2
import numpy as np

from app.video_frames import enhance_gray
from .test_review_workflow import task
from .test_media_reimport import imports, upload


def test_median_background_keeps_a_dark_head_dark_and_flattens_the_background():
    gray = np.full((750, 1024), 120, np.uint8)
    gray[:, :300] = 90  # Uneven illumination is removed by the background estimate.
    cv2.ellipse(gray, (600, 400), (6, 4), 0, 0, 360, 30, -1)
    out = enhance_gray(gray)
    assert out.shape == gray.shape and out.dtype == np.uint8
    assert abs(int(out[100, 100]) - 128) < 12 and abs(int(out[100, 800]) - 128) < 12
    assert out[400, 600] < 40  # The head stands out strongly (gain 8).


def test_enhanced_frame_endpoint_is_a_cached_jpeg_of_the_same_size(imports):
    client, video = imports
    mid = upload(client, video).json()['mediaId']
    raw = client.get(f'/api/track/frame/{mid}/1')
    enhanced = client.get(f'/api/track/frame/{mid}/1?enhance=true')
    assert raw.status_code == enhanced.status_code == 200
    assert enhanced.headers['content-type'] == 'image/jpeg' and enhanced.content != raw.content
    image = cv2.imdecode(np.frombuffer(enhanced.content, np.uint8), cv2.IMREAD_UNCHANGED)
    assert image.shape[:2] == (48, 64)
    from app import main
    assert (main.media_dir(mid) / '.frame_cache' / 'frame_00000001_enh.jpg').is_file()
    assert client.get(f'/api/track/frame/{mid}/99?enhance=true').status_code == 404
