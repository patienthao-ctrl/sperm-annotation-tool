# 异常帧检测 (Anomaly Detector) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 SAM3 Tracking 过程中自动检测标注框的异常放大、缩小、跳飞、非画幅边缘消失问题，发现后暂停 tracking 让用户修正，且不因单帧抖动频繁误触发。

**Architecture:** 后端新建 `anomaly_detector.py` 作为纯数学检测引擎（零 AI 依赖），在 `track_video()` 逐帧循环中嵌入调用。使用「历史中位数基线 + 连续 N 帧滞后」抗抖动，输出三种严重度（NORMAL / WARNING / ANOMALY）。后端通过 task status 把异常信息推给前端，前端高亮异常帧并自动暂停。同时提供「对已有 tracker_results.json 做事后检测」的离线 API。

**Tech Stack:** Python 3.11+, NumPy (只算中位数), FastAPI (已有), 前端 Vue 3 + TS (已有)

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/app/services/anomaly_detector.py` | **CREATE** | 纯数学检测引擎，核心类 `AnomalyDetector` + `AnomalyConfig` |
| `backend/app/tracker.py` | MODIFY | 在 `track_video()` 的逐帧循环中嵌入检测调用 |
| `backend/app/main.py` | MODIFY | 新增 `POST /api/anomaly/scan` 离线扫描端点 |
| `backend/app/schemas.py` | MODIFY | 新增 `AnomalyReport` / `AnomalyFrame` schema |
| `frontend/src/stores/workspace.ts` | MODIFY | 把 task status 里的异常数据转成 UI 状态；异常时 auto-pause |
| `frontend/src/api/trackApi.ts` | MODIFY | `getStatus` 返回值增加 anomaly 字段 |
| `frontend/src/pages/AnnotatePage.vue` | MODIFY | 时间轴异常高亮 + 暂停弹窗 |
| `backend/tests/test_anomaly_detector.py` | **CREATE** | 纯单元测试（mock 数据，不依赖视频/SAM3） |

---

## Task 1: 后端核心检测引擎 (anomaly_detector.py)

**Files:**
- Create: `backend/app/services/anomaly_detector.py`
- Create: `backend/tests/test_anomaly_detector.py`

### 检测器设计

```
输入: 每帧的 bbox 列表 (object_id → [x1,y1,x2,y2])
      video_width, video_height, fps
输出: AnomalyReport (逐帧逐 objectId 的异常状态 + 汇总)
```

### 三级指标 + 滞后机制

```
基线 = 过去 BASELINE_WINDOW 帧的 bbox 中位数 (不含当前帧)
偏离度 = 当前帧 bbox 与基线的差异分数

状态机 (每个 objectId 独立):
  NORMAL → soft_count++, recover_count=0  (severity >= SOFT_THRESHOLD 持续)
  SOFT   → hard_count++, recover_count=0  (severity >= HARD_THRESHOLD 持续)
  HARD   → recover_count=0                 (连续 HYSTERESIS_FRAMES 帧)
  
  恢复: severity < SOFT_THRESHOLD 持续 RECOVERY_FRAMES 帧 → 降级
```

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_anomaly_detector.py
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.anomaly_detector import AnomalyDetector, AnomalyConfig, AnomalyLevel


def _bbox(area=200, cx=320, cy=216, aspect=2.5):
    """生成一个面积 area、中心 (cx,cy)、宽高比 aspect 的 bbox"""
    h = (area / aspect) ** 0.5
    w = area / h
    x1, y1 = cx - w/2, cy - h/2
    return [x1, y1, x1 + w, y1 + h]


def test_normal_frames_no_anomaly():
    """精子连续正常游动 20 帧，不应触发异常"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)
    
    reports = []
    for i in range(20):
        frame_objects = {1: _bbox(area=200 + i*2, cx=320 + i*3, cy=216 + i, aspect=2.5)}
        report = det.push(frame_index=i, frame_objects=frame_objects)
        reports.append(report)
    
    # 最后一帧应该全正常
    last = reports[-1]
    for oid, level in last.object_levels.items():
        assert level == AnomalyLevel.NORMAL, f"object {oid} should be NORMAL, got {level}"


def test_area_shrink_hard_triggers_anomaly():
    """面积缩到 30% 以下 → HARD 异常"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)
    
    # 先喂 5 帧稳定基线
    for i in range(5):
        det.push(i, {1: _bbox(area=200)})
    
    # 突然缩到 50 (25% of 200, < 30%)
    levels_after_shrink = []
    for i in range(5, 15):
        report = det.push(i, {1: _bbox(area=50)})
        levels_after_shrink.append(report.object_levels.get(1))
    
    # 连续 HYSTERESIS_FRAMES 帧后应变成 HARD
    assert AnomalyLevel.HARD in levels_after_shrink


def test_single_jitter_does_not_trigger():
    """单帧抖动后立即恢复 → 不应触发异常"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)
    
    for i in range(5):
        det.push(i, {1: _bbox(area=200)})
    
    # 第 5 帧抖一下
    det.push(5, {1: _bbox(area=50)})
    # 第 6 帧恢复
    det.push(6, {1: _bbox(area=200)})
    
    # 应该还是 NORMAL
    assert det.push(7, {1: _bbox(area=200)}).object_levels.get(1) == AnomalyLevel.NORMAL


def test_disappearance_triggers_after_threshold():
    """连续 DISAPPEAR_FRAMES 帧 objectId 不存在 → 标记丢失"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)
    
    for i in range(10):
        det.push(i, {1: _bbox(area=200)})
    
    # objectId=1 从 frame 10 开始消失
    levels = []
    for i in range(10, 15):
        report = det.push(i, {2: _bbox(area=200)})
        levels.append(report.object_levels.get(1))
    
    # 第 14 帧 (frame 14 = 消失第 4 帧) 应该有 DISAPPEARED
    assert AnomalyLevel.DISAPPEARED in levels


def test_edge_disappearance_is_ok():
    """精子消失但 bbox 端点贴近边缘 -> 不算异常（真出画了）"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)
    
    for i in range(10):
        # bbox 左端点 608 <= EDGE_MARGIN_PX(30) -> 算贴边
        det.push(i, {1: [608, 209, 625, 223]})
    
    # 从 frame 10 开始消失
    for i in range(10, 20):
        report = det.push(i, {})
    
    # 消失帧不应触发（因为 bbox 端点贴边）
    assert report.object_levels.get(1) != AnomalyLevel.DISAPPEARED


def test_ghost_counting_prevention():
    """Fix 1 回归: DISAPPEARED 触发后不应每帧重复报同一 oid"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)
    
    for i in range(10):
        det.push(i, {1: _bbox(area=200)})
    
    # objectId=1 从 frame 10 开始消失
    disappeared_per_frame = []
    for i in range(10, 20):
        report = det.push(i, {2: _bbox(area=200)})
        count = sum(1 for lvl in report.object_levels.values()
                    if lvl == AnomalyLevel.DISAPPEARED)
        disappeared_per_frame.append(count)
    
    # DISAPPEARED 只在触发那一帧出现一次
    triggered_frames = [i+10 for i, c in enumerate(disappeared_per_frame) if c > 0]
    assert len(triggered_frames) == 1, f"DISAPPEARED should fire once, got frames={triggered_frames}"


def test_hard_anomaly_does_not_pollute_baseline():
    """Fix 3 回归: HARD anomaly 的 bbox 不进 history"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)
    
    # 5 帧稳定基线
    for i in range(5):
        det.push(i, {1: _bbox(area=200)})
    
    # frame 5-7: HARD 异常 (面积缩到 20%)
    for i in range(5, 8):
        det.push(i, {1: _bbox(area=40)})
    
    # frame 8-12: 恢复正常
    for i in range(8, 13):
        report = det.push(i, {1: _bbox(area=200)})
    
    # history 里不应该有 area=40 的脏数据
    state = det._states[1]
    for bbox in state.history:
        assert _bbox_area(bbox) >= 100, f"dirty bbox in history: {bbox}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_anomaly_detector.py -v 2>&1 | head -30`
Expected: `ModuleNotFoundError` 或 `ImportError`（anomaly_detector.py 还没写）

- [ ] **Step 3: 写最小实现让测试通过**

```python
# backend/app/services/anomaly_detector.py
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import statistics
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

    # 中心位移 (像素) — 后面会按 fps 缩放
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


def _bbox_wh(bbox: list[float]) -> tuple[float, float]:
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])

def _bbox_area(bbox: list[float]) -> float:
    w, h = _bbox_wh(bbox)
    return max(0.0, w * h)

def _bbox_center(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

def _bbox_aspect(bbox: list[float]) -> float:
    w, h = _bbox_wh(bbox)
    if h <= 0: return 1.0
    return w / h

def _median_bbox(history: list[list[float]]) -> list[float] | None:
    if not history:
        return None
    xs1 = statistics.median(b[0] for b in history)
    ys1 = statistics.median(b[1] for b in history)
    xs2 = statistics.median(b[2] for b in history)
    ys2 = statistics.median(b[3] for b in history)
    return [xs1, ys1, xs2, ys2]


class AnomalyDetector:
    def __init__(self, config: AnomalyConfig | None = None,
                 fps: float = 30.0, width: int = 640, height: int = 480):
        self.cfg = config or AnomalyConfig()
        self.fps = fps
        self.width = width
        self.height = height
        self._states: dict[int, _ObjectState] = {}
        self._all_object_ids: set[int] = set()

        # fps 自适应：阈值按 30fps 为基准缩放
        fps_factor = fps / 30.0 if fps > 0 else 1.0
        self._shift_soft = self.cfg.CENTER_SHIFT_SOFT * fps_factor
        self._shift_hard = self.cfg.CENTER_SHIFT_HARD * fps_factor

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
            # Fix 2: 用 bbox 端点判定边缘，不是 center
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
                state.is_dead = True           # 标记终止，只报警这一次
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
            # 恢复 (DISAPPEARED 是终止态，不参与恢复)
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
                        "area_ratio": round(area_ratio, 3) if baseline else None,
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_anomaly_detector.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd backend
git add services/anomaly_detector.py tests/test_anomaly_detector.py
git commit -m "feat(anomaly): add AnomalyDetector engine with hysteresis-based state machine"
```

---

## Task 2: 集成到 tracking 管线 (tracker.py + main.py)

**Files:**
- Modify: `backend/app/tracker.py:168-212` (track_video 逐帧循环)
- Modify: `backend/app/main.py` (新增 scan endpoint)
- Modify: `backend/app/schemas.py` (新增 schema)

- [ ] **Step 1: 在 tracker.py 的逐帧循环中嵌入检测**

```python
# 在 track_video() 函数开头，engine 初始化之后加：
from .services.anomaly_detector import AnomalyDetector, AnomalyConfig, AnomalyLevel

# 在 session = engine.make_tracker_session(frames) 之后加：
detector = AnomalyDetector(
    config=AnomalyConfig(),
    fps=source_fps,
    width=width,
    height=height,
)

# 在 for output in engine.propagate_manual(...) 循环内，
# new_rows.append(row) 之后加：

            # ── 异常检测 ──
            frame_objs_for_detector: dict[int, list[float]] = {}
            for obj_row in object_rows:
                oid = int(obj_row["object_id"])
                frame_objs_for_detector[oid] = list(obj_row["bbox"])
            anomaly_report = detector.push(frame_idx, frame_objs_for_detector)

            # 如果检测到 HARD 异常 → 提前终止 tracking
            if anomaly_report.should_pause:
                pause_reasons = []
                for af in anomaly_report.frames:
                    if af.level in (AnomalyLevel.ANOMALY, AnomalyLevel.DISAPPEARED):
                        pause_reasons.append(
                            f"object #{af.object_id}: {', '.join(af.reasons)}"
                        )
                print(f"[anomaly] HARD 检测到 → 暂停 frame={frame_idx}: {pause_reasons}")
                # 把异常信息写进每个 row (便于前端展示)
                for row_anom in anomaly_report.frames:
                    row.setdefault("anomalies", []).append({
                        "object_id": row_anom.object_id,
                        "level": row_anom.level.value,
                        "reasons": row_anom.reasons,
                    })
                result["anomaly_paused"] = {
                    "frame_index": frame_idx,
                    "reasons": pause_reasons,
                    "levels": {str(k): v.value for k, v in anomaly_report.object_levels.items()},
                }
                break  # ← 暂停! 终止后续帧 propagation

        # 循环结束后，如果是 anomaly_paused → 不再继续
        # 函数末尾 result dict 已经包含 anomaly_paused 了
```

- [ ] **Step 2: main.py schema + 端点**

在 `backend/app/schemas.py` 末尾加：
```python
class AnomalyScanRequest(BaseModel):
    mediaId: str
    scanExistingTracker: bool = True  # 扫描已有的 tracker_results.json

class AnomalyFrameOut(BaseModel):
    frame_index: int
    object_id: int
    level: str          # "normal" | "warning" | "anomaly" | "disappeared"
    reasons: list[str]
    details: dict[str, Any] = {}

class AnomalyScanResponse(BaseModel):
    mediaId: str
    totalFrames: int
    anomalyFrames: list[AnomalyFrameOut]
    summary: dict[str, int]  # {"anomaly": 3, "warning": 5, "disappeared": 2}
    shouldPauseAt: int | None = None  # 建议在哪一帧暂停
```

在 `backend/app/main.py` 加端点：
```python
@app.post("/api/anomaly/scan", response_model=AnomalyScanResponse)
def scan_anomalies(req: AnomalyScanRequest, user: dict[str, Any] = Depends(current_user)):
    """对已有 tracker_results.json 做事后异常扫描"""
    from .services.anomaly_detector import AnomalyDetector, AnomalyConfig

    directory = resolve_media_dir(req.mediaId)
    if not directory:
        raise HTTPException(404, f"media {req.mediaId} not found")

    # 读 video meta
    mj = directory / "media.json"
    video_meta = {}
    if mj.is_file():
        video_meta = json.loads(mj.read_text())

    cap_meta = _probe_video(directory / video_meta.get("videoName", ""))
    width = cap_meta["width"]
    height = cap_meta["height"]
    fps = cap_meta["fps"]

    # 读 tracker_results.json (JSONL)
    tracker_file = directory / "tracker_results.json"
    rows = _read_jsonl(tracker_file) if tracker_file.is_file() else []

    detector = AnomalyDetector(config=AnomalyConfig(), fps=fps, width=width, height=height)
    all_frames: list[AnomalyFrameOut] = []
    summary: dict[str, int] = {"anomaly": 0, "warning": 0, "disappeared": 0}
    pause_at: int | None = None

    for row in rows:
        fi = int(row.get("frame_index", 0))
        frame_objs: dict[int, list[float]] = {}
        for obj in row.get("objects", []):
            frame_objs[int(obj["object_id"])] = list(obj["bbox"])
        report = detector.push(fi, frame_objs)

        for af in report.frames:
            all_frames.append(AnomalyFrameOut(
                frame_index=af.frame_index,
                object_id=af.object_id,
                level=af.level.value,
                reasons=af.reasons,
                details=af.details,
            ))
            if af.level.value in summary:
                summary[af.level.value] += 1

        if report.should_pause and pause_at is None:
            pause_at = fi

    return AnomalyScanResponse(
        mediaId=req.mediaId,
        totalFrames=len(rows),
        anomalyFrames=all_frames,
        summary=summary,
        shouldPauseAt=pause_at,
    )
```

- [ ] **Step 3: 测试异常暂停能否在 tracking 中触发**

Run: `cd backend && python -c "
from app.services.anomaly_detector import AnomalyDetector, AnomalyConfig, AnomalyLevel

# 模拟 15 帧正常 + 第 16 帧突然 area 缩到 10%
cfg = AnomalyConfig()
det = AnomalyDetector(cfg, fps=15, width=640, height=432)

for i in range(15):
    det.push(i, {1: [300, 200, 340, 220]})

# 突然缩到很小
hard_triggered = False
for i in range(15, 20):
    r = det.push(i, {1: [310, 205, 315, 207]})  # 5x2 超小 bbox
    print(f'frame {i}: level={r.object_levels.get(1)} should_pause={r.should_pause}')
    if r.should_pause:
        hard_triggered = True
        break

assert hard_triggered, 'HARD anomaly should trigger pause'
print('✅ HARD anomaly triggers pause correctly')
"
`
Expected: ✅ HARD anomaly triggers pause correctly

- [ ] **Step 4: 跑现有测试/启动确认不炸**

Run: `cd backend && python -m pytest tests/ -v 2>&1 | tail -10`
Expected: 所有测试 pass

Run: `cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 3000`
Expected: 正常启动

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/tracker.py app/main.py app/schemas.py
git commit -m "feat(tracker): integrate AnomalyDetector into tracking loop + add /api/anomaly/scan endpoint"
```

---

## Task 3: 前端集成（高亮 + 自动暂停）

**Files:**
- Modify: `frontend/src/stores/workspace.ts`
- Modify: `frontend/src/api/trackApi.ts`
- Modify: `frontend/src/pages/AnnotatePage.vue`

- [ ] **Step 1: trackApi 返回值扩展**

```typescript
// frontend/src/api/trackApi.ts
// getStatus 返回增加 anomaly 字段
interface TaskStatus {
  taskId: string
  status: "queued" | "running" | "success" | "failed" | "paused"
  progress?: number
  frameIndex?: number
  message?: string
  // 新增:
  anomaly_paused?: {
    frame_index: number
    reasons: string[]
    levels: Record<string, string>
  }
}
```

- [ ] **Step 2: workspace.ts 处理 anomaly_paused**

在 `runAiTrack` 的 `for(;;)` 轮询循环里加：
```typescript
const status = await trackApi.getStatus(task.taskId)
if (status.status === 'success') break
if (status.status === 'failed') throw new Error(status.message || 'Tracking 失败')

// ── 新增：自动暂停处理 ──
if (status.anomaly_paused) {
  isAiBusy.value = false
  const pauseFrame = status.anomaly_paused.frame_index
  const reasons = status.anomaly_paused.reasons
  // 跳到异常帧
  await seekVideo(frameToTime(pauseFrame))
  // 高亮异常 objectId
  anomalyObjectIds.value = Object.entries(status.anomaly_paused.levels || {})
    .filter(([, v]) => v !== 'normal')
    .map(([k]) => Number(k))
  statusMessage.value = `⚠️ Tracking 暂停 @ frame ${pauseFrame}: ${reasons.join('; ')}`
  showToast(`检测到异常，已暂停在第 ${pauseFrame} 帧`)
  break  // 跳出轮询
}

await new Promise(resolve => setTimeout(resolve, 700))
```

- [ ] **Step 3: AnnotatePage.vue 时间轴异常高亮**

```vue
<!-- 在时间轴 frame 滑块/列表上加红色标记 -->
<div class="timeline-wrapper">
  <input type="range" :max="maxFrameIndex" v-model="currentFrame" />
  <div class="anomaly-markers">
    <span
      v-for="anom in anomalyFrames"
      :key="anom.frame_index"
      :class="['anom-marker', anom.level]"
      :style="{ left: (anom.frame_index / maxFrameIndex * 100) + '%' }"
      :title="`frame ${anom.frame_index}: ${anom.reasons.join(', ')}`"
    ></span>
  </div>
</div>
```

```css
.anomaly-markers { position: relative; height: 20px; }
.anom-marker { position: absolute; width: 4px; height: 100%; top: 0; border-radius: 2px; }
.anom-marker.anomaly { background: #ef4444; }
.anom-marker.warning { background: #f59e0b; }
.anom-marker.disappeared { background: #8b5cf6; }
```

- [ ] **Step 4: Commit**

```bash
cd frontend
git add src/stores/workspace.ts src/api/trackApi.ts src/pages/AnnotatePage.vue
git commit -m "feat(frontend): auto-pause on anomaly + timeline markers + highlight"
```

---

## Task 4: 校准与端到端测试

**Files:** (无新增)

- [ ] **Step 1: 用真实视频跑 tracking，验证 anomaly 不敏感**

Run: 在前端选 `790663...mp4`，标种子帧 → 跑 AI Tracking → 观察是否在正常帧暂停

Expected: 
- 正常 tracking 10 帧不应触发暂停
- SAM3 偶发 bbox 抖动（±2px）不应触发暂停
- 统计 HARD 触发率 < 5%（理想情况 0%）

- [ ] **Step 2: 手动造异常场景，验证能触发**

```python
# 造一个 SAM3 从 frame 10 开始把 bbox 放大 10 倍的 bug 场景
# 方式：跑 tracking → 用 /api/anomaly/scan 扫描 tracker_results.json
# 在 frame 10 之前手工修改 tracker_results.json 里的 bbox
# 然后调 scan endpoint 验证能检测到
```

Expected: scan 返回 shouldPauseAt=10，summary.anomaly >= 1

- [ ] **Step 3: 根据真实视频调参**

观察 1-2 条真实视频的 tracking 情况，调整以下阈值：
- `CENTER_SHIFT_HARD`：如果正常精子游速就有 40px/帧 → 调到 55
- `AREA_GROW_HARD`：如果精子交错面积会涨到 200% → 调到 3.0
- 每改一次 → 跑一遍 tracking 看触发率

- [ ] **Step 4: Commit 最终调参**

```bash
cd backend
git add app/services/anomaly_detector.py
git commit -m "chore(anomaly): tune thresholds on real sperm videos"
```

---

## 自检

**1. Spec 覆盖检查：**
- [x] 异常放大缩小 → `AREA_SHRINK_*` / `AREA_GROW_*`
- [x] 跳飞 (center shift) → `CENTER_SHIFT_*`
- [x] 非边缘消失 → `DISAPPEAR_*` + EDGE_MARGIN 排除边缘
- [x] 自动暂停 → `track_video` 循环里 break + `should_pause`
- [x] 不敏感 → 中位数基线 + 滞后计数器 + RECOVERY_FRAMES
- [x] 前端能看到 → timeline markers + anomalyObjectIds 高亮 + 暂停弹窗
- [x] 事后扫描 → `/api/anomaly/scan` 端点

**2. Placeholder 扫描：** 无 TBD/TODO

**3. 类型一致性：** `AnomalyLevel` (Enum) 在 tracker.py / main.py / 前端 .ts 里统一用 value 字符串；bbox 统一用 `[x1, y1, x2, y2]` 像素格式

**4. 修复项清单（评审后采纳）：**
- [x] **Fix 1 幽灵计数**: `_ObjectState.is_dead` 终止态 + DISAPPEARED 仅触发一次
- [x] **Fix 2 边缘判定**: 用 bbox 端点 (x1,x2,y1,y2) 而非 center + EDGE_MARGIN_PX 15->30
- [x] **Fix 3 脏数据污染**: `hard_score < 1.0` 才写 history，检测时只用稳定历史做基线
- [x] **Fix 4 断点续跑**: `track_video()` 每次调用新建 detector（已天然支持），加注释说明
- [x] **Fix 5 多目标单异常打断**: Phase 1 在前端明确提示是哪只精子 (objectId) 出了问题；Phase 2「忽略该目标」作为后续优化
