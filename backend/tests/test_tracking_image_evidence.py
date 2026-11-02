"""Native-frame identity/ownership; optical quality is tested by the Probe suite."""
import gc
import importlib
import logging
import weakref

import cv2
import numpy as np
import pytest

from app.services.anomaly_detector import ManualBaseline


def video(path, *, width=96, height=64, count=10, base=20):
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),25,(width,height))
    assert writer.isOpened()
    for fi in range(count):
        image=np.empty((height,width,3),np.uint8)
        image[:]=[base+fi*12,60+fi*5,150-fi*4]
        writer.write(image)
    writer.release()
    return path


def decoded_frame(image, count=20, base=20):
    """Which synthetic frame was read. MJPG is lossy and colours drift by OpenCV
    build (CI's image read [30,65,143] for frame 1), so identify the nearest
    frame instead of a fixed tolerance; neighbours differ by 12 in red."""
    mean=np.array(image['mean'])
    distances=[np.abs(mean-[base+fi*12,60+fi*5,150-fi*4]).max() for fi in range(count)]
    best=int(np.argmin(distances))
    assert distances[best]<6, mean # a decoded frame of this video, not blank/garbage
    return best


def test_frame_identity_tolerates_lossy_colour_drift():
    assert decoded_frame({'mean':[30,65,143]})==1
    assert decoded_frame({'mean':[44,70,142]})==2
    with pytest.raises(AssertionError):
        decoded_frame({'mean':[0,0,0]})


class ProbeStub:
    def __init__(self, **kwargs):
        self.configuration=kwargs
        self.primed=[]
        self.observed=[]

    @staticmethod
    def pixels(image):
        return None if image is None else dict(shape=image.shape,dtype=str(image.dtype),mean=image.mean(axis=(0,1)).tolist())

    def prime(self, frame, image, boxes):
        self.primed.append((frame,self.pixels(image),dict(boxes)))
        return {oid:{'head':{'status':'matched'},'reasons':['trusted_optical_reference']} for oid in boxes}

    def observe(self, frame, image, boxes, masks):
        self.observed.append((frame,self.pixels(image),boxes,masks))
        return {oid:{'status':'unknown' if image is None else 'stub-native-read','frameIndex':frame} for oid in boxes}


@pytest.fixture
def service(monkeypatch):
    module=importlib.import_module('app.services.tracking_image_evidence')
    monkeypatch.setattr(module,'TrackingEvidenceProbe',ProbeStub)
    return module


def baseline(frame=0, *, source='manual'):
    return ManualBaseline([8.,8.,28.,28.],frame,'target',source)


def native(service, path, refs=None, seed=0, **kwargs):
    return service.NativeImageEvidence(path,fps=25,width=kwargs.pop('width',96),height=kwargs.pop('height',64),manual_baselines=refs or {},seed_frame=seed,**kwargs)


def test_grouped_only_explicit_past_human_references_are_primed(service,tmp_path,caplog):
    path=video(tmp_path/'source.avi')
    refs={1:baseline(0),2:baseline(0,source='confirmed-normal'),3:baseline(3),
          4:baseline(4),5:baseline(1,source='ai')}
    with caplog.at_level(logging.INFO,logger='review.tracking'),native(service,path,refs,seed=3) as item:
        assert [(f,set(boxes)) for f,_,boxes in item.probe.primed]==[(0,{1,2}),(3,{3})]
        for fi,image,_ in item.probe.primed:
            assert image['shape']==(64,96,3)
            assert decoded_frame(image)==fi
        assert any('tracking.image_reference' in r.message and 'frame=3' in r.message for r in caplog.records)


def test_ai_seed_without_explicit_baseline_never_primes(service,tmp_path):
    path=video(tmp_path/'source.avi')
    with native(service,path,seed=5) as item:
        boxes={7:[8,8,30,30]};masks={7:object()}
        result=item.observe(5,boxes,masks)
        assert item.probe.primed==[]
        assert item.probe.observed[-1][2] is boxes and item.probe.observed[-1][3] is masks
        assert result[7]['frameIndex']==5


def test_sequential_skips_and_rewind_preserve_native_identity(service,tmp_path,monkeypatch):
    path=video(tmp_path/'source.avi');actual=service.open_capture_at;opened=[]
    def capture(path,index):opened.append(index);return actual(path,index)
    monkeypatch.setattr(service,'open_capture_at',capture)
    with native(service,path,{1:baseline(0)},seed=2) as item:
        for fi in (2,3,6,1,2,9):
            item.observe(fi,{1:[8,8,28,28]},None)
            frame,image,_,_=item.probe.observed[-1]
            assert frame==fi and decoded_frame(image)==fi
    assert opened==[0,2,1]


def test_failed_seek_uses_exact_sequential_fallback_not_zero(service,tmp_path,monkeypatch):
    path=video(tmp_path/'source.avi');actual=cv2.VideoCapture;readers=[]
    class NoSeek:
        def __init__(self,*args):self.cap=actual(*args);self.released=False;readers.append(self)
        def set(self,*args):return False
        def release(self):self.released=True;return self.cap.release()
        def __getattr__(self,name):return getattr(self.cap,name)
    monkeypatch.setattr(cv2,'VideoCapture',NoSeek)
    with native(service,path,{1:baseline(4)},seed=7) as item:
        assert decoded_frame(item.probe.primed[0][1])==4
        item.observe(7,{1:[8,8,28,28]},None)
        assert decoded_frame(item.probe.observed[-1][1])==7
    assert all(reader.released for reader in readers)


def test_missing_reference_frame_is_not_primed_and_missing_observation_is_unknown(service,tmp_path,caplog):
    path=video(tmp_path/'source.avi',count=3)
    with caplog.at_level(logging.WARNING,logger='review.tracking'),native(service,path,{1:baseline(7),2:baseline(0)},seed=7) as item:
        assert [(frame,set(boxes)) for frame,_,boxes in item.probe.primed]==[(0,{2})]
        result=item.observe(7,{1:[8,8,28,28]},None)
        assert result[1]['status']=='unknown' and item.probe.observed[-1][1] is None
        assert len([r for r in caplog.records if 'tracking.image_read_failed' in r.message])>=2


def test_absent_source_does_not_return_empty_normal(service,tmp_path,caplog):
    with caplog.at_level(logging.WARNING,logger='review.tracking'),native(service,tmp_path/'missing.avi',{1:baseline()}) as item:
        assert item.probe.primed==[]
        assert item.observe(1,{1:[8,8,28,28]},None)=={1:{'status':'unknown','frameIndex':1}}
        assert any('tracking.image_read_failed' in r.message for r in caplog.records)


def test_source_replaced_after_initialization_cannot_prime_the_new_file(service,tmp_path):
    path=video(tmp_path/'source.avi');item=native(service,path,{1:baseline()})
    video(tmp_path/'new.avi',base=80).replace(path)
    with item:
        assert item.probe.primed==[]
        item.observe(1,{1:[8,8,28,28]},None)
        assert item.probe.observed[-1][1] is None


def test_source_swap_during_read_drops_frame_and_never_uses_stale_reference(service,tmp_path,monkeypatch,caplog):
    path=video(tmp_path/'source.avi');replacement=video(tmp_path/'replacement.avi',base=80)
    actual=service.open_capture_at;readers=[]
    class SwapOnRead:
        def __init__(self,cap):self.cap=cap;self.released=False;readers.append(self)
        def read(self):
            data=self.cap.read();replacement.replace(path);return data
        def release(self):self.released=True;self.cap.release()
        def __getattr__(self,name):return getattr(self.cap,name)
    with native(service,path,{1:baseline()}) as item:
        monkeypatch.setattr(service,'open_capture_at',lambda p,i:SwapOnRead(actual(p,i)))
        with caplog.at_level(logging.WARNING,logger='review.tracking'):
            item.observe(1,{1:[8,8,28,28]},None)
            item.observe(2,{1:[8,8,28,28]},None)
        assert all(record[1] is None for record in item.probe.observed)
        assert all(reader.released for reader in readers)
        assert any('source_changed' in r.message for r in caplog.records)


@pytest.mark.parametrize('wrong', ['size','dtype','failed','position'])
def test_invalid_decode_passes_none_and_releases_reader(service,tmp_path,monkeypatch,wrong):
    path=video(tmp_path/'source.avi');readers=[]
    class Broken:
        def __init__(self):self.released=False;readers.append(self)
        def read(self):
            if wrong=='failed':return False,None
            if wrong=='position':return True,np.zeros((64,96,3),np.uint8)
            return True,np.zeros((32,48,3),np.uint8) if wrong=='size' else np.zeros((64,96,3),np.float32)
        def get(self,key):return 0 if wrong=='position' else 2
        def release(self):self.released=True
    with native(service,path) as item:
        monkeypatch.setattr(service,'open_capture_at',lambda *a:Broken())
        assert item.observe(1,{1:[8,8,28,28]},None)[1]['status']=='unknown'
    assert all(reader.released for reader in readers)


def test_transient_read_failure_recovers_with_fresh_exact_reader(service,tmp_path,monkeypatch):
    path=video(tmp_path/'source.avi');actual=service.open_capture_at;failed=[]
    class FailOnce:
        def __init__(self):self.released=False;failed.append(self)
        def read(self):return False,None
        def release(self):self.released=True
    with native(service,path,{1:baseline()}) as item:
        monkeypatch.setattr(service,'open_capture_at',lambda *a:FailOnce())
        assert item.observe(1,{1:[8,8,28,28]},None)[1]['status']=='unknown'
        assert all(reader.released for reader in failed)
        monkeypatch.setattr(service,'open_capture_at',actual)
        assert item.observe(2,{1:[8,8,28,28]},None)[1]['status']=='stub-native-read'
        assert decoded_frame(item.probe.observed[-1][1])==2
        assert len(item.probe.primed)==1 # a recovery never learns an AI frame.


def test_far_forward_jump_reopens_instead_of_decoding_every_intermediate_frame(service,tmp_path,monkeypatch):
    path=video(tmp_path/'source.avi',count=20);actual=service.open_capture_at;opened=[]
    def capture(path,index):opened.append(index);return actual(path,index)
    monkeypatch.setattr(service,'open_capture_at',capture)
    with native(service,path) as item:
        item.observe(0,{1:[8,8,28,28]},None)
        item.observe(12,{1:[8,8,28,28]},None)
        assert decoded_frame(item.probe.observed[-1][1])==12
    assert opened==[0,12]


def test_probe_failure_propagates_and_context_still_closes_decoder(service,tmp_path,monkeypatch):
    path=video(tmp_path/'source.avi');actual=service.open_capture_at;readers=[]
    def capture(*args):cap=actual(*args);readers.append(cap);return cap
    monkeypatch.setattr(service,'open_capture_at',capture)
    with pytest.raises(RuntimeError,match='probe processing fault'):
        with native(service,path,{1:baseline()}) as item:
            def fail(*args):raise RuntimeError('probe processing fault')
            item.probe.observe=fail
            item.observe(1,{1:[8,8,28,28]},None)
    assert all(not cap.isOpened() for cap in readers)


def test_context_exception_and_repeated_close_release_owned_capture(service,tmp_path,monkeypatch):
    path=video(tmp_path/'source.avi');actual=service.open_capture_at;readers=[]
    def opened(*args):
        cap=actual(*args);readers.append(cap);return cap
    monkeypatch.setattr(service,'open_capture_at',opened)
    item=native(service,path,{1:baseline()})
    with pytest.raises(RuntimeError,match='consumer fault'):
        with item:
            item.observe(1,{1:[8,8,28,28]},None)
            raise RuntimeError('consumer fault')
    item.close()
    assert all(not cap.isOpened() for cap in readers)
    with pytest.raises(RuntimeError,match='closed'):item.observe(2,{1:[8,8,28,28]},None)


def test_native_4k_reader_keeps_at_most_one_full_frame_alive(service,tmp_path,monkeypatch):
    path=video(tmp_path/'source.avi',width=3840,height=2160,count=3)
    actual=service.open_capture_at;weak_frames=[];peak=[0]
    class Tracked:
        def __init__(self,cap):self.cap=cap
        def read(self):
            ok,image=self.cap.read()
            if image is not None:
                weak_frames.append(weakref.ref(image));peak[0]=max(peak[0],sum(r() is not None for r in weak_frames))
            return ok,image
        def __getattr__(self,name):return getattr(self.cap,name)
    monkeypatch.setattr(service,'open_capture_at',lambda *a:Tracked(actual(*a)))
    with native(service,path,{1:baseline(0),2:baseline(1)},seed=1,width=3840,height=2160) as item:
        for fi in (1,2):item.observe(fi,{1:[8,8,28,28]},None)
        assert all(record[1]['shape']==(2160,3840,3) for record in item.probe.observed)
        gc.collect()
        assert not any(reference() is not None for reference in weak_frames)
    assert peak[0]==1
