"""Folders of the media library: one display path per video, stored apart from the files.

The tree is computed from the paths, so a folder exists while it holds a video and
needs no table or management of its own. Moving or renaming never touches video files,
annotations or review records. A video without a row shows as uncategorized.
"""
import logging
from contextlib import closing

from fastapi import APIRouter
from pydantic import Field

from .api_common import LoggedRoute, Model, User
from .db import connect
from .review_workflow import ReviewError

log = logging.getLogger("review.media")
router = APIRouter(prefix="/api/library", tags=["library"], route_class=LoggedRoute)
MAX_DEPTH, MAX_NAME = 8, 100


def normalize(path):
    """Canonical "a/b" form; empty means uncategorized."""
    parts = [part.strip() for part in str(path or "").replace("\\", "/").split("/")]
    parts = [part for part in parts if part]
    if len(parts) > MAX_DEPTH or any(part in {".", ".."} or len(part) > MAX_NAME for part in parts):
        raise ReviewError("INVALID_FOLDER_PATH", f"文件夹路径无效：最多 {MAX_DEPTH} 层，每层最多 {MAX_NAME} 个字", 422)
    return "/".join(parts)


def paths():
    with closing(connect()) as c:
        return {row["media_id"]: row["path"] for row in c.execute("SELECT media_id,path FROM media_folder")}


def assign(c, media_id, path):
    if path:
        c.execute("INSERT INTO media_folder(media_id,path) VALUES (?,?) ON CONFLICT(media_id) DO UPDATE SET path=excluded.path", (media_id, path))
    else:
        c.execute("DELETE FROM media_folder WHERE media_id=?", (media_id,))


def place(media_id, path):
    """Record where a new video belongs. Also clears the row of a deleted video whose id was reused."""
    with closing(connect()) as c, c:
        assign(c, media_id, path)


def move(media_ids, path, actor):
    from .main import media_dir
    path = normalize(path)
    missing = [media_id for media_id in media_ids if not media_dir(media_id).is_dir()]
    if missing:
        raise ReviewError("MEDIA_NOT_FOUND", f"素材不存在：{missing[0]}", 404)
    with closing(connect()) as c, c:
        for media_id in media_ids:
            assign(c, media_id, path)
    log.info("library.moved actor=%s count=%s path=%s", actor, len(media_ids), path)
    return {"moved": len(media_ids), "path": path}


def rename(old, new, actor):
    """Rename a folder and everything under it; renaming onto an existing folder merges them."""
    old, new = normalize(old), normalize(new)
    if not old or not new:
        raise ReviewError("INVALID_FOLDER_PATH", "文件夹名称不能为空", 422)
    if new.startswith(old + "/"):
        raise ReviewError("INVALID_FOLDER_PATH", "不能把文件夹移到它自己里面", 422)
    with closing(connect()) as c, c:
        rows = [row for row in c.execute("SELECT media_id,path FROM media_folder") if row["path"] == old or row["path"].startswith(old + "/")]
        for row in rows:
            assign(c, row["media_id"], normalize(new + row["path"][len(old):]))
    log.info("library.renamed actor=%s from=%s to=%s count=%s", actor, old, new, len(rows))
    return {"moved": len(rows), "path": new}


class Move(Model):
    mediaIds: list[str] = Field(min_length=1, max_length=2000)
    path: str = Field(max_length=1000)


class Rename(Model):
    # "from" is a Python keyword, so the body uses oldPath/newPath.
    oldPath: str = Field(min_length=1, max_length=1000)
    newPath: str = Field(min_length=1, max_length=1000)


@router.put("/folder")
def put_folder(body: Move, user: User):
    return move(body.mediaIds, body.path, user["uid"])


@router.post("/folder/rename")
def post_rename(body: Rename, user: User):
    return rename(body.oldPath, body.newPath, user["uid"])
