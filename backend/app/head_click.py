"""On-demand, click-constrained head proposals. No annotation writes or downloads."""
import hashlib
import json
import logging
import math
import os
import time
from collections import deque
from pathlib import Path
from threading import Event, Lock

log = logging.getLogger('review.annotation.head_click')
# Clicks must answer inside the frontend's 30 s request timeout, so its own busy
# message is shown; tracking has no one waiting on it and already did costly work.
INTERACTIVE_WAIT_SECONDS = 20
BACKGROUND_WAIT_SECONDS = 120


class _FifoLock:
    """First come, first served. A plain Lock lets a tracking loop re-acquire
    before a waiting click wakes, so release hands over to the oldest waiter."""
    def __init__(self):
        self._guard = Lock()
        self._waiters = deque()
        self._held = False

    def acquire(self, timeout):
        with self._guard:
            if not self._held:
                self._held = True
                return True
            turn = Event()
            self._waiters.append(turn)
        if turn.wait(timeout):
            return True
        with self._guard:
            if turn.is_set():  # Handed over just as the wait expired.
                return True
            self._waiters.remove(turn)
            return False

    def release(self):
        with self._guard:
            if self._waiters:
                self._waiters.popleft().set()  # Stays held by the next waiter.
            else:
                self._held = False

    def waiting(self):
        with self._guard:
            return len(self._waiters)


_lock = _FifoLock()
_model = None
_model_path = None
BUNDLED_MODEL = Path(__file__).resolve().parent / 'model_assets' / 'head-detector.pt'


def model_path():
    configured = os.getenv('HEAD_DETECTOR_MODEL', '').strip()
    return Path(configured) if configured else BUNDLED_MODEL


def verify_bundled_weight(path):
    if path.resolve() != BUNDLED_MODEL.resolve():
        return
    try:
        record = json.loads(BUNDLED_MODEL.with_suffix('.json').read_text(encoding='utf-8'))
        if path.stat().st_size != record['size']:
            raise ValueError('size mismatch')
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                checksum.update(block)
        if checksum.hexdigest() != record['sha256']:
            raise ValueError('sha256 mismatch')
    except (OSError, ValueError, KeyError) as exc:
        raise ProposalError('默认头部检测权重校验失败，请重新获取完整仓库文件；原标注未修改。', 503) from exc



class ProposalError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def _take_turn(caller, timeout, busy_message):
    """Queue for the shared detector; the caller must release _lock afterwards."""
    started = time.monotonic()
    if not _lock.acquire(timeout):
        log.warning('annotation.head_detector_wait_timeout caller=%s waited_ms=%.0f queued=%s',
                    caller, (time.monotonic()-started)*1000, _lock.waiting())
        raise ProposalError(busy_message, 503)
    waited = time.monotonic()-started
    if waited >= 1:
        log.info('annotation.head_detector_waited caller=%s waited_ms=%.0f', caller, waited*1000)


def detector_device():
    return os.getenv('HEAD_DETECTOR_DEVICE','cuda:0')


def _threshold(name, default):
    try:
        value=float(os.getenv(name,'').strip() or default)
    except ValueError:
        value=default
    return min(1.0,max(0.01,value))


def high_confidence():
    """Score that may start a track or count as a confident head (evaluated at 0.30)."""
    return _threshold('HEAD_DETECTOR_CONF',0.30)


def low_confidence():
    """Score that may only extend an existing track or answer a click (fewer misses)."""
    return min(high_confidence(),_threshold('HEAD_DETECTOR_LOW_CONF',0.10))


INPUT_LONG_SIDE=1024


def _prepare(image):
    """The model only saw whole frames shrunk so the long side is 1024, gray copied to 3 channels."""
    import cv2
    import numpy as np
    gray=image if image.ndim==2 else cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    h,w=gray.shape[:2]
    scale=INPUT_LONG_SIDE/max(h,w)
    if scale!=1:
        size=(max(1,round(w*scale)),max(1,round(h*scale)))
        gray=cv2.resize(gray,size,interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_CUBIC)
    return np.ascontiguousarray(cv2.merge([gray,gray,gray])),(w/gray.shape[1],h/gray.shape[0])


def _predict_full(model,images,conf):
    """Whole-frame inference for one image or a list; rows are [x1,y1,x2,y2,score] in source pixels."""
    single=not isinstance(images,list)
    batch=[images] if single else images
    prepared=[_prepare(image) for image in batch]
    results=model.predict([p[0] for p in prepared] if not single else prepared[0][0],
                          imgsz=INPUT_LONG_SIDE,conf=conf,iou=.7,max_det=500,
                          device=detector_device(),verbose=False)
    if len(results)!=len(batch):
        raise ProposalError('头部检测返回不完整，请重试。',503)
    out=[]
    for (image,(sx,sy)),result,source in zip(prepared,results,batch):
        boxes=result.boxes.xyxy.cpu().tolist();scores=result.boxes.conf.cpu().tolist()
        if len(boxes)!=len(scores):
            raise ProposalError('头部检测返回结构无效，请重试。',503)
        h,w=source.shape[:2];rows=[]
        for b,score in zip(boxes,scores):
            x1,y1,x2,y2=max(0.,b[0]*sx),max(0.,b[1]*sy),min(float(w),b[2]*sx),min(float(h),b[3]*sy)
            if x2>x1 and y2>y1:
                rows.append([x1,y1,x2,y2,float(score)])
        out.append(rows)
    return out[0] if single else out


def choose(rows, x, y, width, height, min_score=None):
    min_score=low_confidence() if min_score is None else min_score
    candidates = []
    for row in rows:
        if len(row) != 5 or not all(math.isfinite(float(v)) for v in row):
            continue
        x1,y1,x2,y2,score = map(float,row)
        x1,y1,x2,y2 = max(0,x1),max(0,y1),min(width,x2),min(height,y2)
        if x2 <= x1 or y2 <= y1 or not min_score <= score <= 1:
            continue
        radius = min(12., max(3., min(x2-x1,y2-y1)*.3))
        distance = math.hypot(max(x1-x,0,x-x2),max(y1-y,0,y-y2))
        if distance > radius:
            continue
        center_distance = math.hypot(x-(x1+x2)/2,y-(y1+y2)/2)
        candidates.append((distance,center_distance,-score,[x1,y1,x2,y2]))
    candidates.sort()
    if not candidates:
        raise ProposalError('点击附近未找到可信头部，请更靠近头部点击，或使用手动画框。')
    best = candidates[0]
    for other in candidates[1:]:
        a,b=best[3],other[3]
        intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
        if intersection/max(union,1e-9) > .65:
            continue  # Same proposed head at slightly different scales.
        if other[0] <= best[0]+2 and other[1] <= best[1]+4:
            raise ProposalError('点击位置靠近多个头部，请点得更靠近目标中心，或手动画框。')
    return {'bbox':best[3], 'confidence':-best[2]}


def _require_weight(message):
    path = model_path()
    if not path.is_file():
        raise ProposalError(message,503)
    return path


def propose(image, x, y):
    """Whole-frame detection at the low threshold, then the head under the click."""
    path=_require_weight('头部检测权重缺失，请获取完整仓库文件或检查自定义模型路径；仍可手动画框。')
    _take_turn('click', INTERACTIVE_WAIT_SECONDS, '头部检测正在处理其他请求，请稍后重试。')
    try:
        rows=_predict_full(_get_model(path),image,low_confidence())
        h,w=image.shape[:2]
        return choose(rows,x,y,w,h)
    finally:
        _lock.release()


def _get_model(path):
    global _model, _model_path
    if _model is None or _model_path != str(path.resolve()):
        verify_bundled_weight(path)
        try:
            os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
            from ultralytics import YOLO
        except ImportError as exc:
            raise ProposalError('头部检测依赖未安装，请按 backend/requirements.txt 安装依赖。',503) from exc
        _model = YOLO(str(path),task='detect')
        _model_path = str(path.resolve())
        log.info('annotation.head_detector_loaded path=%s bundled=%s', path, path.resolve()==BUNDLED_MODEL.resolve())
    return _model


def detect_batch(images, conf=None, caller='prelabel'):
    """Whole frames in one detector turn; callers keep batches small so clicks are not starved."""
    if not images:return []
    path=_require_weight('头部检测权重缺失，自动预标未完成，请检查仓库文件或自定义路径。')
    _take_turn(caller,BACKGROUND_WAIT_SECONDS,'头部检测器长时间繁忙，自动预标未完成，请稍后重试。')
    try:
        return _predict_full(_get_model(path),list(images),low_confidence() if conf is None else conf)
    finally:
        _lock.release()


def detect_edges(image):
    """Whole-frame candidates at 0.5; EntryGate itself decides what counts as entering at the border."""
    path=_require_weight('边缘识别权重缺失，请获取完整仓库文件或检查自定义模型路径。')
    _take_turn('edge_entry', BACKGROUND_WAIT_SECONDS, '头部检测器长时间繁忙，边缘识别未完成，请稍后重试。')
    try:
        return _predict_full(_get_model(path),image,.5)
    finally:
        _lock.release()


def detect_frame(image):
    """Whole frame at the low threshold; candidates are not labels."""
    path=_require_weight('头部检测权重缺失，自动核验未完成，请检查仓库文件或自定义路径。')
    _take_turn('full_frame', BACKGROUND_WAIT_SECONDS, '头部检测器长时间繁忙，自动核验未完成。')
    try:
        return _predict_full(_get_model(path),image,low_confidence())
    finally:
        _lock.release()


REGION_RADIUS=256  # Native pixels, the reach of the former 512 tiles around each head.


def detect_regions(image, boxes):
    """Candidates near tracked heads only, never a new-object discoverer."""
    if not boxes:return []
    path=_require_weight('综合标注的头部检测权重缺失，请获取完整仓库文件或检查自定义路径。')
    _take_turn('joint_regions',BACKGROUND_WAIT_SECONDS,'头部检测器长时间繁忙，综合标注未完成，请重试。')
    try:
        rows=_predict_full(_get_model(path),[image],low_confidence())[0]
    finally:_lock.release()
    radius=REGION_RADIUS
    centers=[((b[0]+b[2])/2,(b[1]+b[3])/2) for b in boxes]
    return [r for r in rows if any(abs((r[0]+r[2])/2-x)<=radius and abs((r[1]+r[3])/2-y)<=radius for x,y in centers)]


def _region_iou(a,b):
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return inter/max(1e-9,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)
