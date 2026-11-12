"""Detector prelabel linking: confident boxes start tracks, weak boxes only extend them."""
from app import detector_tracks as dt

W, H = 2048, 1500  # gate = 50 px


def box(x, y, score, size=16):
    return [x - size / 2, y - size / 2, x + size / 2, y + size / 2, score]


def test_weak_boxes_fill_flicker_but_never_start_a_track():
    frames = [[box(100, 100, .8)], [box(101, 100, .2)], [], [box(103, 100, .9)], [box(500, 500, .2)], [box(500, 500, .25)]]
    tracks = dt.link(frames, W, H, .3, .1)
    assert len(tracks) == 1  # The lone weak head at (500, 500) never becomes a track.
    kinds = {f: item['kind'] for f, item in tracks[0]['frames'].items()}
    assert kinds == {0: 'high', 1: 'low', 2: 'interp', 3: 'high'}
    assert tracks[0]['frames'][2]['bbox'] == [94.0, 92.0, 110.0, 108.0]
    assert tracks[0]['frames'][2]['score'] is None


def test_one_confident_frame_is_not_enough():
    frames = [[box(100, 100, .9)], [box(100, 100, .2)], [box(100, 100, .2)]]
    assert dt.link(frames, W, H, .3, .1) == []


def test_long_gaps_split_and_moving_heads_follow_their_velocity():
    still = [[box(100, 100, .9)]] * 2 + [[]] * 5 + [[box(100, 100, .9)]] * 2
    assert len(dt.link(still, W, H, .3, .1)) == 2  # A gap longer than 3 frames is not bridged.
    moving = [[box(100 + 40 * f, 300, .9)] for f in range(6)]
    tracks = dt.link(moving, W, H, .3, .1)
    assert len(tracks) == 1 and len(tracks[0]['frames']) == 6


def test_nearby_heads_are_flagged_as_possible_swaps():
    frames = [[box(100 + 10 * f, 100, .9), box(200 - 10 * f, 104, .9)] for f in range(6)]
    tracks = dt.link(frames, W, H, .3, .1)
    assert len(tracks) == 2
    assert all(any(flag['kind'] == 'cross' for flag in t['flags']) for t in tracks)


def test_check_frames_cover_every_tenth_frame_and_entries():
    tracks = [{'frames': {f: {} for f in range(14, 27)}, 'flags': []}, {'frames': {f: {} for f in range(0, 35)}, 'flags': []}]
    frames = dt.check_frames(35, tracks)
    assert [(c['frame'], c['kind']) for c in frames] == [(0, 'fixed'), (10, 'fixed'), (14, 'event'), (20, 'fixed'), (26, 'event'), (30, 'fixed'), (34, 'fixed')]
    # An event within two frames of a chosen frame adds nothing.
    near = [{'frames': {f: {} for f in range(11, 35)}, 'flags': []}]
    assert [c['frame'] for c in dt.check_frames(35, near)] == [0, 10, 20, 30, 34]


def test_new_ids_skip_manual_tombstones_and_seeds(tmp_path):
    (tmp_path / 'seed_frame_000000.json').write_text('{"annotations":[{"object_id":12}]}')
    workspace = {'manualAnnotations': [{'objectId': 3}], 'deletedObjectIds': [7]}
    reserved = dt.reserved_object_ids(workspace, tmp_path)
    assert reserved == {3, 7, 12}
    rows, ids = dt.build_rows([{'frames': {0: {'bbox': [1, 1, 5, 5], 'score': .9, 'kind': 'high'}}, 'flags': []}], 2, reserved)
    assert ids == {0: 13}
    assert rows[0]['objects'][0]['object_id'] == 13 and rows[0]['objects'][0]['source'] == 'detector_track'
    assert rows[1]['objects'] == []


def test_manual_boxes_suppress_the_same_detection():
    frames = [[box(100, 100, .9), box(300, 300, .9)]]
    dt.suppress_manual(frames, {0: [[92, 92, 108, 108]]})
    assert frames == [[box(300, 300, .9)]]


def test_extend_follows_weak_boxes_skips_taken_heads_and_stops_after_misses():
    detections = [[box(100, 100, .15)], [box(102, 100, .12)], [], [box(106, 100, .11), box(300, 300, .9)],
                  [], [], [], [], [box(110, 100, .9)]]
    out = dt.extend(detections, 1, box(102, 100, 1)[:4], {}, W, H, .1)
    assert [(b['frameIndex'], b['kind']) for b in out] == [(0, 'low'), (1, 'seed'), (2, 'interp'), (3, 'low')]
    taken = dt.extend(detections, 1, box(102, 100, 1)[:4], {0: [box(100, 100, 1)[:4]]}, W, H, .1)
    assert [b['frameIndex'] for b in taken] == [1, 2, 3]  # Frame 0's head already belongs to someone.
