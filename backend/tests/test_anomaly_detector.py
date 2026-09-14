"""anomaly_detector 单元测试 —— 纯 mock 数据, 不依赖视频 / SAM3"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.services.anomaly_detector import (  # noqa: E402
    AnomalyConfig,
    AnomalyDetector,
    AnomalyLevel,
    _bbox_area,
)


def _bbox(area: float = 200, cx: float = 320, cy: float = 216, aspect: float = 2.5) -> list[float]:
    """生成一个面积 area、中心 (cx,cy)、宽高比 aspect 的 bbox"""
    h = (area / aspect) ** 0.5
    w = area / h
    x1, y1 = cx - w / 2, cy - h / 2
    return [x1, y1, x1 + w, y1 + h]


# ── 5 个基础测试 ────────────────────────────────────────────────

def test_normal_frames_no_anomaly():
    """精子连续正常游动 20 帧, 不应触发异常"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)

    reports = []
    for i in range(20):
        frame_objects = {1: _bbox(area=200 + i * 2, cx=320 + i * 3, cy=216 + i, aspect=2.5)}
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

    # 连续 HYSTERESIS_FRAMES 帧后应变成 HARD (ANOMALY)
    assert AnomalyLevel.ANOMALY in levels_after_shrink, \
        f"Expected ANOMALY in levels, got {levels_after_shrink}"


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
    """连续 DISAPPEAR_ALERT 帧 objectId 不存在 → 标记丢失"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)

    for i in range(10):
        det.push(i, {1: _bbox(area=200)})

    # objectId=1 从 frame 10 开始消失
    levels = []
    for i in range(10, 15):
        report = det.push(i, {2: _bbox(area=200)})
        levels.append(report.object_levels.get(1))

    # 第 14 帧 (frame 14 = 消失第 4 帧, DISAPPEAR_ALERT=4) 应该有 DISAPPEARED
    assert AnomalyLevel.DISAPPEARED in levels, \
        f"Expected DISAPPEARED, got levels={levels}"


def test_edge_disappearance_is_ok():
    """精子消失但 bbox 端点贴近边缘 -> 不算异常 (真出画了)"""
    cfg = AnomalyConfig()
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)

    for i in range(10):
        # bbox 右端点 625 >= 640-30=610 -> 算贴边 (Fix 2: 用端点 + 30px margin)
        det.push(i, {1: [608, 209, 625, 223]})

    # 从 frame 10 开始消失
    report = None
    for i in range(10, 20):
        report = det.push(i, {})

    # 消失帧不应触发 (因为 bbox 端点贴边)
    assert report.object_levels.get(1) != AnomalyLevel.DISAPPEARED, \
        f"Edge disappearance should NOT trigger DISAPPEARED, got {report.object_levels}"


# ── 3 个回归测试 (评审 Fix 1 / 3) ───────────────────────────────

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
    triggered_frames = [i + 10 for i, c in enumerate(disappeared_per_frame) if c > 0]
    assert len(triggered_frames) == 1, \
        f"DISAPPEARED should fire once, got frames={triggered_frames}"


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
        area = _bbox_area(bbox)
        assert area >= 100, f"dirty bbox in history (area={area}): {bbox}"


def test_fix2_edge_margin_is_30px():
    """Fix 2 回归: EDGE_MARGIN_PX 必须是 30 且用端点判定"""
    cfg = AnomalyConfig()
    assert cfg.EDGE_MARGIN_PX == 30.0, \
        f"EDGE_MARGIN_PX should be 30.0, got {cfg.EDGE_MARGIN_PX}"

    # 测试: bbox 端点触边 (x2 >= width-30) 算 edge, 即使 center 不在边缘
    det = AnomalyDetector(cfg, fps=15, width=640, height=432)

    for i in range(10):
        # bbox x2=625 >= 610 (640-30) -> 贴边
        # 但 center 在 (608+625)/2=616.5, 如果用 center 判定会漏掉
        det.push(i, {1: [608, 209, 625, 223]})

    # 消失后不应触发 DISAPPEARED
    for i in range(10, 15):
        report = det.push(i, {})
    assert report.object_levels.get(1) != AnomalyLevel.DISAPPEARED
