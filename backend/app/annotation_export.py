"""Explicit annotation export snapshots. No synthetic review/confirmation records."""
import json
import uuid
import logging
from contextlib import closing
from . import annotation_completion as annotation
from .db import connect
from .review_workflow import ReviewError, digest, packed, now
from .media_reimport import source_sha

log = logging.getLogger('review.dataset')


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS annotation_export_snapshots (
      id TEXT PRIMARY KEY, actor_id INTEGER NOT NULL, media_id TEXT NOT NULL,
      request_key TEXT NOT NULL, request_hash TEXT NOT NULL, source_revision TEXT NOT NULL,
      media_json TEXT NOT NULL, frames_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
      created_at TEXT NOT NULL, UNIQUE(actor_id,request_key))''')


def authorize(c, ids, actor):
    for vid in ids:
        if vid.startswith('annexp_'):
            row = c.execute('SELECT actor_id FROM annotation_export_snapshots WHERE id=?', (vid,)).fetchone()
            if not row or row[0] != actor:
                raise ReviewError('ANNOTATION_EXPORT_NOT_FOUND', '标注导出快照不存在', 404)


def replay(actor, key, media_id, body):
    with closing(connect()) as c:
        old=c.execute('SELECT * FROM annotation_export_snapshots WHERE actor_id=? AND request_key=?',(actor,key)).fetchone()
        if old:
            if old['request_hash'] != digest(['annotation-export',media_id,body]):
                raise ReviewError('IDEMPOTENCY_KEY_REUSED','重试标识不能用于不同操作')
            return {'finalVersionId':old['id']}
    return None


def source_stamp(media_id):
    """Size and time of the files a snapshot's content is read from; equal stamps mean equal content."""
    from .main import media_dir
    directory, out = media_dir(media_id), []
    for name in ('workspace_state.json', 'tracker_results.json'):
        try:
            stat = (directory / name).stat()
            out.append((stat.st_size, stat.st_mtime_ns))
        except OSError:
            out.append(None)
    return out


def freeze(directory, video, media_id, actor, key, body, info, sha=None):
    """``sha`` lets the caller hash a large video before taking the shared source lock."""
    request_hash = digest(['annotation-export', media_id, body])
    with closing(connect()) as c, c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM annotation_export_snapshots WHERE actor_id=? AND request_key=?', (actor,key)).fetchone()
        if old:
            if old['request_hash'] != request_hash:
                raise ReviewError('IDEMPOTENCY_KEY_REUSED', '重试标识不能用于不同操作')
            return {'finalVersionId':old['id']}
        revision, frames = annotation.source(directory, actor, info, require_author=False)
        if revision != body['expectedSourceRevision']:
            raise ReviewError('SOURCE_REVISION_CONFLICT', '标注来源已变化，请重新预览')
        from .annotation_review import require_passed
        # Only the content a reviewer passed may become training data; frames the
        # review confirmed as empty count as declared empty.
        for i in require_passed(c, media_id, frames, info['frameCount']):
            frames.setdefault(i, {})
        if body['confirmComplete'] is not True:
            raise ReviewError('CONFIRM_COMPLETION_REQUIRED', '请确认已检查标注并导出', 422)
        for r in body['explicitEmptyFrameRanges']:
            if not 0 <= r['start'] <= r['end'] < info['frameCount']:
                raise ReviewError('INVALID_EMPTY_RANGE', '空帧范围无效', 422)
            for i in range(r['start'], r['end']+1):
                if frames.get(i): raise ReviewError('NONEMPTY_FRAME', '已有对象帧不能声明为空', 422)
                frames[i] = {}
        if len(frames) != info['frameCount']:
            raise ReviewError('INCOMPLETE_ANNOTATION', '仍有未知帧，请补标或明确声明空帧', 422)
        frozen = [{'frameIndex':i,'objects':sorted(frames[i].values(), key=lambda o:o['objectId'])} for i in range(info['frameCount'])]
        for frame in frozen:
            for obj in frame['objects']:
                obj['annotationId'] = f"annotation:{media_id}:{frame['frameIndex']}:{obj['objectId']}"
                obj['resolution'] = 'annotation-direct'
        same = c.execute('SELECT id FROM annotation_export_snapshots WHERE actor_id=? AND media_id=? AND source_revision=? AND snapshot_hash=? ORDER BY rowid DESC LIMIT 1',
                         (actor, media_id, revision, digest(frozen))).fetchone()
        if same:  # Unchanged content: exporting it again needs no second snapshot.
            return {'finalVersionId':same['id']}
        media = dict(id='annotation-source-'+media_id, media_id=media_id, sha256=sha or source_sha(directory, video), file_size=video.stat().st_size,
                     width=info['width'], height=info['height'], fps=info['fps'], frame_count=info['frameCount'])
        if annotation.source(directory, actor, info, require_author=False)[0] != revision:
            raise ReviewError('SOURCE_REVISION_CONFLICT', '读取过程中标注来源变化，请重新预览')
        vid='annexp_'+uuid.uuid4().hex
        c.execute('INSERT INTO annotation_export_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)',
                  (vid,actor,media_id,key,request_hash,revision,packed(media),packed(frozen),digest(frozen),now()))
    log.info('dataset.annotation_snapshot media=%s actor=%s snapshot=%s frames=%s', media_id,actor,vid,len(frozen))
    return {'finalVersionId':vid}


def resolve(c, vid, directory=None, info=None):
    row=c.execute('SELECT * FROM annotation_export_snapshots WHERE id=?',(vid,)).fetchone()
    if not row: raise ReviewError('ANNOTATION_EXPORT_NOT_FOUND','标注导出快照不存在',404)
    if directory is None:
        from .api_common import completion_context
        directory, _, info = completion_context(row['media_id'])
    if annotation.source(directory,row['actor_id'],info,require_author=False)[0] != row['source_revision']:
        raise ReviewError('SOURCE_REVISION_CONFLICT',f"「{row['media_id']}」的标注已变化，请重新审查后再导出")
    frames=json.loads(row['frames_json'])
    return {'final':dict(id=vid, snapshot_hash=row['snapshot_hash'], frame_count=len(frames),
                        baseline_id=None, review_version_id=None, confirmation_id=None),
            'media':json.loads(row['media_json']), 'directFrames':frames, 'annotationActorId':row['actor_id']}


def latest(actor):
    """Newest snapshot per video, marked invalid when its workspace changed after export."""
    items = []
    with closing(connect()) as c:
        rows = c.execute('''SELECT * FROM annotation_export_snapshots s WHERE actor_id=? AND rowid=(
          SELECT MAX(rowid) FROM annotation_export_snapshots WHERE actor_id=s.actor_id AND media_id=s.media_id)
          ORDER BY created_at DESC''', (actor,)).fetchall()
        for row in rows:
            media = json.loads(row['media_json'])
            item = {'finalVersionId': row['id'], 'mediaId': row['media_id'], 'name': row['media_id'],
                    'frameCount': media['frame_count'], 'createdAt': row['created_at'], 'valid': True, 'reason': None}
            try:
                from .api_common import completion_context
                directory, video, info = completion_context(row['media_id'])
                item['name'] = video.name
                resolve(c, row['id'], directory, info)
            except ReviewError as e:
                item.update(valid=False, reason=e.message)
            except Exception:
                log.exception('dataset.snapshot_list_failed snapshot=%s', row['id'])
                item.update(valid=False, reason='读取标注失败，请从标注页重新导出')
            items.append(item)
    return items
