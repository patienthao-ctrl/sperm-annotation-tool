"""Annotation review checklist, follow-up re-checks, pass record, staleness and the export gate."""

import json
import uuid

import pytest
from app import annotation_review as review, db
from app.review_workflow import ReviewError
from .test_review_completion import INFO, source
from .test_review_workflow import task

MID = "complete-media"


def obj(oid, x=10):
    return {"objectId": oid, "bbox": [x, 10, x + 8, 18], "classKey": "sperm"}


def frames_with_counts(counts):
    return [[obj(k + 1, 10 * k) for k in range(n)] for n in counts]


def kinds(items):
    return [i["kind"] for i in items]


def check_all(state, uid=1, result="ok"):
    for item in state["items"]:
        if not item["check"]:
            state = review.check(MID, state["reviewId"], item["id"], "empty" if item["kind"] == "unknown" else result, uid)
    return state


def edit_manual(source, x):
    state = json.loads((source / "workspace_state.json").read_text())
    state["manualAnnotations"][0]["bbox"]["x"] = x
    state["revision"] = state.get("revision", 0) + 1  # As every real workspace save does.
    state["updatedBy"] = 2  # Someone else's save must not matter for review or export.
    (source / "workspace_state.json").write_text(json.dumps(state))


def test_checklist_covers_doubts_count_jumps_coverage_unknown_and_samples():
    frames = frames_with_counts([5, 5, 5, 5, 1, 5, 5, 5, 5, 5, 5, 5])
    frames[10] = None
    rows = [{"source_frame_index": 7, "objects": [], "reviewEvidence": {"state": "checked", "findings": [
        {"objectId": 3, "kind": "detector_mismatch", "message": "检测头部与追踪框位置不一致", "bbox": [1, 2, 3, 4]},
        {"objectId": None, "kind": "unlabelled_candidate", "message": "未关联头部候选", "bbox": [5, 6, 7, 8]}]}},
        {"source_frame_index": 1, "objects": [], "reviewEvidence": {"findings": [
            {"objectId": None, "kind": "endpoint_identity_uncertain", "tailObjectId": 9, "bbox": [100, 100, 110, 110]}]}},
        {"source_frame_index": 2, "objects": [], "reviewEvidence": {"findings": [
            {"objectId": None, "kind": "endpoint_identity_uncertain", "tailObjectId": 9, "bbox": [101, 100, 111, 110]}]}}]
    items = review.build_items("arev_fixed", frames, rows)
    assert {"unknown", "doubt", "count", "coverage", "sample"} == set(kinds(items))
    count = next(i for i in items if i["kind"] == "count")
    assert count["frame"] == 4 and "1 个框" in count["message"]
    doubts = [i for i in items if i["kind"] == "doubt"]
    assert [i['category'] for i in doubts] == ['missing', 'position']
    assert doubts[0]['candidates'] == [[100, 100, 110, 110]] and doubts[0]['source'] == 'reverse'
    doubt = doubts[1]
    assert doubt["frame"] == 7 and doubt["objectIds"] == [3] and not doubt["candidates"]
    assert doubt["labels"] == ["框偏离头部"]
    assert [i["frame"] for i in items if i["kind"] == "coverage"] == [3, 6, 8]
    flagged = {f for i in items if i["kind"] != "sample" for f in range(i["startFrame"], i["endFrame"] + 1)}
    picked = [i["frame"] for i in items if i["kind"] == "sample"]
    assert len(picked) == 4 and not flagged & set(picked) and 10 not in picked
    assert len({i["id"] for i in items}) == len(items)
    # The same round id reproduces the same samples.
    assert picked == [i["frame"] for i in review.build_items("arev_fixed", frames, rows) if i["kind"] == "sample"]


def test_rereview_lists_changes_nearby_doubts_and_fewer_samples():
    old = frames_with_counts([2] * 20)
    new = [list(f) for f in old]
    new[9] = [obj(1, 50), obj(2)]
    rows = [
        {"source_frame_index": 10, "objects": [], "reviewEvidence": {"findings": [{"objectId": 1, "kind": "detector_mismatch", "message": "近"}]}},
        {"source_frame_index": 18, "objects": [], "reviewEvidence": {"findings": [{"objectId": 2, "kind": "detector_mismatch", "message": "远"}]}},
    ]
    items = review.build_items("arev_again", new, rows, previous=old)
    assert [i["frame"] for i in items if i["kind"] == "changed"] == [9]
    assert [i["frame"] for i in items if i["kind"] == "doubt"] == [10]
    assert "coverage" not in kinds(items) and kinds(items).count("sample") == 3


def test_full_round_escalates_records_changes_and_gates_export(source):
    state = review.start(MID, source, INFO, 1)
    assert review.start(MID, source, INFO, 2)["reviewId"] == state["reviewId"]  # One open round per video.
    unknown = next(i for i in state["items"] if i["kind"] == "unknown")
    assert (unknown["startFrame"], unknown["endFrame"]) == (2, 2)
    with pytest.raises(ReviewError) as e:
        review.pass_review(MID, source, INFO, state["reviewId"], 1)
    assert e.value.code == "REVIEW_INCOMPLETE"
    with pytest.raises(ReviewError):
        review.check(MID, state["reviewId"], unknown["id"].replace("unknown", "coverage"), "empty", 1)
    # The reviewer fixes a box directly in the shared workspace, then passes.
    edit_manual(source, 30)
    state = check_all(state, uid=2)
    passed = review.pass_review(MID, source, INFO, state["reviewId"], 2)
    assert passed["state"] == "passed" and passed["passedBy"] == 2
    assert passed["changes"]["moved"] == 1 and passed["changes"]["frames"][0]["frameIndex"] == 0
    assert passed["changes"]["frames"][0]["checkedBy"] == 2 and passed["changes"]["reviewers"] == [2]
    status = review.status(MID, source, INFO)
    assert status["status"] == "passed" and status["unknownFrames"] == 1
    assert review.summary([(MID, source)]) == {MID: "passed"}
    with pytest.raises(ReviewError) as e:
        review.start(MID, source, INFO, 1)
    assert e.value.code == "REVIEW_ALREADY_PASSED"
    from app.annotation_completion import source as read
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        assert review.require_passed(c, MID, frames, 3) == {2}
    # Any later edit invalidates the pass until a re-review covers it.
    edit_manual(source, 40)
    assert review.status(MID, source, INFO)["status"] == "stale"
    assert review.summary([(MID, source)]) == {MID: "stale"}
    with db.connect() as c:
        _, frames = read(source, 1, INFO, require_author=False)
        with pytest.raises(ReviewError, match="重新审查"):
            review.require_passed(c, MID, frames, 3)
    again = review.start(MID, source, INFO, 1)
    assert again["round"] == 2 and [i["frame"] for i in again["items"] if i["kind"] == "changed"] == [0]
    assert review.status(MID, source, INFO)["status"] == "open"


def test_fixed_item_adds_one_followup_around_it_instead_of_random_samples(source):
    frames = frames_with_counts([2] * 40)
    rid = "arev_followup"
    items = review.build_items(rid, frames, [])
    with db.connect() as c:
        c.execute(
            "INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, "followup-media", 1, "open", 1, "now", review.revision(frames), json.dumps(frames), json.dumps(items)),
        )
    samples = [i for i in items if i["kind"] == "sample"]
    first = samples[0]
    current = json.loads(json.dumps(frames))
    current[first["frame"]][1]["bbox"] = [12, 10, 16, 14]  # The reviewer shrank object 2 there.
    state = review.check("followup-media", rid, first["id"], "fixed", 1, current)
    followups = [i for i in state["items"] if i["kind"] == "followup"]
    assert len(followups) == 1 and not state["escalated"]
    f = followups[0]
    assert f["followupOf"] == first["id"] and f["objectIds"] == [2]
    assert (f["startFrame"], f["endFrame"]) == (max(0, first["frame"] - 5), min(39, first["frame"] + 5))
    # Re-marking the same item, or an honest "ok", adds nothing more.
    state = review.check("followup-media", rid, first["id"], "fixed", 1, current)
    state = review.check("followup-media", rid, samples[1]["id"], "ok", 1, current)
    assert len([i for i in state["items"] if i["kind"] == "followup"]) == 1
    assert len(state["items"]) == len(items) + 1
    # Several corrections never add random frames.
    for s in samples[1:4]:
        state = review.check("followup-media", rid, s["id"], "fixed", 1, current)
    assert {i["id"] for i in state["items"] if i["kind"] == "sample"} == {i["id"] for i in samples}


def test_pass_rejects_frames_that_are_still_unknown(source):
    state = review.start(MID, source, INFO, 1)
    for item in state["items"]:
        state = review.check(MID, state["reviewId"], item["id"], "fixed" if item["kind"] == "unknown" else "ok", 1)
    with pytest.raises(ReviewError) as e:
        review.pass_review(MID, source, INFO, state["reviewId"], 1)
    assert e.value.code == "UNKNOWN_FRAMES"


def test_review_api_round_trip(source, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import annotation_review_routes as routes
    from app.auth import sign_jwt
    monkeypatch.setattr(routes, "media_context", lambda mid, allow_tracking=False: (source, source / "video.avi", INFO))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    base = f"/api/annotation-review/media/{MID}"
    assert client.get(base).status_code == 401
    client.headers["Authorization"] = "Bearer " + sign_jwt({"uid": 1})
    assert client.get(base).json()["status"] == "none"
    state = client.post(base + "/start").json()
    rid = state["review"]["reviewId"]
    assert state["status"] == "open" and state["review"]["checked"] == 0
    for item in state["review"]["items"]:
        result = "empty" if item["kind"] == "unknown" else "ok"
        response = client.put(f"{base}/items/{item['id']}", json={"reviewId": rid, "result": result})
        assert response.status_code == 200, response.text
    assert client.put(f"{base}/items/x", json={"reviewId": rid, "result": "maybe"}).status_code == 422
    assert client.post(base + "/pass", json={"reviewId": "arev_other"}).json()["code"] == "REVIEW_NOT_CURRENT"
    done = client.post(base + "/pass", json={"reviewId": rid})
    assert done.status_code == 200 and done.json()["status"] == "passed"
    assert client.put(f"{base}/items/x", json={"reviewId": rid, "result": "ok"}).json()["code"] == "REVIEW_CLOSED"


def test_video_without_saved_workspace_is_not_ready_rather_than_failing(source):
    (source / "workspace_state.json").unlink()
    state = review.status(MID, source, INFO)
    assert state["status"] == "none" and "先在标注页" in state["notReady"]
    with pytest.raises(ReviewError) as e:
        review.start(MID, source, INFO, 1)
    assert e.value.code == "SOURCE_UNAVAILABLE"


def test_many_identity_findings_remain_evidence_not_tasks():
    """Identity-only evidence cannot flood the checklist, even with many objects."""
    rows = [{"source_frame_index": f, "objects": [], "reviewEvidence": {"findings": [
        {"objectId": oid, "kind": "endpoint_disagreement", "message": "long sentence"} for oid in range(1, 28)]}}
        for f in range(25)]
    items = review.doubt_items(rows)
    assert items == []
    assert sum(len(r["reviewEvidence"]["findings"]) for r in rows) == 675


def missing_row(fi, boxes):
    return dict(source_frame_index=fi, objects=[], reviewEvidence=dict(findings=[
        dict(kind='unlabelled_candidate', objectId=None, bbox=b, message='候选') for b in boxes]))


def reverse_row(fi, tail, box):
    return dict(source_frame_index=fi, objects=[], reviewEvidence=dict(findings=[
        dict(kind='endpoint_identity_uncertain', objectId=None, tailObjectId=tail, bbox=box)]))


HEAD=[100,100,120,120]
HEAD_OBJ=dict(objectId=1,bbox=HEAD,classKey='sperm')


def evidence_rows(count=25, heads=None):
    heads=heads or {}
    return [dict(source_frame_index=fi, _reviewObjects=heads.get(fi,[]), objects=[],
                 reviewEvidence=dict(findings=[])) for fi in range(count)]


def test_reverse_only_evidence_becomes_one_continuous_item():
    rows=evidence_rows(15)
    for fi in range(10,15):
        rows[fi]['reviewEvidence']['findings']=[dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=9,bbox=[300,300,320,320])]
    items=review.doubt_items(rows,info={'width':1000,'height':800},frames_list=[[HEAD_OBJ] for _ in range(15)])
    assert len(items)==1
    item=items[0]
    assert item['category']=='missing' and item['source']=='reverse'
    assert (item['startFrame'],item['endFrame'])==(10,14) and item['anchor']==[300,300,320,320]
    assert [o['frameIndex'] for o in item['observations']]==[10,11,12,13,14]
    assert all(len(o['candidates'])==1 and not o['objectIds'] for o in item['observations'])


def test_reverse_evidence_merges_small_covered_gap_but_not_a_closed_gap():
    rows=evidence_rows(15)
    for fi in range(10,15):
        rows[fi]['reviewEvidence']['findings']=[dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=9,bbox=[300,300,320,320])]
    overlap=dict(objectId=4,bbox=[300,300,320,320],classKey='sperm')
    rows[12]['_reviewObjects']=[overlap]
    items=[i for i in review.doubt_items(rows,info={'width':1000,'height':800},frames_list=[[HEAD_OBJ] for _ in range(15)]) if i['category']=='missing']
    assert len(items)==1 and [o['frameIndex'] for o in items[0]['observations']]==[10,11,13,14]
    for row in rows[10:15]:
        row['_reviewObjects']=[overlap]
    assert review.doubt_items(rows,info={'width':1000,'height':800},frames_list=[[HEAD_OBJ] for _ in range(15)])==[]


def test_reverse_evidence_rejects_edge_oversize_and_single_frame():
    head_boxes=[obj(1)]
    edge=evidence_rows(3)
    edge[0]['reviewEvidence']['findings']=[dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=9,bbox=[2,100,18,116])]
    edge[1]['reviewEvidence']['findings']=[dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=9,bbox=[2,100,18,116])]
    assert review.doubt_items(edge,info={'width':1000,'height':800},frames_list=[[obj(1)] for _ in range(3)])==[]
    large=evidence_rows(3)
    for fi in range(2):
        large[fi]['reviewEvidence']['findings']=[dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=9,bbox=[300,300,500,500])]
    assert review.doubt_items(large,info={'width':1000,'height':800},frames_list=[[obj(1)] for _ in range(3)])==[]
    single=evidence_rows(3)
    single[0]['reviewEvidence']['findings']=[dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=9,bbox=[300,300,320,320])]
    assert review.doubt_items(single,info={'width':1000,'height':800},frames_list=[[obj(1)] for _ in range(3)])==[]


def detector_row(fi, box):
    return dict(source_frame_index=fi, objects=[], reviewEvidence=dict(findings=[
        dict(kind='unlabelled_candidate',objectId=None,bbox=box,score=.8)]))


def test_detector_single_point_is_dropped_and_motion_separates_missing_from_static():
    frames=[[HEAD_OBJ] for _ in range(10)]
    assert review.doubt_items([detector_row(0,[10,10,20,20])],info={'width':1000,'height':800},frames_list=frames)==[]
    moving=review.doubt_items([detector_row(0,[10,10,20,20]),detector_row(5,[100,10,110,20])],
                              info={'width':1000,'height':800},frames_list=frames)
    assert len(moving)==1 and moving[0]['category']=='missing' and moving[0]['source']=='detector'
    assert [o['frameIndex'] for o in moving[0]['observations']]==[0,5]
    static=review.doubt_items([detector_row(0,[10,10,20,20]),detector_row(5,[10,10,20,20])],
                              info={'width':1000,'height':800},frames_list=frames)
    assert len(static)==1 and static[0]['category']=='static' and static[0]['labels']==['静止目标']


def test_detector_candidate_inside_reverse_path_only_confirms_it():
    rows=evidence_rows(10)
    rows[0]['reviewEvidence']['findings']=[dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=9,bbox=[300,300,320,320]),
                                           dict(kind='unlabelled_candidate',objectId=None,bbox=[300,300,320,320],score=.8)]
    rows[1]['reviewEvidence']['findings']=[dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=9,bbox=[300,300,320,320])]
    items=review.doubt_items(rows,info={'width':1000,'height':800},frames_list=[[HEAD_OBJ] for _ in range(10)])
    assert len(items)==1 and items[0]['source']=='reverse' and items[0]['detectorAgrees'] is True


def test_adopt_object_id_uses_terminal_alias_and_avoids_duplicate_ids():
    rows=evidence_rows(4,{1:[obj(3,700),obj(7,800)]})
    for fi in range(2):
        rows[fi]['reviewEvidence']['findings']=[dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=9,bbox=[300,300,320,320])]
    rows[3]['reviewEvidence']['endpointCheck']=dict(state='checked',terminalAnnotations=[],matches={'9':3})
    frames_list=[rows[0]['_reviewObjects'],rows[1]['_reviewObjects'],[HEAD_OBJ],[HEAD_OBJ]]
    items=[i for i in review.doubt_items(rows,info={'width':1000,'height':800},frames_list=frames_list) if i['category']=='missing']
    assert len(items)==1 and items[0]['adoptObjectId']==8


def test_track_ended_requires_missing_identity_inside_the_frame():
    frames=[[obj(1)] if fi<8 else [] for fi in range(10)]
    rows=[dict(source_frame_index=fi,_reviewObjects=frames[fi],objects=[],reviewEvidence=dict(findings=[])) for fi in range(10)]
    items=review.doubt_items(rows,info={'width':1000,'height':800},frames_list=frames)
    ended=[i for i in items if i.get('source')=='track_ended']
    assert len(ended)==1 and ended[0]['objectId']==1 and ended[0]['lastFrame']==7
    assert (ended[0]['frame'],ended[0]['endFrame'])==(8,9) and ended[0]['lastBox']==[10,10,18,18]
    rows[7]['reviewEvidence']['boundaryExit']=dict(exitedObjectIds=[1])
    assert not [i for i in review.doubt_items(rows,info={'width':1000,'height':800},frames_list=frames) if i.get('source')=='track_ended']


def test_nonactionable_identity_evidence_stays_out_of_the_checklist():
    rows=[dict(source_frame_index=0,_reviewObjects=[obj(1)],objects=[],reviewEvidence=dict(findings=[
        dict(kind='endpoint_identity_uncertain',objectId=1,bbox=[10,10,18,18],tailObjectId=9),
        dict(kind='joint_uncertain',objectId=1,bbox=[10,10,18,18]),
        dict(kind='subject_identity',objectId=1,bbox=[10,10,18,18]),
        dict(kind='backward_disagreement',objectId=1,bbox=[10,10,18,18])]))]
    assert review.doubt_items(rows,info={'width':1000,'height':800},frames_list=[[obj(1)]])==[]


def test_missing_candidate_shared_by_multiple_boxes_is_association_not_missing():
    raw=missing_row(0,[[10,10,20,20]])
    raw['objects']=[dict(object_id=1,source='ai',bbox=[10,10,20,20]),dict(object_id=2,source='ai',bbox=[11,10,21,20])]
    before=json.dumps(raw,sort_keys=True)
    items=review.doubt_items([raw])
    assert len(items)==1 and items[0]['category']=='association' and items[0]['objectIds']==[1,2]
    assert json.dumps(raw,sort_keys=True)==before


def test_candidate_matching_is_independent_of_endpoint_tolerance(monkeypatch):
    from app import endpoint_tracking
    monkeypatch.setitem(endpoint_tracking.ENDPOINT_REVIEW_TOLERANCE,'iou',.01)
    monkeypatch.setattr(endpoint_tracking,'endpoint_disagreement_requires_review',lambda f:False)
    assert review.candidate_overlaps([10,10,20,20],[9,9,21,21])
    assert not review.candidate_overlaps([10,10,20,20],[100,100,110,110])


def test_unlocatable_candidate_stays_an_explicit_check_failure():
    items=review.doubt_items([missing_row(0,[[10,10,10,20]])])
    assert len(items)==1 and items[0]['category']=='check'


def test_all_nine_regions_use_original_image_dimensions():
    boxes=[[x-5,y-5,x+5,y+5] for y in [50,150,250] for x in [50,150,250]]
    rows=[]
    for fi in range(2):
        row=dict(source_frame_index=fi,objects=[],_reviewObjects=[obj(1,140)],reviewEvidence=dict(findings=[
            dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=i+1,bbox=box) for i,box in enumerate(boxes)]))
        rows.append(row)
    items=review.doubt_items(rows,info={'width':300,'height':300},frames_list=[[obj(1,140)]]*2)
    assert [i['region'] for i in items]==['左上','上方','右上','左侧','中央','右侧','左下','下方','右下']


def test_sampling_and_coverage_scale_with_video_length():
    short=frames_with_counts([5]*25)
    short_items=review.build_items('arev_short',short,[])
    assert len([i for i in short_items if i['kind']=='sample'])==5
    assert [i['frame'] for i in short_items if i['kind']=='coverage']==[6,12,18]
    long=frames_with_counts([5]*1000)
    long_items=review.build_items('arev_long',long,[])
    assert len([i for i in long_items if i['kind']=='sample'])==20
    assert len([i for i in long_items if i['kind']=='coverage'])==10
    assert review.sample_count(25)==5 and review.sample_count(25,True)==3


def test_round_reports_last_actor_and_activity(source):
    state=review.start(MID,source,INFO,1)
    assert state['lastActorId']==1 and state['lastActivityAt']==state['startedAt']
    item=state['items'][0]
    updated=review.check(MID,state['reviewId'],item['id'],'empty' if item['kind']=='unknown' else 'ok',3)
    assert updated['lastActorId']==3 and updated['lastActivityAt']


def test_status_cache_uses_revision_stamp_and_pass_reads_fresh(source,monkeypatch):
    review.clear_load_cache()
    original=review.annotation.source
    calls=[]
    def counted(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(review.annotation,'source',counted)
    review.status(MID,source,INFO)
    review.status(MID,source,INFO)
    assert len(calls)==1
    edit_manual(source,31)
    review.status(MID,source,INFO)
    assert len(calls)==2
    review.clear_load_cache()


def test_not_target_is_limited_to_missing_and_static_items(source):
    state=review.start(MID,source,INFO,1)
    position=next(i for i in state['items'] if i['kind']=='coverage')
    with pytest.raises(ReviewError) as error:
        review.check(MID,state['reviewId'],position['id'],'not_target',1)
    assert error.value.code=='INVALID_RESULT'


def test_not_target_records_and_revokes_same_transaction_exclusions():
    media='exclusion-media-'+uuid.uuid4().hex;rid='arev_'+uuid.uuid4().hex;frames=[[HEAD_OBJ] for _ in range(12)]
    item=dict(id='doubt-reverse',kind='doubt',category='missing',source='reverse',startFrame=2,endFrame=4,frame=2,
              objectIds=[],objectId=None,anchor=[300,300,320,320],candidates=[[300,300,320,320]],labels=['可能漏标'],
              observations=[dict(frameIndex=fi,objectIds=[],candidates=[[300,300,320,320]]) for fi in (2,3,4)])
    with db.connect() as c:
        review.migrate(c)
        c.execute("INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json) VALUES (?,?,?,?,?,?,?,?,?)",
                  (rid,media,1,'open',1,'now',review.revision(frames),json.dumps(frames),json.dumps([item])))
    review.check(media,rid,item['id'],'not_target',7)
    review.check(media,rid,item['id'],'not_target',7)
    with db.connect() as c:
        rows=c.execute("SELECT * FROM annotation_review_exclusions WHERE media_id=? AND item_id=?",(media,item['id'])).fetchall()
        expected=[dict(frameIndex=o['frameIndex'],bbox=o['candidates'][0]) for o in item['observations']]
        assert len(rows)==1 and rows[0]['revoked_at'] is None and json.loads(rows[0]['frames_json'])==expected
        excluded=review.active_exclusions(c,media)
    rows=[reverse_row(fi,9,[300,300,320,320]) for fi in (2,3,4)]
    assert review.doubt_items(rows,info={'width':1000,'height':800},frames_list=frames,exclusions=excluded)==[]
    review.check(media,rid,item['id'],'fixed',7)
    with db.connect() as c:
        row=c.execute("SELECT * FROM annotation_review_exclusions WHERE media_id=? AND item_id=?",(media,item['id'])).fetchone()
        assert row['revoked_at'] and row['revoked_by']==7
        excluded=review.active_exclusions(c,media)
    assert len(review.doubt_items(rows,info={'width':1000,'height':800},frames_list=frames,exclusions=excluded))==1


def test_static_exclusion_matches_same_location_on_any_frame():
    exclusion=[dict(source='static',frames=[dict(frameIndex=50,bbox=[300,300,320,320])])]
    rows=[detector_row(0,[300,300,320,320]),detector_row(5,[300,300,320,320])]
    frames=[[HEAD_OBJ] for _ in range(10)]
    assert review.doubt_items(rows,info={'width':1000,'height':800},frames_list=frames,exclusions=exclusion)==[]


def test_already_boxed_answer_is_fixed_only_when_the_reviewer_edited_those_frames():
    media='boxed-media-'+uuid.uuid4().hex;rid='arev_'+uuid.uuid4().hex;frames=[[HEAD_OBJ] for _ in range(6)]
    item=dict(id='doubt-reverse',kind='doubt',category='missing',source='reverse',startFrame=2,endFrame=3,frame=2,
              objectIds=[],objectId=None,anchor=[300,300,320,320],candidates=[[300,300,320,320]],labels=['可能漏标'],
              observations=[dict(frameIndex=fi,objectIds=[],candidates=[[300,300,320,320]]) for fi in (2,3)])
    with db.connect() as c:
        review.migrate(c)
        c.execute("INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json) VALUES (?,?,?,?,?,?,?,?,?)",
                  (rid,media,1,'open',1,'now',review.revision(frames),json.dumps(frames),json.dumps([item])))
    unchanged=json.loads(json.dumps(frames))
    state=review.check(media,rid,item['id'],'ok',7,unchanged)
    assert state['items'][0]['check']['result']=='ok'  # Duplicate detection: the head already had a box.
    edited=json.loads(json.dumps(frames));edited[3]=[HEAD_OBJ,dict(objectId=2,bbox=[300,300,320,320],classKey='sperm')]
    state=review.check(media,rid,item['id'],'ok',7,edited)
    assert state['items'][0]['check']['result']=='fixed'  # The reviewer drew it during this round.
    edited_elsewhere=json.loads(json.dumps(frames));edited_elsewhere[5]=[]
    state=review.check(media,rid,item['id'],'ok',7,edited_elsewhere)
    assert state['items'][0]['check']['result']=='ok'


def test_rereview_ended_tracks_are_scoped_and_not_duplicated():
    old=[[HEAD_OBJ] for _ in range(30)]
    new=json.loads(json.dumps(old));new[5]=[];new[8]=[];new[24]=[]
    rows=evidence_rows(30,heads={fi:[HEAD_OBJ,dict(objectId=2,bbox=[300,300,320,320])] for fi in range(7)})
    for fi in range(7,30):rows[fi]['_reviewObjects']=[HEAD_OBJ]
    rows[19]['_reviewObjects']=[dict(objectId=3,bbox=[400,300,420,320])]
    ended=[i for i in review.build_items('rereview',new,rows,old,info={'width':1000,'height':800}) if i.get('source')=='track_ended']
    assert [(i['objectId'],i['frame']) for i in ended]==[(2,7)]


def test_static_motion_does_not_subtract_swimming_sperm():
    frames=[[dict(objectId=1,bbox=[100+fi*4,100,120+fi*4,120])] for fi in range(6)]
    static=review.doubt_items([detector_row(0,[300,300,320,320]),detector_row(5,[300,300,320,320])],frames_list=frames)
    moving=review.doubt_items([detector_row(0,[300,300,320,320]),detector_row(5,[320,300,340,320])],frames_list=frames)
    assert static[0]['category']=='static'
    assert moving[0]['category']=='missing'


def test_reverse_adoption_ids_reserved_across_conflicting_tracks():
    rows=[]
    for fi in (0,1):
        row=reverse_row(fi,1,[300,300,320,320])
        row['reviewEvidence']['findings'].append(dict(kind='endpoint_identity_uncertain',objectId=None,tailObjectId=2,bbox=[500,300,520,320]))
        rows.append(row)
    frames=[[HEAD_OBJ,dict(objectId=2,bbox=[200,100,220,120])]]*2
    items=review.doubt_items(rows,frames_list=frames,info={'width':1000,'height':800})
    assert len(items)==2 and len({i['adoptObjectId'] for i in items})==2
    assert all(i['adoptObjectId']>2 for i in items)


def test_detector_links_one_to_one_using_all_distances():
    def finding(x):return dict(kind='unlabelled_candidate',bbox=[x-2,298,x+2,302])
    findings={0:[finding(100),finding(130)],5:[finding(120),finding(135)]}
    items=review.detector_items(findings,{},dict(diagonal=5),[])
    assert len(items)==2
    assert sorted([(i['anchor'][0],i['observations'][-1]['candidates'][0][0]) for i in items])==[(98,118),(128,133)]
    assert all(len({o['frameIndex'] for o in i['observations']})==2 for i in items)


def test_moving_exclusion_does_not_hide_later_static_head():
    box=[300,300,320,320]
    exclusions=[dict(source='detector',frames=[dict(frameIndex=50,bbox=box)])]
    rows=[detector_row(0,box),detector_row(5,box)]
    assert len(review.doubt_items(rows,frames_list=[[HEAD_OBJ]]*10,exclusions=exclusions))==1
    exclusions[0]['source']='static'
    assert review.doubt_items(rows,frames_list=[[HEAD_OBJ]]*10,exclusions=exclusions)==[]


def test_export_decisions_preserve_passed_rounds_and_latest_location_wins():
    media='decision-'+uuid.uuid4().hex
    def item(x):return dict(id='doubt-1',kind='doubt',category='missing',source='reverse',observations=[dict(frameIndex=2,candidates=[[x,300,x+20,320]])])
    with db.connect() as c:
        review.migrate(c)
        for round_no,x,result in [(1,300,'fixed'),(2,500,'not_target'),(3,300,'not_target')]:
            rid=media+str(round_no)
            c.execute("INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json) VALUES (?,?,?,?,?,?,?,?,?)",
                      (rid,media,round_no,'passed',1,'now','revision','[]',json.dumps([item(x)])))
            c.execute('INSERT INTO annotation_review_checks VALUES (?,?,?,?,?)',(rid,'doubt-1',result,1,str(round_no)))
        decisions=review.review_decisions(c,media,'sha')
    assert sorted((d['bbox'][0],d['result']) for d in decisions)==[(300,'not_target'),(500,'not_target')]


def test_static_exclusions_are_typed_and_legacy_records_remain_scoped():
    media='static-'+uuid.uuid4().hex;rid=media+'round'
    item=dict(id='doubt-1',kind='doubt',category='static',source='detector',frame=0,startFrame=0,endFrame=5,
              observations=[dict(frameIndex=0,candidates=[[300,300,320,320]])])
    with db.connect() as c:
        review.migrate(c)
        c.execute("INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json) VALUES (?,?,?,?,?,?,?,?,?)",
                  (rid,media,1,'open',1,'now','revision','[]',json.dumps([item])))
    review.check(media,rid,item['id'],'not_target',1)
    with db.connect() as c:
        assert review.active_exclusions(c,media)[0]['source']=='static'
        c.execute("UPDATE annotation_review_exclusions SET source='detector' WHERE review_id=?",(rid,))
        assert review.active_exclusions(c,media)[0]['source']=='static'


def test_export_decisions_keep_archived_checks_but_newer_ok_supersedes_them():
    media='archived-'+uuid.uuid4().hex
    item=dict(id='doubt-1',kind='doubt',category='missing',source='reverse',observations=[dict(frameIndex=2,candidates=[[300,300,320,320]])])
    archived=[dict(items=[item],checks={'doubt-1':dict(result='not_target')})]
    with db.connect() as c:
        review.migrate(c)
        c.execute("INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json,checklist_history_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (media,media,1,'passed',1,'now','revision','[]','[]',json.dumps(archived)))
        assert len(review.review_decisions(c,media,'sha'))==1
        c.execute("INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json) VALUES (?,?,?,?,?,?,?,?,?)",
                  (media+'2',media,2,'passed',1,'now','revision','[]',json.dumps([item])))
        c.execute('INSERT INTO annotation_review_checks VALUES (?,?,?,?,?)',(media+'2','doubt-1','ok',1,'now'))
        assert review.review_decisions(c,media,'sha')==[]


def track(boxes, oid=1):
    return [[{"objectId": oid, "bbox": b, "classKey": "sperm"}] if b else [] for b in boxes]


def plain_rows(frames):
    return [dict(source_frame_index=fi,_reviewObjects=frames[fi] or [],objects=[],reviewEvidence=dict(findings=[])) for fi in range(len(frames))]


GEOMETRY_INFO = {'width': 1000, 'height': 800}


def test_box_inflated_by_debris_is_one_size_item_at_the_worst_frame():
    normal = [100, 100, 110, 110]
    boxes = [normal] * 20
    boxes[8] = [100, 100, 116, 118]  # Debris merged into the mask for two frames.
    boxes[9] = [100, 100, 118, 120]
    frames = track(boxes)
    items = [i for i in review.doubt_items(plain_rows(frames), info=GEOMETRY_INFO, frames_list=frames) if i.get('category') == 'size']
    assert len(items) == 1
    item = items[0]
    assert (item['startFrame'], item['endFrame'], item['frame']) == (8, 9, 9) and item['objectIds'] == [1]
    assert item['source'] == 'size_jump' and item['ratio'] >= 1.6


def test_manual_boxes_steady_tracks_and_edge_clipping_are_not_size_items():
    normal = [100, 100, 110, 110]
    boxes = [normal] * 20
    boxes[8] = [100, 100, 118, 120]
    frames = track(boxes)
    frames[8][0]['source'] = 'manual'  # A human chose this padding on purpose.
    assert not [i for i in review.doubt_items(plain_rows(frames), info=GEOMETRY_INFO, frames_list=frames) if i.get('category') == 'size']
    edge = [[0, 100, 10, 110]] * 10 + [[0, 100, 4, 110]] * 2 + [None] * 3
    frames = track(edge)
    assert not [i for i in review.doubt_items(plain_rows(frames), info=GEOMETRY_INFO, frames_list=frames) if i.get('category') == 'size']


def test_object_far_above_clip_head_size_is_listed_once():
    small = [[{"objectId": oid, "bbox": [200 * oid, 300, 200 * oid + 10, 310], "classKey": "sperm"} for oid in (1, 2, 3)] for _ in range(12)]
    for frame in small:
        frame.append({"objectId": 4, "bbox": [700, 300, 716, 320], "classKey": "sperm"})
    items = [i for i in review.doubt_items(plain_rows(small), info=GEOMETRY_INFO, frames_list=small) if i.get('category') == 'size']
    assert [(i['source'], i['objectIds']) for i in items] == [('oversized', [4])]
    assert items[0]['startFrame'] == items[0]['endFrame']


def test_track_ending_at_frame_edge_lists_its_last_frames_unless_exit_was_confirmed():
    boxes = [[400 - 40 * k, 300, 410 - 40 * k, 310] for k in range(11)] + [None] * 4  # Swims out to the left.
    boxes[10] = [0, 300, 10, 310]
    frames = track(boxes)
    rows = plain_rows(frames)
    items = review.doubt_items(rows, info=GEOMETRY_INFO, frames_list=frames)
    exits = [i for i in items if i.get('category') == 'exit']
    assert len(exits) == 1 and (exits[0]['startFrame'], exits[0]['endFrame'], exits[0]['frame']) == (8, 10, 10)
    assert exits[0]['objectId'] == 1 and not [i for i in items if i.get('source') == 'track_ended']
    rows[10]['reviewEvidence']['boundaryExit'] = dict(exitedObjectIds=[1])
    assert not [i for i in review.doubt_items(rows, info=GEOMETRY_INFO, frames_list=frames) if i.get('category') == 'exit']
    inside = track([[400, 300, 410, 310]] * 10 + [None] * 3)
    assert not [i for i in review.doubt_items(plain_rows(inside), info=GEOMETRY_INFO, frames_list=inside) if i.get('category') == 'exit']
