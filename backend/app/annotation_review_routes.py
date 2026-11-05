"""Annotation review frame checklist, pass and media reset routes."""

from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from . import annotation_review as review
from .api_common import Key, LoggedRoute, Model, User, media_context
from .review_source_lock import source_read
from .review_workflow import ReviewError

router = APIRouter(prefix="/api/annotation-review", tags=["annotation-review"], route_class=LoggedRoute)


class RoundRef(Model):
    reviewId: str = Field(min_length=1, max_length=64)


class Check(RoundRef):
    result: Literal["ok", "empty"]


class ResetAnnotations(Model):
    expectedRevision: int = Field(strict=True, ge=0)
    confirmDiscard: bool


@router.get("/summary")
def summary(user: User):
    from .main import iter_media_dirs
    directories = {d.name: d for d in iter_media_dirs()}
    items = review.summary(list(directories.items()))
    return {"items": items, "stages": {m: review.stage(directories[m]) for m, s in items.items() if s == "none"}}


@router.get("/media/{media_id}")
def status(media_id: str, user: User):
    with source_read():
        directory, _, info = media_context(media_id, allow_tracking=True)
        return review.status(media_id, directory, info)


@router.post("/media/{media_id}/start")
def start(media_id: str, user: User):
    with source_read():
        directory, _, info = media_context(media_id)
        review.start(media_id, directory, info, user["uid"])
        return review.status(media_id, directory, info)


@router.put("/media/{media_id}/frames/{frame_index}")
def check(media_id: str, frame_index: int, body: Check, user: User):
    with source_read():
        directory, _, info = media_context(media_id, allow_tracking=True)
        current = review.load_cached(media_id, directory, info)
    if not 0 <= frame_index < len(current):
        raise ReviewError("ITEM_NOT_FOUND", "帧号超出视频范围", 404)
    review.check(media_id, body.reviewId, frame_index, body.result, user["uid"], current)
    with source_read():
        directory, _, info = media_context(media_id, allow_tracking=True)
        return review.status(media_id, directory, info)


@router.post("/media/{media_id}/pass")
def pass_review(media_id: str, body: RoundRef, user: User):
    with source_read():
        directory, _, info = media_context(media_id)
        review.pass_review(media_id, directory, info, body.reviewId, user["uid"])
        return review.status(media_id, directory, info)


@router.post("/media/{media_id}/reset-annotations")
def reset_annotations(media_id: str, body: ResetAnnotations, user: User, key: Key):
    from . import main, media_reimport
    from .review_source_lock import source_write

    @source_write
    def run():
        directory = main.media_dir(media_id)
        if not directory.is_dir() or not main.find_video(directory):
            raise ReviewError("MEDIA_NOT_FOUND", "素材不存在", 404)
        return media_reimport.reset(directory, user["uid"], key, body.model_dump(), lambda: main.tracking_is_busy(media_id))
    return run()
