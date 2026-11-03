import json
import pytest
from app import annotation_export as direct, annotation_completion as a, db, training_export as export
from .test_review_completion import source, body, INFO
from .test_review_workflow import task


def review_pass(source, uid=1):
    """Pass an annotation review: every frame checked, frames without annotations confirmed empty."""
    from app import annotation_review as review
    state = review.start('complete-media', source, INFO, uid)
    frames = review.load(source, INFO)
    for start, end in state['frames']:
        for i in range(start, end + 1):
            review.check('complete-media', state['reviewId'], i, 'ok' if frames[i] else 'empty', uid, frames)
    return review.pass_review('complete-media', source, INFO, state['reviewId'], uid)


def test_snapshot_requires_review_pass_and_explicit_confirmation(source):
    with db.connect() as c: export.migrate(c)
    with pytest.raises(Exception, match='审查') as e:
        direct.freeze(source, source/'video.avi', 'complete-media', 1, 'bad', body(source, explicitEmptyFrameRanges=[]), INFO)
    assert e.value.code == 'REVIEW_REQUIRED'
    review_pass(source)
    # Frames the review confirmed empty count as declared, so no explicit range is needed.
    direct.freeze(source, source/'video.avi', 'complete-media', 1, 'reviewed', body(source, explicitEmptyFrameRanges=[]), INFO)
    with pytest.raises(Exception, match='确认'):
        direct.freeze(source, source/'video.avi', 'complete-media', 1, 'bad2', body(source, confirmComplete=False), INFO)


def test_direct_snapshot_replay_and_stale_version(source, monkeypatch):
    with db.connect() as c: export.migrate(c)
    from app import api_common as routes
    monkeypatch.setattr(routes, "completion_context", lambda mid: (source, source/"video.avi", INFO))
    review_pass(source)
    args = (source, source/'video.avi', 'complete-media', 1, 'direct-key', body(source), INFO)
    result = direct.freeze(*args)
    assert direct.freeze(*args) == result
    with db.connect() as c:
        versions, summary = export.snapshots(c, [result['finalVersionId']])
        assert summary['frameCount'] == 3
        assert versions[0]['frames'][0]['objects'][0]['bbox'] == [64,48,128,96]
        assert versions[0]['final']['confirmation_id'] is None
        with pytest.raises(Exception): direct.authorize(c, [result['finalVersionId']], 2)
    state = json.loads((source/'workspace_state.json').read_text())
    state['manualAnnotations'][0]['bbox']['x'] = 11
    (source/'workspace_state.json').write_text(json.dumps(state))
    with db.connect() as c:
        with pytest.raises(Exception, match='变化'): direct.resolve(c, result['finalVersionId'], directory=source, info=INFO)


def test_real_direct_export_zip_and_invalidation(source, monkeypatch, tmp_path):
    import zipfile
    from app import main, api_common as routes, confirmation_workflow as confirmation
    from .test_training_export import video_file
    video=video_file(source, n=3)
    with db.connect() as c:
        confirmation.migrate(c)
        export.migrate(c)
    monkeypatch.setattr(routes,'completion_context',lambda mid:(source,video,INFO))
    monkeypatch.setattr(main,'media_dir',lambda mid:source)
    monkeypatch.setattr(export,'DATASET_EXPORT_DIR',tmp_path/'packages')
    monkeypatch.setattr(export,'enqueue',lambda eid:None)
    review_pass(source)
    result=direct.freeze(source,video,'complete-media',1,'snapshot',body(source),INFO)
    settings=dict(finalVersionIds=[result['finalVersionId']],format='yolo',splitRatio=.8)
    job=export.create(1,'export',settings)
    export.run(job['exportId'])
    assert export.status(job['exportId'],1)['state']=='ready'
    with zipfile.ZipFile(export.download(job['exportId'],1)) as z:
        assert len([n for n in z.namelist() if n.startswith('images/') and n.endswith('.jpg')])==3
        labels=[z.read(n).decode() for n in z.namelist() if n.startswith('labels/') and n.endswith('.txt')]
        assert len(labels)==3 and sum(bool(x.strip()) for x in labels)==1
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['sources'][0]['annotationOnly'] is True
        assert 'reviewDecisions' not in manifest
        assert not any(n.startswith('review/') for n in z.namelist())
    # Replaying the original request is stable; changing the current source revokes downloads.
    assert export.create(1,'export',settings)['exportId']==job['exportId']
    state=json.loads((source/'workspace_state.json').read_text());state['manualAnnotations'][0]['bbox']['x']=12
    (source/'workspace_state.json').write_text(json.dumps(state))
    assert export.status(job['exportId'],1)['state']=='invalidated'
    with pytest.raises(Exception): export.download(job['exportId'],1)

def test_api_authorization_and_original_snapshot_replay(source, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.auth import sign_jwt
    from app.training_export_routes import router
    from app import confirmation_workflow as confirmation, api_common as routes
    with db.connect() as c:
        confirmation.migrate(c);export.migrate(c)
    monkeypatch.setattr(routes,'completion_context',lambda mid:(source,source/'video.avi',INFO))
    app=FastAPI();app.include_router(router)
    client=TestClient(app)
    review_pass(source)
    payload=body(source)
    headers={'Authorization':'Bearer '+sign_jwt({'uid':1,'name':'a'}),'X-Review-Contract':'2','Idempotency-Key':'snapshot-http'}
    path='/api/datasets/annotations/complete-media/snapshot'
    assert client.post(path,json=payload).status_code==401
    response=client.post(path,json=payload,headers=headers)
    assert response.status_code==200,response.text
    with monkeypatch.context() as replay_patch:
        replay_patch.setattr(routes,'completion_context',lambda mid:(_ for _ in ()).throw(RuntimeError('must not reread on replay')))
        assert client.post(path,json=payload,headers=headers).json()==response.json()
    changed=client.post(path,json={**payload,'confirmComplete':False},headers=headers)
    assert changed.status_code==409
    ids={'finalVersionIds':[response.json()['finalVersionId']]}
    other={**headers,'Authorization':'Bearer '+sign_jwt({'uid':2,'name':'b'})}
    assert client.post('/api/datasets/exports/preview',json=ids,headers=other).status_code==404
    assert client.post('/api/datasets/exports/preview',json=ids,headers=headers).status_code==200


def test_snapshot_failure_preserves_source_and_no_partial_snapshot(source, monkeypatch):
    with db.connect() as c: export.migrate(c)
    review_pass(source)
    before=(source/'workspace_state.json').read_bytes()
    monkeypatch.setattr(direct,'compute_file_sha256',lambda _:(_ for _ in ()).throw(OSError('injected disk error')))
    with pytest.raises(OSError):direct.freeze(source,source/'video.avi','complete-media',1,'fail',body(source),INFO)
    with db.connect() as c:assert c.execute('SELECT COUNT(*) FROM annotation_export_snapshots').fetchone()[0]==0
    assert (source/'workspace_state.json').read_bytes()==before


def test_latest_snapshot_per_video_reports_changed_workspaces(source, monkeypatch):
    with db.connect() as c: export.migrate(c)
    from app import api_common as routes
    monkeypatch.setattr(routes, "completion_context", lambda mid: (source, source/"video.avi", INFO))
    review_pass(source)
    first = direct.freeze(source, source/'video.avi', 'complete-media', 1, 'list-1', body(source), INFO)
    second = direct.freeze(source, source/'video.avi', 'complete-media', 1, 'list-2', body(source), INFO)
    items = direct.latest(1)
    assert [i['finalVersionId'] for i in items] == [second['finalVersionId']] != [first['finalVersionId']]
    assert items[0]['valid'] and items[0]['name'] == 'video.avi' and items[0]['frameCount'] == 3
    assert direct.latest(2) == []
    state = json.loads((source/'workspace_state.json').read_text())
    state['manualAnnotations'][0]['bbox']['x'] = 11
    (source/'workspace_state.json').write_text(json.dumps(state))
    stale = direct.latest(1)[0]
    assert not stale['valid'] and '变化' in stale['reason']


def test_export_marks_boxes_the_reviewer_changed(source, monkeypatch, tmp_path):
    import zipfile
    from app import main, api_common as routes, confirmation_workflow as confirmation, annotation_review as review
    from .test_training_export import video_file
    from .test_annotation_review import edit_manual
    video=video_file(source,n=3)
    with db.connect() as c:
        confirmation.migrate(c)
        export.migrate(c)
    monkeypatch.setattr(routes,'completion_context',lambda mid:(source,video,INFO))
    monkeypatch.setattr(main,'media_dir',lambda mid:source)
    monkeypatch.setattr(export,'DATASET_EXPORT_DIR',tmp_path/'packages')
    monkeypatch.setattr(export,'enqueue',lambda eid:None)
    state=review.start('complete-media',source,INFO,1)
    edit_manual(source,30)  # The reviewer moves the box during review.
    workspace=json.loads((source/'workspace_state.json').read_text());workspace['updatedBy']=1
    (source/'workspace_state.json').write_text(json.dumps(workspace))
    current=review.load(source,INFO)
    for i in range(INFO['frameCount']):
        review.check('complete-media',state['reviewId'],i,'ok' if current[i] else 'empty',2,current)
    passed=review.pass_review('complete-media',source,INFO,state['reviewId'],2)
    moved={(f['frameIndex'],oid) for f in passed['changes']['frames'] for oid in f['moved']}
    assert moved
    result=direct.freeze(source,video,'complete-media',1,'edited',body(source),INFO)
    job=export.create(1,'edited-export',dict(finalVersionIds=[result['finalVersionId']],format='yolo',splitRatio=.8))
    export.run(job['exportId'])
    with zipfile.ZipFile(export.download(job['exportId'],1)) as z:
        manifest=json.loads(z.read('manifest.json'))
    objects=[(s['frameIndex'],o['objectId'],o['reviewEdited']) for s in manifest['samples'] for o in s['objects']]
    assert objects and all(edited==((fi,oid) in moved) for fi,oid,edited in objects)
    assert any(edited for _,_,edited in objects)
