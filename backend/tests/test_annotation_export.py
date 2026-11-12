import json
import pytest
from app import annotation_export as direct, annotation_completion as a, db, training_export as export
from .test_review_completion import source, body, INFO
from .test_review_workflow import task


def review_pass(source, uid=1, result='tail'):
    """Pass an annotation review: every target answered, frames without annotations confirmed empty, one playthrough."""
    from app import annotation_review as review
    state = review.start('complete-media', source, INFO, uid)
    frames = review.load(source, INFO)
    for oid in state['targets']:
        review.check_target('complete-media', state['reviewId'], oid, result, uid, frames)
    for start, end in state['unknownFrames']:
        for i in range(start, end + 1):
            review.check('complete-media', state['reviewId'], i, 'empty', uid, frames)
    review.record_playthrough('complete-media', state['reviewId'], uid, frames)
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
        assert [o['tail'] for s in manifest['samples'] for o in s['objects']] == [True]  # review_pass answers "tail".
        assert all(s['ignore']==[] for s in manifest['samples'])
        assert not any(n.startswith('review/') for n in z.namelist())
    # Replaying the original request is stable; changing the current source revokes downloads.
    assert export.create(1,'export',settings)['exportId']==job['exportId']
    state=json.loads((source/'workspace_state.json').read_text());state['manualAnnotations'][0]['bbox']['x']=12
    (source/'workspace_state.json').write_text(json.dumps(state))
    assert export.status(job['exportId'],1)['state']=='invalidated'
    with pytest.raises(Exception): export.download(job['exportId'],1)

def test_unsure_targets_are_ignored_regions_and_answers_mark_tails(source, monkeypatch, tmp_path):
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
    review_pass(source, result='unsure')
    result=direct.freeze(source,video,'complete-media',1,'snapshot',body(source),INFO)
    job=export.create(1,'export',dict(finalVersionIds=[result['finalVersionId']],format='yolo',splitRatio=.8))
    export.run(job['exportId'])
    with zipfile.ZipFile(export.download(job['exportId'],1)) as z:
        labels=[z.read(n).decode() for n in z.namelist() if n.startswith('labels/') and n.endswith('.txt')]
        manifest=json.loads(z.read('manifest.json'))
    assert not any(x.strip() for x in labels)  # Not a positive and not a negative: no label line.
    ignored=[(s['frameIndex'],i['objectId']) for s in manifest['samples'] for i in s['ignore']]
    assert ignored and all(s['objectCount']==0 for s in manifest['samples'])


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
    monkeypatch.setattr(direct,'source_sha',lambda *_:(_ for _ in ()).throw(OSError('injected disk error')))
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
    assert first == second  # Unchanged content reuses its snapshot.
    assert [i['finalVersionId'] for i in items] == [first['finalVersionId']]
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
    for oid in review.status('complete-media',source,INFO)['review']['targets']:
        review.check_target('complete-media',state['reviewId'],oid,'tail',2,current)
    for i,frame in enumerate(current):
        if frame is None:review.check('complete-media',state['reviewId'],i,'empty',2,current)
    review.record_playthrough('complete-media',state['reviewId'],2,current)
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


def exportable(source, monkeypatch, tmp_path):
    from app import main, api_common as routes, confirmation_workflow as confirmation
    from .test_training_export import video_file
    video = video_file(source, n=3)
    with db.connect() as c:
        confirmation.migrate(c)
        export.migrate(c)
    monkeypatch.setattr(routes, 'completion_context', lambda mid: (source, video, INFO))
    monkeypatch.setattr(main, 'media_dir', lambda mid: source)
    monkeypatch.setattr(export, 'DATASET_EXPORT_DIR', tmp_path / 'packages')
    monkeypatch.setattr(export, 'enqueue', lambda eid: None)
    review_pass(source)
    final = direct.freeze(source, video, 'complete-media', 1, 'snapshot', body(source), INFO)['finalVersionId']
    return export.create(1, 'export', dict(finalVersionIds=[final], format='yolo', splitRatio=.8))['exportId']


def test_build_fully_validates_only_at_its_start_and_end(source, monkeypatch, tmp_path):
    eid = exportable(source, monkeypatch, tmp_path)
    seen, original = [], export.eligible
    monkeypatch.setattr(export, 'eligible', lambda c, ids: (seen.append(ids), original(c, ids))[1])
    export.run(eid)
    assert len(seen) == 2  # Progress checks in between only compare file stamps.
    assert export.status(eid, 1)['state'] == 'ready'


def test_source_change_during_the_build_stops_it(source, monkeypatch, tmp_path):
    eid = exportable(source, monkeypatch, tmp_path)
    build = export.build_package

    def change_then_build(*args):
        state = json.loads((source / 'workspace_state.json').read_text())
        state['manualAnnotations'][0]['bbox']['x'] = 12
        (source / 'workspace_state.json').write_text(json.dumps(state))
        return build(*args)
    monkeypatch.setattr(export, 'build_package', change_then_build)
    export.run(eid)
    result = export.status(eid, 1)
    assert result['state'] in ('failed', 'invalidated') and result['errorCode'] == 'SOURCE_REVISION_CONFLICT'
    assert not (export.DATASET_EXPORT_DIR / (eid + '.zip')).exists()


def test_status_of_a_running_job_does_not_revalidate(source, monkeypatch, tmp_path):
    eid = exportable(source, monkeypatch, tmp_path)
    monkeypatch.setattr(export, 'eligible', lambda *_: pytest.fail('polling must not re-read the sources'))
    assert export.status(eid, 1)['state'] == 'queued'


def test_jpegs_are_stored_uncompressed(source, monkeypatch, tmp_path):
    import zipfile
    eid = exportable(source, monkeypatch, tmp_path)
    export.run(eid)
    with zipfile.ZipFile(export.download(eid, 1)) as z:
        kinds = {i.filename.rsplit('.')[-1]: i.compress_type for i in z.infolist()}
    assert kinds['jpg'] == zipfile.ZIP_STORED and kinds['txt'] == zipfile.ZIP_DEFLATED


def test_dataset_download_cookie_is_limited_to_downloads(source, monkeypatch, tmp_path):
    from fastapi import FastAPI, Response, Request
    from fastapi.testclient import TestClient
    from app import auth
    from app.training_export_routes import router
    eid = exportable(source, monkeypatch, tmp_path)
    export.run(eid)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    url = f'/api/datasets/exports/{eid}/download'
    assert client.get(url).status_code == 401
    response = Response()
    auth.set_video_session(response, Request({'type': 'http', 'scheme': 'http', 'headers': [], 'path': '/', 'server': ('t', 80)}), 1)
    header = next(v.decode() for k, v in response.raw_headers if k == b'set-cookie' and auth.EXPORT_COOKIE.encode() in v)
    assert 'Path=/api/datasets/exports/' in header and 'HttpOnly' in header and 'samesite=strict' in header.lower()
    token = header.split(';')[0].split('=', 1)[1]
    client.cookies.set(auth.EXPORT_COOKIE, token)
    got = client.get(url)
    assert got.status_code == 200 and got.headers['content-type'] == 'application/zip'
    assert 'content-disposition' not in got.headers  # The page's link names the file.
    assert client.get(url, headers={'Range': 'bytes=0-9'}).status_code == 206  # An interrupted download can resume.
    assert client.get(f'/api/datasets/exports/{eid}').status_code == 401  # The cookie only opens downloads.
    client.cookies.set(auth.EXPORT_COOKIE, auth.sign_jwt({'uid': 1, 'purpose': 'source-video'}))
    assert client.get(url).status_code == 401  # A video cookie is not a download cookie.
    client.cookies.clear()
    client.cookies.set(auth.EXPORT_COOKIE, auth.sign_jwt({'uid': 2, 'purpose': 'dataset-download'}))
    assert client.get(url).status_code == 404  # Another account's cookie opens nothing.


def test_job_list_exported_record_and_delete(source, monkeypatch, tmp_path):
    eid = exportable(source, monkeypatch, tmp_path)
    assert export.my_jobs(1)[0]['state'] == 'queued' and export.exported_media() == {}
    with pytest.raises(Exception) as e:
        export.delete(eid, 1)
    assert e.value.code == 'EXPORT_RUNNING'
    export.run(eid)
    job = export.my_jobs(1)[0]
    assert job['exportId'] == eid and job['state'] == 'ready' and job['mediaIds'] == ['complete-media'] and job['videoCount'] == 1
    assert export.my_jobs(2) == []
    assert list(export.exported_media()) == ['complete-media']
    with pytest.raises(Exception) as e:
        export.delete(eid, 2)
    assert e.value.code == 'EXPORT_NOT_FOUND'
    zip_file = export.DATASET_EXPORT_DIR / (eid + '.zip')
    assert zip_file.exists()
    assert export.delete(eid, 1)['deleted'] and not zip_file.exists()
    assert export.my_jobs(1) == [] and list(export.exported_media()) == ['complete-media']  # It was still exported.
    with pytest.raises(Exception):
        export.download(eid, 1)


def test_invalidated_download_names_the_changed_video(source, monkeypatch, tmp_path):
    eid = exportable(source, monkeypatch, tmp_path)
    export.run(eid)
    state = json.loads((source / 'workspace_state.json').read_text())
    state['manualAnnotations'][0]['bbox']['x'] = 12
    (source / 'workspace_state.json').write_text(json.dumps(state))
    result = export.status(eid, 1)
    assert result['state'] == 'invalidated' and 'complete-media' in result['errorMessage']
