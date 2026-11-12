"""AI tracking and head clicks share one detector: they must queue, not fail."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from app import head_click

WAIT = 5


class Values:
    def __init__(self, data):
        self.data = data

    def cpu(self):
        return self

    def tolist(self):
        return self.data


def _result():
    return SimpleNamespace(boxes=SimpleNamespace(xyxy=Values([[504.32, 376.32, 529.92, 401.92]]), conf=Values([.9])))


@pytest.fixture
def detector(tmp_path, monkeypatch):
    """Real queue and detector functions; only the YOLO model is replaced."""
    weight = tmp_path / 'head.pt'
    weight.write_bytes(b'fixture-not-a-model')
    monkeypatch.setenv('HEAD_DETECTOR_MODEL', str(weight))
    gate = SimpleNamespace(entered=threading.Event(), release=threading.Event(), calls=0, order=[], fail_next=False)
    gate.release.set()

    def predict(image, **kwargs):
        gate.calls += 1
        gate.order.append('regions' if isinstance(image, list) else 'single')
        gate.entered.set()
        assert gate.release.wait(WAIT)
        if gate.fail_next:
            gate.fail_next = False
            raise RuntimeError('injected predictor failure')
        return [_result() for _ in image] if isinstance(image, list) else [_result()]

    monkeypatch.setattr(head_click, '_model', SimpleNamespace(predict=predict))
    monkeypatch.setattr(head_click, '_model_path', str(weight.resolve()))
    return gate


IMAGE = np.zeros((600, 800, 3), dtype=np.uint8)
CALLS = {
    'edges': lambda: head_click.detect_edges(IMAGE),
    'regions': lambda: head_click.detect_regions(IMAGE, [[390, 290, 410, 310]]),
    'frame': lambda: head_click.detect_frame(IMAGE),
    'click': lambda: head_click.propose(IMAGE, 400, 300),
}


def _run(call):
    outcome = {}

    def target():
        try:
            outcome['value'] = call()
        except Exception as exc:  # Reported to the asserting thread.
            outcome['error'] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def _hold_detector(gate):
    """Start a click that stays inside the model until the test releases it."""
    gate.release.clear()
    thread, outcome = _run(CALLS['click'])
    assert gate.entered.wait(WAIT)
    return thread, outcome


@pytest.mark.parametrize('name', ['edges', 'regions', 'frame', 'click'])
def test_every_detector_call_waits_for_a_click_in_progress(detector, name):
    holder, held = _hold_detector(detector)
    calls_before = detector.calls
    waiter, outcome = _run(CALLS[name])
    waiter.join(.2)
    # Still queued: neither failed fast nor entered the model concurrently.
    assert waiter.is_alive() and not outcome and detector.calls == calls_before
    detector.release.set()
    holder.join(WAIT)
    waiter.join(WAIT)
    assert not holder.is_alive() and not waiter.is_alive()
    assert 'error' not in held and 'error' not in outcome
    assert 'value' in outcome


def _wait_until_queued(count=1):
    for _ in range(500):
        if head_click._lock.waiting() >= count:
            return
        threading.Event().wait(.01)
    raise AssertionError('caller never joined the detector queue')


def test_click_is_not_starved_by_a_tracking_loop(detector):
    """A loop that re-acquires immediately must still let a queued click in."""
    detector.release.clear()

    def tracking():
        for _ in range(3):
            CALLS['regions']()

    tracker_thread, tracked = _run(tracking)
    assert detector.entered.wait(WAIT)
    clicker, clicked = _run(CALLS['click'])
    _wait_until_queued()
    detector.release.set()
    clicker.join(WAIT)
    tracker_thread.join(WAIT)
    assert 'error' not in tracked and 'error' not in clicked
    # The click takes the next turn instead of waiting for the whole loop.
    assert detector.order == ['regions', 'single', 'regions', 'regions']


def test_wait_timeout_is_a_clear_failure_and_detector_recovers(detector, monkeypatch, caplog):
    monkeypatch.setattr(head_click, 'BACKGROUND_WAIT_SECONDS', .05)
    caplog.set_level('WARNING', logger='review.annotation.head_click')
    holder, _ = _hold_detector(detector)
    with pytest.raises(head_click.ProposalError, match='忙') as caught:
        CALLS['edges']()
    assert caught.value.status == 503
    assert 'annotation.head_detector_wait_timeout' in caplog.text
    assert head_click._lock.waiting() == 0  # A timed-out waiter leaves the queue.
    detector.release.set()
    holder.join(WAIT)
    assert not holder.is_alive()
    assert CALLS['click']()['bbox'] == pytest.approx([394, 294, 414, 314])  # Nothing leaked.


def test_model_failure_hands_detector_to_the_next_in_queue(detector):
    holder, held = _hold_detector(detector)
    waiter, outcome = _run(CALLS['click'])
    _wait_until_queued()
    detector.fail_next = True
    detector.release.set()
    holder.join(WAIT)
    waiter.join(WAIT)
    assert not holder.is_alive() and not waiter.is_alive()
    assert isinstance(held['error'], RuntimeError)  # Failure stays visible.
    assert outcome['value']['bbox'] == pytest.approx([394, 294, 414, 314])


def test_tracking_with_edge_discovery_survives_a_concurrent_click(detector, tmp_path, monkeypatch):
    """The reported failure: a click holding the detector must not fail tracking."""
    import json
    import cv2
    from app import tracker
    from .test_tracking_adaptive_feedback import FakeWindow
    video = tmp_path / 'video.avi'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 20, (200, 100))
    frame = np.random.default_rng(1).integers(0, 255, (100, 200, 3), dtype=np.uint8)
    for _ in range(5):
        writer.write(frame)
    writer.release()
    seed = tmp_path / 'seed_frame_000000.json'
    seed.write_text(json.dumps({'frame': {'frameIndex': 0}, 'annotations': [
        {'object_id': 7, 'bbox': [100, 40, 110, 50], 'source': 'manual'}]}))
    (tmp_path / 'workspace_state.json').write_text('{}')
    meta = {'width': 200, 'height': 100, 'fps': 20, 'frameCount': 5, 'source_frame_indices': list(range(5))}

    class Engine:
        model_id = 'fake'
        device = 'cpu'
        torch_dtype = 'float32'

        def make_tracker_session(self, frames):
            return {}

        def add_manual_boxes(self, session, frame, objects):
            session.update({o['object_id']: o['bbox'] for o in objects})

        def propagate_manual(self, session, max_frames, start_frame_idx):
            for index in range(start_frame_idx, 5):
                yield SimpleNamespace(frame_idx=index)

        def decode_tracker_output(self, session, output):
            return [SimpleNamespace(object_id=oid, bbox=box, score=.9, mask_area=None, sam3_object_id=oid)
                    for oid, box in session.items()], None

    monkeypatch.setattr(tracker, 'get_sam3_engine', lambda *a: Engine())
    monkeypatch.setattr(tracker, 'SourceVideoWindow', lambda *a, **kw: FakeWindow(meta))
    holder, held = _hold_detector(detector)
    track, outcome = _run(lambda: tracker.track_video(
        str(video), str(seed), str(tmp_path / 'tracker_results.json'), 5, edge_discovery=True))
    _wait_until_queued()
    detector.release.set()
    holder.join(WAIT)
    track.join(WAIT * 2)
    assert 'error' not in held
    assert 'error' not in outcome
    assert len(outcome['value']['frames']) == 5
