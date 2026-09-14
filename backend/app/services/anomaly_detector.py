"""纯数学异常检测引擎 —— 在 SAM3 tracking 过程中自动检测标注框的异常放大、缩小、跳飞、非画幅边缘消失问题。

设计要点:
- 历史中位数基线 + 连续 N 帧滞后 (HYSTERESIS_FRAMES) 抗抖动
- 三级指标 (面积 / 位移 / 宽高比) 各自软硬阈值
- 每个 objectId 独立状态机: NORMAL → WARNING → ANOMALY, 支持 RECOVERY_FRAMES 恢复
- DISAPPEARED 终止态: is_dead 标记后不再对该 oid 检测 (Fix 1 幽灵计数)
- 边缘判定用 bbox 端点 + EDGE_MARGIN_PX=30 (Fix 2)
- hard_score < 1.0 才把 bbox 写进 history, 避免脏数据污染基线 (Fix 3)

零 AI 依赖, 只靠标准库 statistics。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AnomalyLevel(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    ANOMALY = "anomaly"          # 硬异常 → 自动暂停
    DISAPPEARED = "disappeared"  # 非边缘消失 → 暂停


@dataclass
class AnomalyConfig:
    # 历史窗口
    BASELINE_WINDOW: int = 5
    HYSTERESIS_FRAMES: int = 2
    RECOVERY_FRAMES: int = 3
    DISAPPEAR_WARN: int = 2
    DISAPPEAR_ALERT: int = 4

    # 面积 (相对中位数)
    AREA_SHRINK_SOFT: float = 0.6
    AREA_SHRINK_HARD: float = 0.3
    AREA_GROW_SOFT: float = 1.6
    AREA_GROW_HARD: float = 2.5

    # 中心位移 (像素) —— 后面会按 fps 缩放
    CENTER_SHIFT_SOFT: float = 25.0
    CENTER_SHIFT_HARD: float = 40.0

    # 宽高比
    ASPECT_CHANGE_SOFT: float = 1.3
    ASPECT_CHANGE_HARD: float = 1.8

    # 边缘判定 (bbox 端点距画面边缘 px). 用 30px 缓冲区:
    # 精子游速 ~5px/帧 -> 30px 提前 6 帧预知出画 -> 不被误判为非边缘消失
    EDGE_MARGIN_PX: float = 30.0


@dataclass
class _ObjectState:
    """每个 objectId 的跟踪状态机"""
    level: AnomalyLevel = AnomalyLevel.NORMAL
    soft_count: int = 0
    hard_count: int = 0
    recover_count: int = 0
    missing_count: int = 0
    edge_frames: int = 0  # 连续多少帧在边缘

    # Fix 1 (幽灵计数): is_dead 终止态
    # DISAPPEARED 触发后标记 true -> 后续帧不再对该 oid 做任何检测
    # 避免每帧都把同一个 dead oid 加进 disappeared 列表
    is_dead: bool = False
    dead_at_frame: int | None = None

    # 历史 bbox (最多保留 BASELINE_WINDOW + 10 帧)
    history: list[list[float]] = field(default_factory=list)


@dataclass
class AnomalyFrame:
    frame_index: int
    object_id: int
    level: AnomalyLevel
    reasons: list[str]  # ["area_shrink", "center_shift", ...]
    details: dict[str, Any]


@dataclass
class AnomalyReport:
    frame_index: int
    object_levels: dict[int, AnomalyLevel]
    frames: list[AnomalyFrame]   # 本帧的详细报告
    should_pause: bool           # 本帧是否需要暂停


# ── bbox 辅助函数 ────────────────────────────────────────────

def _bbox_wh(bbox: list[float]) -> tuple[float, float]:
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


def _bbox_area(bbox: list[float]) -> float:
    w, h = _bbox_wh(bbox)
    return max(0.0, w * h)


def _bbox_center(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _bbox_aspect(bbox: list[float]) -> float:
    w, h = _bbox_wh(bbox)
    if h <= 0:
        return 1.0
    return w / h


def _median_bbox(history: list[list[float]]) -> list[float] | None:
    if not history:
        return None
    xs1 = statistics.median(b[0] for b in history)
    ys1 = statistics.median(b[1] for b in history)
    xs2 = statistics.median(b[2] for b in history)
    ys2 = statistics.median(b[3] for b in history)
    return [xs1, ys1, xs2, ys2]


# ── 核心检测器 ────────────────────────────────────────────────

class AnomalyDetector:
    def __init__(self, config: AnomalyConfig | None = None,
                 fps: float = 30.0, width: int = 640, height: int = 480):
        self.cfg = config or AnomalyConfig()
        self.fps = fps
        self.width = width
        self.height = height
        self._states: dict[int, _ObjectState] = {}
        self._all_object_ids: set[int] = set()

        # fps 自适应: 阈值按 30fps 为基准缩放
        fps_factor = fps / 30.0 if fps > 0 else 1.0
        self._shift_soft = self.cfg.CENTER_SHIFT_SOFT * fps_factor
        self._shift_hard = self.cfg.CENTER_SHIFT_HARD * fps_factor

    # ------------------------------------------------------------------
    def push(self, frame_index: int, frame_objects: dict[int, list[float]]) -> AnomalyReport:
        """
        frame_objects: {object_id: [x1, y1, x2, y2]}  像素坐标
        """
        # 1. 更新所有已知 objectId 的 missing_count
        current_ids = set(frame_objects.keys())
        disappeared: list[int] = []
        for oid in self._all_object_ids - current_ids:
            state = self._states[oid]

            # Fix 1 (幽灵计数): 已终止的 oid 不再检测
            if state.is_dead:
                continue

            state.missing_count += 1

            # Fix 2: 用 bbox 端点判定边缘, 不是 center
            # bbox 端点触边 -> 真出画 -> 不算异常消失
            at_edge = False
            if state.history:
                x1, y1, x2, y2 = state.history[-1]
                at_edge = (
                    x1 <= self.cfg.EDGE_MARGIN_PX
                    or x2 >= self.width - self.cfg.EDGE_MARGIN_PX
                    or y1 <= self.cfg.EDGE_MARGIN_PX
                    or y2 >= self.height - self.cfg.EDGE_MARGIN_PX
                )

            if state.missing_count >= self.cfg.DISAPPEAR_ALERT and not at_edge:
                state.is_dead = True           # 标记终止, 只报警这一次
                state.dead_at_frame = frame_index
                state.level = AnomalyLevel.DISAPPEARED
                disappeared.append(oid)

        # 2. 对本帧每个 objectId 做检测
        frame_anomalies: list[AnomalyFrame] = []
        object_levels: dict[int, AnomalyLevel] = {}

        for oid, bbox in frame_objects.items():
            self._all_object_ids.add(oid)
            state = self._states.setdefault(oid, _ObjectState())

            # Fix 1 (幽灵计数): 已终止的 oid 不再检测
            if state.is_dead:
                continue

            state.missing_count = 0  # 本帧出现了 → 重置 missing

            # Fix 3 (脏数据污染): 先检测、后写 history
            # 用已有的稳定历史 (不含当前帧) 计算基线
            baseline_start = max(0, len(state.history) - self.cfg.BASELINE_WINDOW)
            baseline_hist = state.history[baseline_start:]  # 历史中最近 BASELINE_WINDOW 帧
            baseline = _median_bbox(baseline_hist)

            reasons: list[str] = []
            soft_score = 0.0  # 0~1 偏离度
            hard_score = 0.0
            area_ratio: float | None = None

            if baseline is not None and _bbox_area(baseline) > 0:
                cur_area = _bbox_area(bbox)
                base_area = _bbox_area(baseline)
                area_ratio = cur_area / base_area

                # 面积检测
                if area_ratio < self.cfg.AREA_SHRINK_HARD or area_ratio > self.cfg.AREA_GROW_HARD:
                    reasons.append("area_extreme")
                    hard_score = max(hard_score, 1.0)
                elif area_ratio < self.cfg.AREA_SHRINK_SOFT or area_ratio > self.cfg.AREA_GROW_SOFT:
                    reasons.append("area_unusual")
                    soft_score = max(soft_score, 0.7)

                # 位移检测
                cur_cx, cur_cy = _bbox_center(bbox)
                base_cx, base_cy = _bbox_center(baseline)
                dist = ((cur_cx - base_cx) ** 2 + (cur_cy - base_cy) ** 2) ** 0.5
                if dist >= self._shift_hard:
                    reasons.append("center_shift_large")
                    hard_score = max(hard_score, 1.0)
                elif dist >= self._shift_soft:
                    reasons.append("center_shift")
                    soft_score = max(soft_score, 0.7)

                # 宽高比检测
                cur_ar = _bbox_aspect(bbox)
                base_ar = _bbox_aspect(baseline)
                if base_ar > 0:
                    ar_ratio = max(cur_ar, base_ar) / min(cur_ar, base_ar)
                    if ar_ratio >= self.cfg.ASPECT_CHANGE_HARD:
                        reasons.append("aspect_extreme")
                        hard_score = max(hard_score, 1.0)
                    elif ar_ratio >= self.cfg.ASPECT_CHANGE_SOFT:
                        reasons.append("aspect_unusual")
                        soft_score = max(soft_score, 0.7)

            # 状态机更新
            if hard_score > 0:
                state.hard_count += 1
                state.soft_count = 0
                state.recover_count = 0
            elif soft_score > 0:
                state.soft_count += 1
                state.recover_count = 0
            else:
                state.recover_count += 1
                state.soft_count = max(0, state.soft_count - 1)
                state.hard_count = max(0, state.hard_count - 1)

            # 升级
            if state.hard_count >= self.cfg.HYSTERESIS_FRAMES:
                state.level = AnomalyLevel.ANOMALY
            elif state.soft_count >= self.cfg.HYSTERESIS_FRAMES and state.level == AnomalyLevel.NORMAL:
                state.level = AnomalyLevel.WARNING
            # 恢复 (DISAPPEARED 是终止态, 不参与恢复)
            if (state.recover_count >= self.cfg.RECOVERY_FRAMES
                    and state.level not in (AnomalyLevel.DISAPPEARED,)):
                state.level = AnomalyLevel.NORMAL

            # Fix 3 (脏数据): 只有当本帧没被判 HARD 时才把 bbox 写进历史
            # 否则异常 bbox 会污染后续中位数基线
            if hard_score < 1.0:
                state.history.append(list(bbox))
                if len(state.history) > self.cfg.BASELINE_WINDOW + 10:
                    state.history = state.history[-(self.cfg.BASELINE_WINDOW + 10):]

            object_levels[oid] = state.level

            if state.level != AnomalyLevel.NORMAL or reasons:
                frame_anomalies.append(AnomalyFrame(
                    frame_index=frame_index,
                    object_id=oid,
                    level=state.level,
                    reasons=reasons,
                    details={
                        "bbox": [round(v, 2) for v in bbox],
                        "baseline": [round(v, 2) for v in baseline] if baseline else None,
                        "area_ratio": round(area_ratio, 3) if area_ratio is not None else None,
                    },
                ))

        # 把 DISAPPEARED 的也加进去
        for oid in disappeared:
            state = self._states[oid]
            object_levels[oid] = AnomalyLevel.DISAPPEARED
            frame_anomalies.append(AnomalyFrame(
                frame_index=frame_index,
                object_id=oid,
                level=AnomalyLevel.DISAPPEARED,
                reasons=["non_edge_disappearance"],
                details={},
            ))

        should_pause = any(
            lvl in (AnomalyLevel.ANOMALY, AnomalyLevel.DISAPPEARED)
            for lvl in object_levels.values()
        )

        return AnomalyReport(
            frame_index=frame_index,
            object_levels=object_levels,
            frames=frame_anomalies,
            should_pause=should_pause,
        )

    # ------------------------------------------------------------------
    def scan_history(self, rows: list[dict]) -> AnomalyReport:
        """对已完成的 tracking 结果做事后扫描"""
        self._states.clear()
        self._all_object_ids.clear()

        last_report: AnomalyReport | None = None
        for row in rows:
            fi = int(row.get("frame_index", 0))
            frame_objs: dict[int, list[float]] = {}
            for obj in row.get("objects", []):
                oid = int(obj.get("object_id", 0))
                frame_objs[oid] = list(obj.get("bbox", [0, 0, 0, 0]))
            last_report = self.push(fi, frame_objs)

        return last_report or AnomalyReport(0, {}, [], False)
