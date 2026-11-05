"""Authenticated training export jobs; clients cannot supply annotation geometry."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import Field, model_validator

from . import training_export as export
from .auth import current_download_user
from .review_workflow import ReviewError
from .api_common import Key, LoggedRoute, Model, User, AnnotationComplete

router = APIRouter(prefix="/api/datasets", tags=["datasets"], route_class=LoggedRoute)
legacy_router = APIRouter(tags=["datasets"], route_class=LoggedRoute)


class Selection(Model):
    finalVersionIds: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        min_length=1, max_length=100
    )


Split = Literal["train", "val", "test"]


class SplitOptions(Model):
    splitRatio: float = Field(default=0.8, gt=0, lt=1, strict=True)
    # Omitted fields keep the legacy frame-order meaning for older clients and stored retries.
    splitMode: Literal["frame", "video"] = "frame"
    testRatio: float = Field(default=0.0, ge=0, lt=1, strict=True)
    videoSplits: dict[str, Split] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def _ratios(self):
        if self.splitRatio + self.testRatio >= 1:
            raise ValueError("训练与测试比例之和必须小于 1，验证集不能为空")
        if self.splitMode == "frame" and (self.testRatio or self.videoSplits):
            raise ValueError("按帧划分不支持测试集或手动指定视频划分")
        return self


NEW_FIELDS = ("splitMode", "testRatio", "videoSplits", "includeSourceVideos")


def settings(body):
    # Unset new fields stay absent so request hashes from before this option remain stable.
    data = body.model_dump()
    for name in NEW_FIELDS:
        if name in data and name not in body.model_fields_set:
            data.pop(name)
    return data


class PreviewBody(Selection, SplitOptions):
    pass


class Export(Selection, SplitOptions):
    format: Literal["yolo", "coco", "both"] = "yolo"
    includeSourceVideos: bool = Field(default=False, strict=True)


@router.post("/exports/preview")
def preview(body: PreviewBody, user: User):
    from contextlib import closing
    from .db import connect
    from .annotation_export import authorize
    with closing(connect()) as c: authorize(c, body.finalVersionIds, user["uid"])
    return export.preview(body.finalVersionIds, settings(body))


@router.post("/exports")
def create(body: Export, user: User, key: Key):
    return export.create(user["uid"], key, settings(body))


@router.get("/annotations/snapshots")
def annotation_snapshots(user: User):
    from .annotation_export import latest
    return {"items": latest(user["uid"])}


@router.get("/exports/{eid}")
def status(eid: str, user: User):
    return export.status(eid, user["uid"])


@router.get("/exports/{eid}/download")
def download(eid: str, user: Annotated[dict, Depends(current_download_user)]):
    path = export.download(eid, user["uid"])
    # No Content-Disposition name: it would override the readable name the page's link asks for.
    return FileResponse(path, media_type="application/zip")


@legacy_router.post("/api/export/dataset")
def retired(user: User):
    raise ReviewError(
        "FINAL_CONFIRMATION_REQUIRED",
        "旧导出入口已停用。请在标注页审查通过后导出训练数据集。",
        410,
    )


@router.post('/annotations/{media_id}/snapshot')
def annotation_snapshot(media_id: str, body: AnnotationComplete, user: User, key: Key):
    from .annotation_export import freeze, replay
    from .api_common import completion_context
    from .media_reimport import source_sha
    from .review_source_lock import source_read
    prior=replay(user['uid'],key,media_id,body.model_dump())
    if prior is not None: return prior
    directory, video, _ = completion_context(media_id)
    sha = source_sha(directory, video)  # May read the whole video: never under the shared lock.
    with source_read():  # Waits briefly for a colleague's save instead of failing the export.
        directory, video, info = completion_context(media_id)
        return freeze(directory, video, media_id, user['uid'], key, body.model_dump(), info, sha)


@router.get('/annotations/{media_id}/preview')
def annotation_preview(media_id: str, user: User):
    """Frame coverage for the export dialog, with frames the passed review confirmed empty."""
    from contextlib import closing
    from .annotation_completion import source, ranges
    from .annotation_review import last_passed, confirmed_empty
    from .api_common import completion_context
    from .db import connect
    from .review_source_lock import source_read
    with source_read():
        directory, _, info = completion_context(media_id)
        revision, frames = source(directory, user['uid'], info, require_author=False)
    with closing(connect()) as c:
        passed = last_passed(c, media_id)
        empty = confirmed_empty(c, passed['id']) if passed else set()
    missing = [i for i in range(info['frameCount']) if i not in frames and i not in empty]
    return dict(sourceRevision=revision, frameCount=info['frameCount'],
                objectFrames=sum(bool(x) for x in frames.values()),
                emptyFrames=sum(not x for x in frames.values()) + len(empty - set(frames)),
                unknownFrames=len(missing), unknownFrameRanges=ranges(missing))
