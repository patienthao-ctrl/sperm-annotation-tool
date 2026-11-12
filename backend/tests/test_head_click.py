"""Click proposals never mutate saved annotations or invent fallback boxes."""
import pytest
from app import head_click
from .test_media_reimport import imports, upload
from .test_review_workflow import task


def test_click_tolerance_and_ambiguous_heads():
    assert head_click.choose([[20,20,30,30,.8]], 32,25,64,48)['bbox'] == [20,20,30,30]
    with pytest.raises(head_click.ProposalError):
        head_click.choose([[20,20,30,30,.8],[32,20,42,30,.9]],31,25,64,48)
    with pytest.raises(head_click.ProposalError):
        head_click.choose([],25,25,64,48)
    with pytest.raises(head_click.ProposalError):
        head_click.choose([[0,0,float('nan'),30,.9]],25,25,64,48)


def test_proposal_exact_frame_and_no_persistence(imports, monkeypatch):
    client, video = imports
    mid=upload(client,video).json()['mediaId']
    calls=[]
    def predict(image,x,y):
        calls.append((image.shape,x,y))
        return {'bbox':[20,20,30,30], 'confidence':.8}
    monkeypatch.setattr(head_click,'propose',predict)
    result=client.post('/api/track/head-box/'+mid,json={'frameIndex':1,'x':25,'y':25})
    assert result.status_code==200,result.text
    assert result.json()['frameIndex']==1
    assert calls==[((48,64,3),25,25)]
    assert client.get('/api/track/workspace/'+mid).json().get('manualAnnotations',[])==[]
    assert client.post('/api/track/head-box/'+mid,json={'frameIndex':99,'x':25,'y':25}).status_code==404
    assert client.post('/api/track/head-box/'+mid,json={'frameIndex':1,'x':999,'y':25}).status_code==422
    client.headers.pop('Authorization')
    assert client.post('/api/track/head-box/'+mid,json={'frameIndex':1,'x':25,'y':25}).status_code==401


def test_unavailable_model_recovers_without_writing(imports,monkeypatch):
    client,video=imports
    mid=upload(client,video).json()['mediaId']
    monkeypatch.setenv('HEAD_DETECTOR_MODEL','/missing/head.pt')
    path='/api/track/head-box/'+mid
    response=client.post(path,json={'frameIndex':0,'x':20,'y':20})
    assert response.status_code==503
    monkeypatch.setattr(head_click,'propose',lambda *args:{'bbox':[15,15,25,25],'confidence':.9})
    assert client.post(path,json={'frameIndex':0,'x':20,'y':20}).status_code==200
    assert client.get('/api/track/workspace/'+mid).json().get('manualAnnotations',[])==[]


def test_malformed_duplicate_and_distant_candidates():
    assert head_click.choose([[20,20,30,30,.9],[20.2,20.1,30.1,30.2,.8]],25,25,64,48)['bbox']==[20,20,30,30]
    with pytest.raises(head_click.ProposalError):head_click.choose([[20,20,30,30,.9]],60,45,64,48)
    with pytest.raises(head_click.ProposalError):head_click.choose([[30,20,20,30,.9]],25,25,64,48)


def test_whole_frame_input_matches_training_and_lock_recovers(tmp_path,monkeypatch):
    import numpy as np
    from types import SimpleNamespace
    weight=tmp_path/'head.pt';weight.write_bytes(b'fixture-not-a-model')
    monkeypatch.setenv('HEAD_DETECTOR_MODEL',str(weight))
    class Values:
        def __init__(self,data):self.data=data
        def cpu(self):return self
        def tolist(self):return self.data
    calls=[]
    def predict(image,**kwargs):
        calls.append((image.shape,kwargs['imgsz'],kwargs['conf'],kwargs['iou']))
        # Boxes come back in the resized 1024x768 input; 800x600 was scaled by 1.28.
        return [SimpleNamespace(boxes=SimpleNamespace(xyxy=Values([[502.4,371.2,527.36,396.8]]),conf=Values([.12])))]
    model=SimpleNamespace(predict=predict)
    monkeypatch.setattr(head_click,'_model',model)
    monkeypatch.setattr(head_click,'_model_path',str(weight.resolve()))
    image=np.zeros((600,800,3),dtype=np.uint8)
    expected=pytest.approx([392.5,290.0,412.0,310.0])
    assert head_click.propose(image,400,300)['bbox']==expected
    # Whole frame, long side 1024, gray copied to 3 channels; a 0.12 head still answers a click.
    assert calls==[((768,1024,3),1024,.1,.7)]
    def fail(*args,**kwargs):raise RuntimeError('injected predictor failure')
    model.predict=fail
    with pytest.raises(RuntimeError):head_click.propose(image,400,300)
    model.predict=predict
    assert head_click.propose(image,400,300)['bbox']==expected


def test_thresholds_are_configurable(monkeypatch):
    monkeypatch.delenv('HEAD_DETECTOR_CONF',raising=False);monkeypatch.delenv('HEAD_DETECTOR_LOW_CONF',raising=False)
    assert (head_click.high_confidence(),head_click.low_confidence())==(.3,.1)
    monkeypatch.setenv('HEAD_DETECTOR_CONF','0.4');monkeypatch.setenv('HEAD_DETECTOR_LOW_CONF','0.2')
    assert (head_click.high_confidence(),head_click.low_confidence())==(.4,.2)
    monkeypatch.setenv('HEAD_DETECTOR_LOW_CONF','0.9')  # Never above the confident threshold.
    assert head_click.low_confidence()==.4
    monkeypatch.setenv('HEAD_DETECTOR_CONF','bad')
    assert head_click.high_confidence()==.3


def test_gray_and_small_frames_are_prepared_like_training():
    import numpy as np
    image,scale=head_click._prepare(np.zeros((1500,2048),dtype=np.uint8))
    assert image.shape==(750,1024,3) and scale==(2.0,2.0)
    image,scale=head_click._prepare(np.zeros((300,400,3),dtype=np.uint8))
    assert image.shape==(768,1024,3) and scale==pytest.approx((400/1024,300/768))
