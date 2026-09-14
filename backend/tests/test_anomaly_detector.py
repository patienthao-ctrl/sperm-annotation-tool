"""Unit tests for AnomalyDetector - pure mock data, no video / SAM3 needed.

Covers the 5 reviewer fixes and the core metrics:
  1. normal frames -> no anomaly
  2. area shrink HARD -> ANOMALY
  3. single jitter -> no trigger (hysteresis)
  4. disappearance -> DISAPPEARED after DISAPPEAR_ALERT frames
  5. edge disappearance -> OK (Fix 2 regression)
  6. ghost counting -> DISAPPEARED reported once (Fix 1 regression)
  7. HARD anomaly bbox does not pollute baseline (Fix 3 regression)
  8. center shift -> ANOMALY
"""

import os
import sys

# Make backend package importable when running `python -m pytest` from
# the backend/ directory.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.anomaly_detector import (  # noqa: E402
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
        # small deterministic jitter well within SOFT thresholds
        cx = 320 + (i % 3 - 1) * 2          # 318 / 320 / 322
        cy = 216 + (i % 3 - 1) * 2
        area = 200 + (i % 5 - 2) * 4         # 192..208
        det.push(i, {1: _bbox(area=area, cx=cx, cy=cy)})

    assert not any(r.should_pause for r in det.reports)
    assert det.states[1].level == AnomalyLevel.NORMAL
    assert det.states[1].hard_count == 0
    assert det.states[1].soft_count == 0


# ---------------------------------------------------------------------------
# Test 2: area shrinks to 25% -> HARD -> ANOMALY (after hysteresis)
# ---------------------------------------------------------------------------


def test_area_shrink_hard_triggers_anomaly() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    # 10 normal frames to build a stable baseline
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # 3 frames with area = 50 (25% of baseline -> area_ratio 0.25 < 0.3 HARD)
    for i in range(10, 13):
        det.push(i, {1: _bbox(area=50, cx=320, cy=216)})

    # Frame 10: hard_count=1, level still NORMAL (hysteresis=2)
    assert det.reports[10].object_levels[1] == AnomalyLevel.NORMAL
    # Frame 11: hard_count=2 -> ANOMALY
    assert det.reports[11].object_levels[1] == AnomalyLevel.ANOMALY
    assert det.reports[11].should_pause is True
    # Frame 12: still ANOMALY
    assert det.reports[12].object_levels[1] == AnomalyLevel.ANOMALY
    assert det.reports[12].should_pause is True
    assert det.states[1].level == AnomalyLevel.ANOMALY


# ---------------------------------------------------------------------------
# Test 3: single jitter frame must NOT trigger (hysteresis)
# ---------------------------------------------------------------------------


def test_single_jitter_does_not_trigger() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # one HARD jitter frame
    det.push(10, {1: _bbox(area=50, cx=320, cy=216)})
    # back to normal
    for i in range(11, 15):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})

    assert det.states[1].level == AnomalyLevel.NORMAL
    assert det.states[1].hard_count == 0
    assert not any(r.should_pause for r in det.reports)


# ---------------------------------------------------------------------------
# Test 4: object disappears for 4 frames -> DISAPPEARED
# ---------------------------------------------------------------------------


def test_disappearance_triggers_after_threshold() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # object 1 disappears for 4 frames (DISAPPEAR_ALERT=4)
    for i in range(10, 14):
        det.push(i, {})

    # Frame 13 is the 4th missing frame -> DISAPPEARED
    assert det.reports[13].object_levels[1] == AnomalyLevel.DISAPPEARED
    assert det.reports[13].should_pause is True
    assert det.states[1].level == AnomalyLevel.DISAPPEARED
    assert det.states[1].is_dead is True
    assert det.states[1].dead_at_frame == 13


# ---------------------------------------------------------------------------
# Test 5: bbox at frame edge -> disappearance is OK (Fix 2 regression)
# ---------------------------------------------------------------------------


def test_edge_disappearance_is_ok() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    # last bbox touches the right edge: x2=625 >= 640-30=610
    edge_bbox = [608.0, 209.0, 625.0, 223.0]
    for i in range(10):
        det.push(i, {1: list(edge_bbox)})
    # object disappears for 6 frames (well past DISAPPEAR_ALERT=4)
    for i in range(10, 16):
        det.push(i, {})

    assert det.states[1].level != AnomalyLevel.DISAPPEARED
    assert det.states[1].is_dead is False
    assert det.states[1].dead_at_frame is None
    assert not any(r.should_pause for r in det.reports)


# ---------------------------------------------------------------------------
# Test 6: DISAPPEARED reported exactly once (Fix 1 ghost-counting regression)
# ---------------------------------------------------------------------------


def test_ghost_counting_prevention() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # object disappears for 8 frames (4 to trigger + 4 more after death)
    for i in range(10, 18):
        det.push(i, {})

    disappeared_frames = [
        f for r in det.reports
        for f in r.frames
        if f.object_id == 1 and f.level == AnomalyLevel.DISAPPEARED
    ]
    assert len(disappeared_frames) == 1
    assert disappeared_frames[0].frame_index == 13
    assert det.states[1].is_dead is True


# ---------------------------------------------------------------------------
# Test 7: HARD anomaly bbox must NOT pollute baseline (Fix 3 regression)
# ---------------------------------------------------------------------------


def test_hard_anomaly_does_not_pollute_baseline() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # 2 HARD anomaly frames: center shifts 60px (> 40px HARD threshold)
    for i in range(10, 12):
        det.push(i, {1: _bbox(area=200, cx=380, cy=216)})
    # return to normal for 3 frames to allow recovery
    for i in range(12, 15):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})

    history = det.states[1].history
    # normal bbox x1 = 320 - 22.36/2 = 308.82
    # anomaly bbox x1 = 380 - 22.36/2 = 368.82
    # Fix 3: anomaly bboxes must NOT be in history
    for b in history:
        assert b[0] < 350, f"anomaly bbox leaked into history: {b}"

    # Frame 12 is the first normal frame after anomaly. Because history was
    # not polluted, baseline_cx ~= 320 and center_shift ~= 0 (no SOFT/HARD).
    frame12 = next(f for f in det.reports[12].frames if f.object_id == 1)
    assert frame12.details.get("center_shift", 0.0) < 25.0
    assert frame12.reasons == []  # no anomaly this frame

    # After 3 recovery frames, level returns to NORMAL
    assert det.reports[14].object_levels[1] == AnomalyLevel.NORMAL
    assert det.states[1].level == AnomalyLevel.NORMAL


# ---------------------------------------------------------------------------
# Test 8: center shift >= 40px -> HARD -> ANOMALY
# ---------------------------------------------------------------------------


def test_center_shift_triggers_anomaly() -> None:
    det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
    for i in range(10):
        det.push(i, {1: _bbox(area=200, cx=320, cy=216)})
    # 3 frames with cx=370 (50px shift > 40px HARD)
    for i in range(10, 13):
        det.push(i, {1: _bbox(area=200, cx=370, cy=216)})

    # Frame 10: hard_count=1, still NORMAL (hysteresis=2)
    assert det.reports[10].object_levels[1] == AnomalyLevel.NORMAL
    # Frame 11: hard_count=2 -> ANOMALY
    assert det.reports[11].object_levels[1] == AnomalyLevel.ANOMALY
    assert det.reports[11].should_pause is True
    assert det.states[1].level == AnomalyLevel.ANOMALY


# ---------------------------------------------------------------------------
# Sanity: config defaults match the spec
# ---------------------------------------------------------------------------


def test_config_defaults_match_spec() -> None:
    cfg = AnomalyConfig()
    assert cfg.BASELINE_WINDOW == 5
    assert cfg.HYSTERESIS_FRAMES == 2
    assert cfg.RECOVERY_FRAMES == 3
    assert cfg.DISAPPEAR_WARN == 2
    assert cfg.DISAPPEAR_ALERT == 4
    assert cfg.AREA_SHRINK_HARD == 0.3
    assert cfg.AREA_GROW_HARD == 2.5
    assert cfg.CENTER_SHIFT_HARD == 40.0
    assert cfg.ASPECT_CHANGE_HARD == 1.8
    assert cfg.EDGE_MARGIN_PX == 30.0  # Fix 2: 30 not 15
