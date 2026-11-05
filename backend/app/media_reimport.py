"""Content-based duplicate imports and recoverable annotation reset.

Reset keeps the identical source video and immutable withdrawn A history. Its
journal reconciles filesystem moves with the SQLite receipt after a restart.
Callers hold the single-process source lock; SQL transactions exclude claims.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import shutil
import uuid

from . import annotation_state
from .db import connect
from .review_workflow import ReviewError, digest, now, packed

log = logging.getLogger('review.media')


def move_to_trash(path, root, label):
    """Keep what a delete or overwrite removes in <root>/_trash for manual recovery.

    Same filesystem, so this is a rename and costs nothing for large videos. Directories
    starting with "_" are invisible to the media list and duplicate detection.
    """
    target = root / '_trash' / f"{label}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    target.parent.mkdir(parents=True, exist_ok=True)
    path.replace(target)
    return target


def file_sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def source_fingerprint(path):
    stat = path.stat()
    return {'size':stat.st_size, 'mtimeNs':stat.st_mtime_ns, 'inode':stat.st_ino}


def source_sha(directory, video):
    try:
        meta = json.loads((directory/'media.json').read_text(encoding='utf-8'))
        sha = meta.get('sha256')
        if isinstance(sha,str) and len(sha) == 64 and meta.get('sourceFingerprint') == source_fingerprint(video):
            return sha
    except (OSError,ValueError):
        pass
    return file_sha(video)


def duplicate(directories, find_video, upload):
    sha = file_sha(upload)
    matches = []
    for directory in directories:
        video = find_video(directory)
        if video and video.stat().st_size == upload.stat().st_size and source_sha(directory,video) == sha:
            matches.append(directory)
    # Older installations may already contain duplicate upload directories.
    # Prefer the source referenced by immutable A rather than an unused copy.
    if matches:
        with closing(connect()) as c:
            reference = c.execute('SELECT media_id FROM media_revisions WHERE sha256=?', (sha,)).fetchone()
        if reference:
            preferred = next((d for d in matches if d.name == reference[0]), None)
            if preferred:
                return preferred, sha
    return (matches[0] if matches else None), sha


def overwrite_permission(c, media_id):
    # Old A/B/C/F records are immutable snapshots kept as history; they no longer
    # lock the live workspace. Overwriting makes any review pass stale instead.
    return True, ''


def duplicate_data(directory, uid):
    with closing(connect()) as c:
        allowed, reason = overwrite_permission(c, directory.name)
    state = annotation_state.read_state(directory)
    try:
        name = json.loads((directory / 'media.json').read_text()).get('videoName', directory.name)
    except (OSError, ValueError):
        name = directory.name
    log.info('media.duplicate_detected media=%s actor=%s can_overwrite=%s', directory.name, uid, allowed)
    return dict(mediaId=directory.name, videoName=name, canOverwrite=allowed,
                reason=reason, workspaceRevision=int(state.get('revision', 0)))


def recover(root):
    journals = root / '_annotation_resets'
    if not journals.is_dir():
        return
    for folder in journals.iterdir():
        journal = folder / 'journal.json'
        if not journal.is_file():
            # No moves take place before journal publication.
            shutil.rmtree(folder)
            continue
        data = json.loads(journal.read_text())
        media_id = data['mediaId']
        if Path(media_id).name != media_id:
            raise RuntimeError('Invalid annotation reset journal')
        directory = root / media_id
        with closing(connect()) as c:
            committed = c.execute('SELECT 1 FROM review_write_receipts WHERE user_id=? AND request_key=? AND request_hash=?',
                                  (data['uid'], data['key'], data['hash'])).fetchone()
        if not committed:
            # Restore each moved file, including the original revision. The
            # list is fixed before the first move, so partial moves are safe.
            for name in data['files']:
                if Path(name).name != name:
                    raise RuntimeError('Invalid annotation reset filename')
                old = folder / name
                if old.exists():
                    old.replace(directory / name)
            if 'workspace_state.json' not in data['files']:
                (directory / 'workspace_state.json').unlink(missing_ok=True)
        if committed:
            move_to_trash(folder, root, f'{media_id}-overwritten')
        else:
            shutil.rmtree(folder)
        log.warning('media.reset_recovered media=%s committed=%s', media_id, bool(committed))


def reset(directory, uid, key, body, tracking_busy):
    media_id = directory.name
    h = digest(['reset-annotations', media_id, body])
    folder = None
    committed = False
    try:
        with closing(connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            prior = c.execute('SELECT * FROM review_write_receipts WHERE user_id=? AND request_key=?', (uid, key)).fetchone()
            if prior:
                if prior['request_hash'] != h:
                    raise ReviewError('IDEMPOTENCY_KEY_REUSED', '重试标识不能用于不同操作')
                log.info('media.reset_replay media=%s actor=%s key=%s', media_id, uid, key)
                return json.loads(prior['response_json'])
            allowed, reason = overwrite_permission(c, media_id)
            if not allowed:
                raise ReviewError('MEDIA_ALREADY_SUBMITTED', reason)
            if tracking_busy():
                raise ReviewError('TRACKING_BUSY', 'AI Tracking 正在运行，暂时不能覆盖标注')
            state = annotation_state.read_state(directory)
            revision = int(state.get('revision', 0))
            if body['expectedRevision'] != revision:
                raise ReviewError('WORKSPACE_REVISION_CONFLICT', '工作区已变化，请重新导入并确认覆盖')
            if body['confirmDiscard'] is not True:
                raise ReviewError('DISCARD_CONFIRMATION_REQUIRED', '请确认舍弃旧标注', 422)
            files = [p for p in directory.iterdir() if p.is_file() and (
                p.name in {'workspace_state.json', 'tracker_results.json', annotation_state.RESULT_META_FILE, 'tracker_overlay.mp4'}
                or (p.name.startswith('annotations_frame_') and p.suffix == '.json'))]
            folder = directory.parent / '_annotation_resets' / uuid.uuid4().hex
            folder.mkdir(parents=True)
            journal = dict(mediaId=media_id, uid=uid, key=key, hash=h, files=[p.name for p in files])
            (folder / 'journal.json').write_text(packed(journal))
            for path in files:
                path.replace(folder / path.name)
            clean = dict(format='annotation-workspace-v1', mediaId=media_id, updatedBy=uid,
                         revision=revision + 1, generationId=uuid.uuid4().hex, currentFrame=0,
                         manualAnnotations=[], manualBaselines=[], deletedObjectIds=[], deletedFrameObjects=[],
                         deletedTrackingIds=[], anomalyFrames=[], pausedAnomalies=[], lastPausedContext=None,
                         normalMotionSamples=[], trackingFeedbackEvents=[],
                         display=dict(brightness=100, contrast=100, zoom=1))
            (directory / 'workspace_state.json').write_text(packed(clean))
            c.execute('DELETE FROM annotations WHERE media_id=?', (media_id,))
            result = dict(ok=True, mediaId=media_id, revision=revision + 1, generationId=clean['generationId'])
            c.execute('INSERT INTO review_write_receipts VALUES (?,?,?,?,?)', (uid, key, h, packed(result), now()))
            c.commit()
            committed = True
        log.info('media.annotations_reset media=%s actor=%s revision=%s key=%s', media_id, uid, revision + 1, key)
        return result
    except ReviewError as exc:
        log.warning('media.reset_rejected media=%s actor=%s code=%s', media_id, uid, exc.code)
        raise
    except Exception as exc:
        log.exception('media.reset_failed media=%s actor=%s key=%s', media_id, uid, key)
        raise ReviewError('RESET_FAILED', '覆盖失败，旧数据已保留，请使用原请求重试', 503) from exc
    finally:
        if folder is not None:
            if not committed:
                # DB context has rolled back before reconciling the journal.
                recover(directory.parent)
            else:
                try:
                    move_to_trash(folder, directory.parent, f'{media_id}-overwritten')
                except OSError:
                    log.exception('media.reset_cleanup_pending media=%s', media_id)
