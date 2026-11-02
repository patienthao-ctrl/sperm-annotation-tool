"""Joint output behavior, independent from UI strings or implementation scores."""
import copy
import pytest


def scenario(n=9):
    rows=[dict(source_frame_index=i,frame_index=i,objects=[dict(object_id=1,bbox=[10+i,10,20+i,20],source='sam3')],reviewEvidence=dict(state='checked',findings=[])) for i in range(n)]
    reverse={i:{91:[10+i,10,20+i,20]} for i in range(n)}
    candidates={i:[[10+i,10,20+i,20,.95]] for i in range(n)}
    return rows,reverse,candidates


def run(rows,reverse,candidates,**kwargs):
    from app.joint_tracking import combine_tracks
    return combine_tracks(rows,reverse,{'91':1},candidates,width=200,height=100,**kwargs)


def test_forward_drift_is_actually_corrected_in_output():
    rows,reverse,candidates=scenario()
    for i in range(3,6):rows[i]['objects'][0]['bbox']=[100+i,10,110+i,20]
    result=run(rows,reverse,candidates)
    assert result['state']=='checked' and result['changedObjects']>=3
    for i in range(3,6):assert rows[i]['objects'][0]['bbox']==pytest.approx(reverse[i][91])
    assert all(len(r['objects'])==1 for r in rows)


def test_backward_drift_does_not_damage_correct_forward():
    rows,reverse,candidates=scenario();before=copy.deepcopy(rows)
    for i in range(3,6):reverse[i][91]=[100+i,10,110+i,20]
    run(rows,reverse,candidates)
    assert [r['objects'] for r in rows]==[r['objects'] for r in before]


def test_detector_refines_two_loose_head_boxes_without_averaging_heads():
    rows,reverse,candidates=scenario()
    for i in range(1,8):
        rows[i]['objects'][0]['bbox']=[7+i,7,23+i,23]
        reverse[i][91]=[8+i,8,22+i,22]
    run(rows,reverse,candidates)
    for i in range(2,7):assert rows[i]['objects'][0]['bbox']==pytest.approx(candidates[i][0][:4])


def test_existing_identity_recovered_from_reverse_not_new_object():
    rows,reverse,candidates=scenario()
    for i in range(3,6):rows[i]['objects']=[]
    run(rows,reverse,candidates)
    for i in range(3,6):assert rows[i]['objects'][0]['object_id']==1 and rows[i]['objects'][0]['bbox']==reverse[i][91]


def test_detector_absence_never_deletes_manual_scope_or_fabricates_a_box():
    rows,reverse,_=scenario();candidates={i:[] for i in range(9)}
    before=copy.deepcopy(rows);run(rows,reverse,candidates)
    assert [r['objects'] for r in rows]==[r['objects'] for r in before]
    rows,reverse,candidates=scenario();rows[4]['objects']=[];reverse[4]={}
    run(rows,reverse,candidates)
    assert rows[4]['objects']==[]  # detection alone cannot create an interior object


def test_manual_intermediate_geometry_is_preserved():
    rows,reverse,candidates=scenario();manual={4:{1:[80,10,90,20]}}
    run(rows,reverse,candidates,manual=manual)
    assert rows[4]['objects'][0]['bbox']==manual[4][1]


def test_unmatched_detector_does_not_expand_scope():
    rows,reverse,candidates=scenario()
    for i in candidates:candidates[i].append([160,40,170,50,.999])
    run(rows,reverse,candidates)
    assert all([o['object_id'] for o in r['objects']]==[1] for r in rows)


def test_natural_overlap_does_not_merge_two_identities():
    rows,reverse,candidates=scenario()
    for i,r in enumerate(rows):
        r['objects'].append(dict(object_id=2,bbox=[15+i,10,25+i,20],source='sam3'))
        reverse[i][92]=[15+i,10,25+i,20]
        candidates[i]=[[12+i,10,22+i,20,.99]]  # ambiguous single candidate
    from app.joint_tracking import combine_tracks
    combine_tracks(rows,reverse,{'91':1,'92':2},candidates,width=200,height=100)
    assert all([o['object_id'] for o in r['objects']]==[1,2] for r in rows)
    assert all(r['objects'][0]['bbox']!=r['objects'][1]['bbox'] for r in rows)


@pytest.mark.parametrize('bad',[[1,2,float('nan'),4,.9],[1,2,3,4,1.5]])
def test_malformed_detector_output_fails_before_any_row_changes(bad):
    rows,reverse,candidates=scenario();before=copy.deepcopy(rows);candidates[4]=[bad]
    with pytest.raises(ValueError):run(rows,reverse,candidates)
    assert rows==before


def test_native_frame_or_detector_failure_preserves_input_and_releases_reader(monkeypatch):
    import numpy as np
    from app import joint_tracking,head_click,video_frames
    rows,reverse,candidates=scenario();before=copy.deepcopy(rows);released=[]
    class Reader:
        def __init__(self):self.index=0
        def get(self,prop):return 200 if prop==3 else 100
        def read(self):
            self.index+=1
            return (False,None) if self.index==5 else (True,np.full((100,200,3),self.index-1,dtype='uint8'))
        def release(self):released.append(True)
    monkeypatch.setattr(video_frames,'open_capture_at',lambda *a:Reader())
    monkeypatch.setattr(head_click,'detect_regions',lambda image,boxes:candidates[int(image[0,0,0])])
    endpoint=dict(state='checked',matches={'91':1},anchorHash='x')
    with pytest.raises(ValueError):joint_tracking.synthesize_video('fake',rows,reverse,endpoint,{})
    assert rows==before and released
    class Good(Reader):
        def read(self):self.index+=1;return True,np.full((100,200,3),self.index-1,dtype='uint8')
    monkeypatch.setattr(video_frames,'open_capture_at',lambda *a:Good())
    def fail(*a):raise RuntimeError('detector unavailable')
    monkeypatch.setattr(head_click,'detect_regions',fail)
    with pytest.raises(RuntimeError):joint_tracking.synthesize_video('fake',rows,reverse,endpoint,{})
    assert rows==before
    monkeypatch.setattr(head_click,'detect_regions',lambda image,boxes:candidates[int(image[0,0,0])])
    events=[]
    assert joint_tracking.synthesize_video('fake',rows,reverse,endpoint,{},progress=lambda **p:events.append(p))['state']=='checked'
    # Structured combine progress for the UI bar: every source frame once, in order.
    assert [e['phaseCurrent'] for e in events]==[r['source_frame_index'] for r in rows]
    assert all(e['phase']=='combine' and e['phaseTo']==rows[-1]['source_frame_index'] for e in events)


def test_missing_observation_is_not_an_empty_success():
    rows,reverse,candidates=scenario();before=copy.deepcopy(rows);del candidates[4]
    with pytest.raises(ValueError):run(rows,reverse,candidates)
    assert rows==before


def test_thirty_heads_keep_stable_ids_when_one_direction_drifts():
    from app.joint_tracking import combine_tracks
    rows=[];reverse={};candidates={}
    for fi in range(12):
        objects=[];reverse[fi]={};candidates[fi]=[]
        for oid in range(1,31):
            x=(oid%10)*40+10+fi;y=(oid//10)*40+10;b=[x,y,x+10,y+10]
            objects.append(dict(object_id=oid,bbox=b,source='sam3'))
            reverse[fi][oid+100]=b;candidates[fi].append([*b,.95])
        rows.append(dict(source_frame_index=fi,objects=objects))
    for fi in range(4,8):rows[fi]['objects'][19]['bbox']=[450,150,460,160]
    combine_tracks(rows,reverse,{str(i+100):i for i in range(1,31)},candidates,width=500,height=200)
    for fi,r in enumerate(rows):
        assert [o['object_id'] for o in r['objects']]==list(range(1,31))
        assert r['objects'][19]['bbox']==reverse[fi][120]


def test_joint_endpoint_matching_can_recover_long_forward_drift(window):
    from app.endpoint_tracking import verify_endpoints
    from .test_endpoint_tracking import rows,Engine
    value=rows(12)
    for r in value[3:]:r['objects'][0]['bbox']=[100,0,110,10]
    reverse={}
    result=verify_endpoints('fake',Engine(),value,[dict(object_id=91,bbox=[11,0,21,10])],window_size=4,reverse_observations=reverse,joint_mode=True)
    assert result['matches']=={'91':1} and len(reverse)==12


from .test_endpoint_tracking import window


def test_worker_detector_failure_preserves_geometry_and_records_failure_then_retry(tmp_path,monkeypatch,caplog):
    import json
    from app import main,tracker,endpoint_tracking,joint_tracking
    from app.schemas import TrackRequest
    value,reverse,candidates=scenario()
    (tmp_path/'video.avi').write_bytes(b'isolated-test-video')
    (tmp_path/'media.json').write_text(json.dumps(dict(width=200,height=100,frameCount=9,fps=30)))
    (tmp_path/'workspace_state.json').write_text(json.dumps(dict(updatedBy=1,manualAnnotations=[])))
    original=copy.deepcopy(value)
    monkeypatch.setattr(main,'track_video',lambda *a,**kw:dict(frames=copy.deepcopy(original),lastProcessedFrame=8,reachedVideoEnd=True,anomaly_paused=None))
    monkeypatch.setattr(tracker,'get_sam3_engine',lambda *a:object())
    def verify(*a,**kw):kw['reverse_observations'].update(reverse);return dict(state='checked',matches={'91':1},anchorHash='test')
    monkeypatch.setattr(endpoint_tracking,'verify_endpoints',verify)
    def fail(*a,**kw):
        a[1][0]['objects'][0]['bbox']=[99,1,109,11]
        raise RuntimeError('injected detector failure')
    monkeypatch.setattr(joint_tracking,'synthesize_video',fail)
    req=TrackRequest(mediaId='joint-isolated',startFrame=0,annotations=[],terminalAnnotations=[],reviewMode=True,combineResults=True)
    target=tmp_path/'tracker_results.json'
    main._run_tracking_task('joint-failed',req,tmp_path/'video.avi',tmp_path/'seed.json',target)
    state=main.TASKS.pop('joint-failed');assert state['status']=='failed' and state['jointTracking']['state']=='failed'
    saved=[json.loads(line) for line in target.read_text().splitlines()]
    assert [r['objects'] for r in saved]==[r['objects'] for r in original]
    assert all(r['reviewEvidence']['jointCheck']['state']=='failed' for r in saved)
    assert 'tracking.joint_failed' in caplog.text
    monkeypatch.setattr(main,'media_dir',lambda *a:tmp_path)
    monkeypatch.setattr(main,'find_video',lambda *a:tmp_path/'video.avi')
    assert main.tracking_result('joint-isolated',None,False,{'uid':1})['jointTracking']['state']=='failed'
    def restored(*a,**kw):return joint_tracking.combine_tracks(a[1],reverse,{'91':1},candidates,width=200,height=100)
    monkeypatch.setattr(joint_tracking,'synthesize_video',restored)
    main._run_tracking_task('joint-retry',req,tmp_path/'video.avi',tmp_path/'seed.json',target)
    assert main.TASKS.pop('joint-retry')['status']=='success'
    assert main.tracking_result('joint-isolated',None,False,{'uid':1})['jointTracking']==dict(state='checked',checkedFrames=9)


def test_joint_request_requires_endpoints_before_seed_write(tmp_path,monkeypatch):
    from app import main
    from app.schemas import TrackRequest
    from fastapi import HTTPException
    monkeypatch.setattr(main,'require_tracking_enabled',lambda:None)
    monkeypatch.setattr(main,'tracking_is_busy',lambda *_:False)
    monkeypatch.setattr(main,'media_dir',lambda *a:tmp_path)
    monkeypatch.setattr(main,'find_video',lambda *a:tmp_path/'video.avi')
    monkeypatch.setattr(main,'_probe_video',lambda *a:dict(frameCount=9,width=200,height=100))
    req=TrackRequest(mediaId='joint-isolated',startFrame=0,annotations=[dict(object_id=1,bbox=[1,1,11,11])],combineResults=True)
    with pytest.raises(HTTPException) as error:main.start_tracking(req,{'uid':1},'joint-no-tail')
    assert error.value.status_code==422 and not (tmp_path/'seed_frame_000000.json').exists()


def test_explicit_frame_and_global_deletions_are_not_recovered():
    rows,reverse,candidates=scenario()
    run(rows,reverse,candidates,excluded={4:{1}})
    assert rows[4]['objects']==[]
    assert not rows[4]['reviewEvidence']['jointCheck']['uncertainObjectIds']
    rows,reverse,candidates=scenario()
    run(rows,reverse,candidates,excluded={fi:{1} for fi in range(9)})
    assert all(not r['objects'] for r in rows)


def test_intermittent_detector_does_not_make_head_size_flicker():
    rows,reverse,candidates=scenario(15)
    for fi,row in enumerate(rows):
        row['objects'][0]['bbox']=[7+fi,7,23+fi,23]
        reverse[fi][91]=[7+fi,7,23+fi,23]
        if fi%2==0:candidates[fi]=[]
    run(rows,reverse,candidates)
    sizes=[o['bbox'][2]-o['bbox'][0] for row in rows[2:-2] for o in row['objects']]
    assert max(sizes)-min(sizes)<.001


def test_debris_inflated_tracker_box_shrinks_to_detector_size_when_detector_backs_off():
    rows,reverse,candidates=scenario(15)
    for i in range(6,9):
        # Debris beside the head: both SAM3 directions swallow it, and the
        # detector's confidence on the obscured head drops below 0.7.
        rows[i]['objects'][0]['bbox']=[10+i,10,26+i,28]
        reverse[i][91]=[10+i,10,26+i,28]
        candidates[i]=[[10+i,10,20+i,20,.5]]
    run(rows,reverse,candidates)
    for i in range(6,9):
        b=rows[i]['objects'][0]['bbox']
        assert b[2]-b[0]==pytest.approx(10) and b[3]-b[1]==pytest.approx(10)
        # Centred on the tracker box, never moved onto the debris candidate.
        assert ((b[0]+b[2])/2,(b[1]+b[3])/2)==pytest.approx((18+i,19))


def test_size_candidate_needs_detector_support_and_never_overrides_manual():
    rows,reverse,candidates=scenario(9)
    for i in range(9):
        rows[i]['objects'][0]['bbox']=[10+i,10,26+i,28];reverse[i][91]=[10+i,10,26+i,28]
    for i in range(2,9):candidates[i]=[]
    before=copy.deepcopy(rows)
    run(rows,reverse,candidates)  # Two supported frames only: no typical size, keep tracker geometry.
    assert [r['objects'][0]['bbox'] for r in rows[2:]]==[r['objects'][0]['bbox'] for r in before[2:]]
    rows,reverse,candidates=scenario(12)
    for i in range(6,9):
        rows[i]['objects'][0]['bbox']=[10+i,10,26+i,28];reverse[i][91]=[10+i,10,26+i,28];candidates[i]=[]
    manual={7:{1:[17,10,33,28]}}
    run(rows,reverse,candidates,manual=manual)
    assert rows[7]['objects'][0]['bbox']==pytest.approx([17,10,33,28])
