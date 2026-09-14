from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from .config import TRACK_FRAMES


class AuthRequest(BaseModel):
    username: str
    password: str


class ManualAnnotationRequest(BaseModel):
    mediaId: str
    mediaType: str | None = None
    mediaName: str | None = None
    mediaWidth: float | None = None
    mediaHeight: float | None = None
    frameIndex: int = 0
    timestampMs: float = 0
    objects: list[dict[str, Any]]
    annotationVersion: str | None = None


class TrackRequest(BaseModel):
    mediaId: str
    mediaName: str | None = None
    mediaWidth: float | None = None
    mediaHeight: float | None = None
    startFrame: int = Field(ge=0)
    maxFrames: int = Field(default=TRACK_FRAMES, ge=1, le=TRACK_FRAMES)
    annotations: list[dict[str, Any]]


def jsonable_bbox(bbox: list[float]) -> list[float]:
    return [round(float(v), 3) for v in bbox]


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
