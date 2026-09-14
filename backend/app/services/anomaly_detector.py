"""Anomaly detector for sperm tracking annotations.

Pure-math engine (no AI / numpy dependency) that flags suspicious bbox
transitions during SAM3 tracking so the pipeline can pause for human review.

Reviewer fixes implemented:
  Fix 1 - Ghost counting: dead objects (is_dead=True) are never re-reported.
  Fix 2 - Edge detection: uses bbox endpoints with EDGE_MARGIN_PX=30 (not 15).
  Fix 3 - Dirty data: detect first, write history only when hard_score < 1.0.
  Fix 4 - Resume by restart: each track_video() call builds a fresh detector.
  Fix 5 - Multi-target: should_pause fires if ANY object is ANOMALY/DISAPPEARED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import statistics
from typing import Any, Iterable


class AnomalyLevel(str, Enum):
    """Severity level for a single tracked object at a single frame."""

    NORMAL = "normal"
    WARNING = "warning"
    ANOMALY = "anomaly"
    DISAPPEARED = "disappeared"


@dataclass
class AnomalyConfig:
    """All tunable thresholds for the detector."""

    BASELINE_WINDOW: int = 5
    DISAPPEAR_WARN: int = 2
    DISAPPEAR_ALERT: int = 3
    # 面积阈值 (精子小, SAM3 mask 面积会抖但 >3.5x 就是真异常)
    AREA_SHRINK_SOFT: float = 0.35
    AREA_SHRINK_HARD: float = 0.25
    AREA_GROW_SOFT: float = 2.5
    AREA_GROW_HARD: float = 3.5
    # 位移阈值 (主要检测指标: center_shift 抓真实跳飞)
    # cfg 值 * fps_scale(30/fps) = 实际阈值
    # 15fps video: 40 * 2 = 80px; 30fps video: 40 * 1 = 40px
    CENTER_SHIFT_SOFT: float = 25.0
    CENTER_SHIFT_HARD: float = 40.0
    # 宽高比 (精子会转, 但翻转 >2x 是真异常)
    ASPECT_CHANGE_SOFT: float = 1.5
    ASPECT_CHANGE_HARD: float = 2.0
    EDGE_MARGIN_PX: float = 30.0


# ---------------------------------------------------------------------------
# Geometry helpers (pure stdlib, no numpy)
# ---------------------------------------------------------------------------


def _bbox_wh(bbox) -> tuple[float, float]:
    w = float(bbox[2]) - float(bbox[0])
    h = float(bbox[3]) - float(bbox[1])
    return w, h


def _bbox_area(bbox) -> float:
    w, h = _bbox_wh(bbox)
    return max(w, 0.0) * max(h, 0.0)


def _bbox_center(bbox) -> tuple[float, float]:
    cx = (float(bbox[0]) + float(bbox[2])) / 2.0
    cy = (float(bbox[1]) + float(bbox[3])) / 2.0
    return cx, cy


def _bbox_aspect(bbox) -> float:
    w, h = _bbox_wh(bbox)
    if h <= 0:
        return 1.0
    return w / h


def _median_bbox(history: list) -> list | None:
    """Return per-coordinate median of the recent history slice.

    Returns None when history is empty so the caller can short-circuit
    anomaly computation on the very first frame.
    """
    if not history:
        return None
    x1s = [float(b[0]) for b in history]
    y1s = [float(b[1]) for b in history]
    x2s = [float(b[2]) for b in history]
    y2s = [float(b[3]) for b in history]
    return [
        statistics.median(x1s),
        statistics.median(y1s),
        statistics.median(x2s),
        statistics.median(y2s),
    ]


# ---------------------------------------------------------------------------
# State containers
# ---------------------------------------------------------------------------


@dataclass
class _ObjectState:
    """Per-object tracking state (private to the detector)."""

    level: AnomalyLevel = AnomalyLevel.NORMAL
    soft_count: int = 0
    hard_count: int = 0
    recover_count: int = 0
    missing_count: int = 0
    edge_frames: int = 0
    is_dead: bool = False  # Fix 1: terminal flag, never recovers
    dead_at_frame: int | None = None  # Fix 1: frame index of DISAPPEARED
    history: list = field(default_factory=list)


@dataclass
class AnomalyFrame:
    """Per-object anomaly record for a single frame."""

    frame_index: int
    object_id: int
    level: AnomalyLevel
    reasons: list  # list[str]
    details: dict  # metric values for debugging


@dataclass
class AnomalyReport:
    """Aggregated report for one frame across all objects."""

    frame_index: int
    object_levels: dict  # {object_id: AnomalyLevel}
    frames: list  # list[AnomalyFrame]
    should_pause: bool


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class AnomalyDetector:
    """Per-frame anomaly detector with hysteresis state machine.

    Usage:
        det = AnomalyDetector(frame_width=640, frame_height=480, fps=30)
        for i, frame_objs in enumerate(frames):
            report = det.push(i, frame_objs)
            if report.should_pause:
                break  # hand off to human reviewer
    """

    def __init__(
        self,
        config: AnomalyConfig | None = None,
        frame_width: int = 640,
        frame_height: int = 480,
        fps: int = 30,
        all_object_ids: Iterable[int] | None = None,
    ) -> None:
        self.config = config or AnomalyConfig()
        self.frame_width = int(frame_width)
        self.frame_height = int(frame_height)
        self.fps = int(fps) if int(fps) > 0 else 30
        # Fix 4: fresh instance per track_video() call = resume by restart
        self.all_object_ids: set[int] = (
            set(all_object_ids) if all_object_ids else set()
        )
        self.states: dict[int, _ObjectState] = {}
        self.reports: list[AnomalyReport] = []
        self.current_frame_index: int = -1

    # -- internal helpers ---------------------------------------------------

    def _state(self, oid: int) -> _ObjectState:
        if oid not in self.states:
            self.states[oid] = _ObjectState()
        return self.states[oid]

    def _is_at_edge(self, bbox) -> bool:
        """Fix 2: edge check uses bbox ENDPOINTS (not center), 30px buffer."""
        if bbox is None:
            return False
        m = self.config.EDGE_MARGIN_PX
        x1 = float(bbox[0])
        y1 = float(bbox[1])
        x2 = float(bbox[2])
        y2 = float(bbox[3])
        return bool(
            x1 <= m
            or x2 >= self.frame_width - m
            or y1 <= m
            or y2 >= self.frame_height - m
        )

    # -- public API ---------------------------------------------------------

    def push(self, frame_index: int, frame_objects: dict) -> AnomalyReport:
        """Process one frame.

        Args:
            frame_index: 0-based frame counter.
            frame_objects: ``{object_id: bbox[x1, y1, x2, y2]}`` for objects
                visible in this frame. Objects absent from this dict but
                previously seen are treated as missing.

        Returns:
            AnomalyReport for this frame.
        """
        cfg = self.config
        self.current_frame_index = frame_index
        report = AnomalyReport(
            frame_index=frame_index,
            object_levels={},
            frames=[],
            should_pause=False,
        )

        current_ids = set(frame_objects.keys())

        # ------------------------------------------------------------------
        # Step 1: disappeared objects (in all_object_ids but not this frame)
        # Implements Fix 1 (ghost counting) and Fix 2 (edge-aware disappear).
        # ------------------------------------------------------------------
        for oid in (self.all_object_ids - current_ids):
            st = self._state(oid)
            if st.is_dead:
                # Fix 1: terminal state - skip, do NOT re-report
                continue
            st.missing_count += 1
            last_bbox = st.history[-1] if st.history else None
            at_edge = self._is_at_edge(last_bbox)
            if at_edge:
                # Fix 2: object swam off frame boundary, do not escalate
                st.edge_frames += 1
                continue
            st.edge_frames = 0
            if st.missing_count >= cfg.DISAPPEAR_ALERT:
                # Hard disappear: terminal transition
                st.is_dead = True
                st.dead_at_frame = frame_index
                st.level = AnomalyLevel.DISAPPEARED
                report.frames.append(
                    AnomalyFrame(
                        frame_index=frame_index,
                        object_id=oid,
                        level=st.level,
                        reasons=["disappeared"],
                        details={
                            "missing_count": st.missing_count,
                            "at_edge": False,
                            "dead_at_frame": frame_index,
                        },
                    )
                )
                report.object_levels[oid] = st.level
            elif st.missing_count >= cfg.DISAPPEAR_WARN:
                if st.level == AnomalyLevel.NORMAL:
                    st.level = AnomalyLevel.WARNING
                report.frames.append(
                    AnomalyFrame(
                        frame_index=frame_index,
                        object_id=oid,
                        level=st.level,
                        reasons=["missing"],
                        details={
                            "missing_count": st.missing_count,
                            "at_edge": False,
                        },
                    )
                )
                report.object_levels[oid] = st.level

        # ------------------------------------------------------------------
        # Step 2: present objects
        # Implements Fix 3 (detect first, write history only if clean).
        # ------------------------------------------------------------------
        for oid, bbox in frame_objects.items():
            st = self._state(oid)
            if st.is_dead:
                # Fix 1: object already declared dead, ignore new sightings
                continue
            self.all_object_ids.add(oid)
            st.missing_count = 0
            st.edge_frames = 0

            # Fix 3: baseline uses STABLE history only (not current frame)
            recent = st.history[-cfg.BASELINE_WINDOW:] if st.history else []
            base = _median_bbox(recent)

            reasons: list[str] = []
            details: dict[str, Any] = {}
            hard_score = 0
            soft_score = 0

            if base is not None:
                # --- Metric 1: area ratio ---
                base_area = _bbox_area(base)
                cur_area = _bbox_area(bbox)
                if base_area > 0:
                    area_ratio = cur_area / base_area
                    details["area_ratio"] = area_ratio
                    if (
                        area_ratio < cfg.AREA_SHRINK_HARD
                        or area_ratio > cfg.AREA_GROW_HARD
                    ):
                        hard_score += 1
                        reasons.append(f"area_ratio={area_ratio:.3f} HARD")
                    elif (
                        area_ratio < cfg.AREA_SHRINK_SOFT
                        or area_ratio > cfg.AREA_GROW_SOFT
                    ):
                        soft_score += 1
                        reasons.append(f"area_ratio={area_ratio:.3f} SOFT")

                # --- Metric 2: center shift (euclidean) ---
                bcx, bcy = _bbox_center(base)
                ccx, ccy = _bbox_center(bbox)
                dist = ((ccx - bcx) ** 2 + (ccy - bcy) ** 2) ** 0.5
                details["center_shift"] = dist
                # "按 fps 缩放": 30fps is the reference; at higher fps each
                # frame covers less time so the per-frame pixel budget
                # shrinks proportionally.
                fps_scale = 30.0 / self.fps
                hard_t = cfg.CENTER_SHIFT_HARD * fps_scale
                soft_t = cfg.CENTER_SHIFT_SOFT * fps_scale
                if dist >= hard_t:
                    hard_score += 1
                    reasons.append(f"center_shift={dist:.1f}px HARD")
                elif dist >= soft_t:
                    soft_score += 1
                    reasons.append(f"center_shift={dist:.1f}px SOFT")

                # --- Metric 3: aspect ratio change ---
                base_ar = _bbox_aspect(base)
                cur_ar = _bbox_aspect(bbox)
                if base_ar > 0 and cur_ar > 0:
                    ar_ratio = max(base_ar, cur_ar) / min(base_ar, cur_ar)
                    details["aspect_ratio_change"] = ar_ratio
                    if ar_ratio >= cfg.ASPECT_CHANGE_HARD:
                        hard_score += 1
                        reasons.append(f"aspect_change={ar_ratio:.3f} HARD")
                    elif ar_ratio >= cfg.ASPECT_CHANGE_SOFT:
                        soft_score += 1
                        reasons.append(f"aspect_change={ar_ratio:.3f} SOFT")

            # --- Hysteresis counters ---
            # HARD events (shift/area/aspect) are INSTANT — 1 frame → ANOMALY
            # Only SOFT events accumulate (2 frames → WARNING)
            if hard_score > 0:
                st.hard_count += 1
                st.soft_count = 0
                st.recover_count = 0
            elif soft_score > 0:
                st.soft_count += 1
                st.hard_count = 0
                st.recover_count = 0
            else:
                st.recover_count += 1
                st.hard_count = 0
                st.soft_count = 0

            # --- Level transitions ---
            # HARD (any metric) → instant ANOMALY (单帧事件: 跳飞/面积爆炸/宽高比翻转)
            if hard_score >= 1:
                st.level = AnomalyLevel.ANOMALY
            # SOFT × 2 → WARNING (连续温和异常)
            elif st.soft_count >= 2 and st.level == AnomalyLevel.NORMAL:
                st.level = AnomalyLevel.WARNING
            # DISAPPEARED → handled separately below (needs accumulation)

            # Fix 3: commit to history ONLY when no HARD anomaly this frame,
            # so anomalous bboxes cannot pollute the median baseline.
            if hard_score < 1.0:
                st.history.append([float(b) for b in bbox])

            report.frames.append(
                AnomalyFrame(
                    frame_index=frame_index,
                    object_id=oid,
                    level=st.level,
                    reasons=reasons,
                    details=details,
                )
            )
            report.object_levels[oid] = st.level

        # ------------------------------------------------------------------
        # Step 3: aggregate should_pause (Fix 5: multi-target)
        # ------------------------------------------------------------------
        for st in self.states.values():
            if st.level in (AnomalyLevel.ANOMALY, AnomalyLevel.DISAPPEARED):
                report.should_pause = True
                break

        self.reports.append(report)
        return report

    def scan_history(self, frames: Iterable) -> list:
        """Post-hoc scan of completed tracking data (e.g. JSONL replay).

        Args:
            frames: iterable of ``(frame_index, frame_objects_dict)`` tuples.

        Returns:
            List of all AnomalyReports produced.
        """
        for frame_index, frame_objects in frames:
            self.push(frame_index, frame_objects)
        return self.reports
