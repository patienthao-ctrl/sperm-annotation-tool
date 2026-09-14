"""Unit tests for AnomalyDetector - pure mock data, no video / SAM3 needed.

Covers the 6 metrics with NEW single-frame trigger logic:
  1. normal frames -> no anomaly (baseline)
  2. HARD area shrink -> ANOMALY same frame (单帧触发)
  3. SOFT jitter only -> no trigger (accumulated SOFT only)
  4. disappearance 3 frames -> DISAPPEARED (DISAPPEAR_ALERT=3)
  5. edge disappearance -> OK (Fix 2 regression)
  6. DISAPPEARED reported exactly once (ghost prevention)
  7. HARD bbox does not pollute baseline (Fix 3 regression)
  8. center shift HARD -> ANOMALY same frame (单帧触发)
  9. config defaults match spec
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.anomaly_detector import (
    AnomalyConfig,
    AnomalyDetector,
    AnomalyLevel,
)


def _bbox(area: float = 200.0, cx: float = 320.0,
          cy: float = 216.0, aspect: float = 2.5) -> list:
    """Build a [x1, y1, x2, y2] bbox with the requested geometric invariants."""
    h = (area / aspect) ** 0.5
    w = area / h
    x1, y1 = cx - w / 2, cy - h / 2
    return [x1, y1, x1 + w, y1 + h]


# ---------------------------------------------------------------------------
# Test 1: 20 frames of normal swimming never pause
# ---------------------------------------------------------------------------


def test_normal_frames_no_anomaly() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(20):
        cx = 320 + (i % 3 - 1) * 2
        cy = 216 + (i % 3 - 1) * 2
        area = 200 + (i % 5 - 2) * 4
        det.push(i, {1: _bbox(area=area, cx=cx, cy=cy)})

    assert not any(r.should_pause for r in det.reports)
    assert det.states[1].level == AnomalyLevel.NORMAL


# ---------------------------------------------------------------------------
# Test 2: area shrinks 4x -> HARD -> ANOMALY SAME FRAME (单帧触发)
# ---------------------------------------------------------------------------


def test_area_shrink_hard_triggers_anomaly() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # area=50 is 0.25x of 200, AREA_SHRINK_HARD=0.25 → HARD
    det.push(10, {1: _bbox(area=50, cx=320, cy=216)})

    # NEW: HARD events are INSTANT — frame 10 is ANOMALY, no hysteresis wait
    assert det.reports[10].object_levels[1] == AnomalyLevel.ANOMALY
    assert det.reports[10].should_pause is True
    assert det.states[1].level == AnomalyLevel.ANOMALY


# ---------------------------------------------------------------------------
# Test 3: single SOFT jitter must NOT trigger
# ---------------------------------------------------------------------------


def test_single_soft_jitter_does_not_trigger() -> None:
    """单帧 SOFT 不够, 只有 HARD 才单帧触发"""
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # area=100 is 0.5x → SOFT (AREA_SHRINK_SOFT=0.35, 0.5 > 0.35 so... hmm)
    # Let me use center_shift SOFT: 30px > 25 SOFT but < 40 HARD
    det.push(10, {1: _bbox(area=200, cx=350, cy=216)})  # 30px shift
    # back to normal
    for i in range(11, 15):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})

    # 30px shift is SOFT (between 25 and 40), single SOFT frame → no trigger
    assert det.states[1].level == AnomalyLevel.NORMAL
    assert not any(r.should_pause for r in det.reports)


# ---------------------------------------------------------------------------
# Test 4: object disappears for 3 frames -> DISAPPEARED (DISAPPEAR_ALERT=3)
# ---------------------------------------------------------------------------


def test_disappearance_triggers_after_threshold() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # object 1 disappears for 3 frames (DISAPPEAR_ALERT=3)
    for i in range(10, 13):
        det.push(i, {})

    # Frame 12 is the 3rd missing frame → DISAPPEARED
    assert det.reports[12].object_levels[1] == AnomalyLevel.DISAPPEARED
    assert det.reports[12].should_pause is True
    assert det.states[1].level == AnomalyLevel.DISAPPEARED
    assert det.states[1].is_dead is True
    assert det.states[1].dead_at_frame == 12


# ---------------------------------------------------------------------------
# Test 5: bbox at frame edge -> disappearance is OK (Fix 2 regression)
# ---------------------------------------------------------------------------


def test_edge_disappearance_is_ok() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    # bbox touches the right edge (625 >= 640-30=610)
    edge_bbox = [608.0, 209.0, 625.0, 223.0]
    for i in range(10):
        det.push(i, {1: list(edge_bbox)})
    # disappears for 6 frames (well past DISAPPEAR_ALERT=3)
    for i in range(10, 16):
        det.push(i, {})

    assert det.states[1].level != AnomalyLevel.DISAPPEARED
    assert det.states[1].is_dead is False
    assert not any(r.should_pause for r in det.reports)


# ---------------------------------------------------------------------------
# Test 6: DISAPPEARED reported exactly once (Fix 1 ghost-counting regression)
# ---------------------------------------------------------------------------


def test_ghost_counting_prevention() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # disappears for 8 frames
    for i in range(10, 18):
        det.push(i, {})

    disappeared_frames = [
        f for r in det.reports
        for f in r.frames
        if f.object_id == 1 and f.level == AnomalyLevel.DISAPPEARED
    ]
    assert len(disappeared_frames) == 1
    assert disappeared_frames[0].frame_index == 12  # DISAPPEAR_ALERT=3
    assert det.states[1].is_dead is True


# ---------------------------------------------------------------------------
# Test 7: HARD anomaly bbox must NOT pollute baseline (Fix 3 regression)
# ---------------------------------------------------------------------------


def test_hard_anomaly_does_not_pollute_baseline() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # HARD center shift 60px (> 40 HARD threshold)
    det.push(10, {1: _bbox(area=200, cx=380, cy=216)})
    # return to normal
    for i in range(11, 15):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})

    history = det.states[1].history
    # anomaly bbox x1 = 380 - 22.36/2 = 368.82
    for b in history:
        assert b[0] < 350, f"anomaly bbox leaked into history: {b}"


# ---------------------------------------------------------------------------
# Test 8: center shift >= 40px -> HARD -> ANOMALY SAME FRAME
# ---------------------------------------------------------------------------


def test_center_shift_triggers_anomaly() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # 50px shift > 40 HARD → single-frame trigger
    det.push(10, {1: _bbox(area=200, cx=370, cy=216)})

    # NEW: HARD → instant ANOMALY, no hysteresis
    assert det.reports[10].object_levels[1] == AnomalyLevel.ANOMALY
    assert det.reports[10].should_pause is True
    assert det.states[1].level == AnomalyLevel.ANOMALY


# ---------------------------------------------------------------------------
# Test 9: config defaults match spec
# ---------------------------------------------------------------------------


def test_config_defaults_match_spec() -> None:
    cfg = AnomalyConfig()
    assert cfg.BASELINE_WINDOW == 5
    # HYSTERESIS_FRAMES removed — HARD events are single-frame, SOFT accumulates 2
    assert cfg.DISAPPEAR_WARN == 2
    assert cfg.DISAPPEAR_ALERT == 3
    assert cfg.AREA_SHRINK_HARD == 0.25
    assert cfg.AREA_GROW_HARD == 3.5
    assert cfg.CENTER_SHIFT_HARD == 40.0
    assert cfg.ASPECT_CHANGE_HARD == 2.0
    assert cfg.EDGE_MARGIN_PX == 30.0
