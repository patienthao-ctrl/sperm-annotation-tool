"""On-demand, click-constrained head proposals. No annotation writes or downloads."""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from threading import Lock

log = logging.getLogger('review.annotation.head_click')
_lock = Lock()
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


def choose(rows, x, y, width, height):
    candidates = []
    for row in rows:
        if len(row) != 5 or not all(math.isfinite(float(v)) for v in row):
            continue
        x1,y1,x2,y2,score = map(float,row)
        x1,y1,x2,y2 = max(0,x1),max(0,y1),min(width,x2),min(height,y2)
        if x2 <= x1 or y2 <= y1 or not 0.15 <= score <= 1:
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


def propose(image, x, y):
    global _model, _model_path
    path = model_path()
    if not path.is_file():
        raise ProposalError('头部检测权重缺失，请获取完整仓库文件或检查自定义模型路径；仍可手动画框。',503)
    if not _lock.acquire(blocking=False):
        raise ProposalError('头部检测正在处理其他请求，请稍后重试。',503)
    try:
        _get_model(path)
        h,w=image.shape[:2]
        left,top=max(0,int(x)-256),max(0,int(y)-256)
        right,bottom=min(w,left+512),min(h,top+512)
        result=_model.predict(image[top:bottom,left:right],imgsz=512,conf=.15,iou=.5,
                              device=os.getenv('HEAD_DETECTOR_DEVICE','cuda:0'),verbose=False)[0]
        rows=[]
        for bbox,confidence in zip(result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist()):
            rows.append([bbox[0]+left,bbox[1]+top,bbox[2]+left,bbox[3]+top,confidence])
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


def detect_edges(image):
    """Native-resolution overlapping perimeter tiles, one shared model lock."""
    path = model_path()
    if not path.is_file():
        raise ProposalError('边缘识别权重缺失，请获取完整仓库文件或检查自定义模型路径。', 503)
    if not _lock.acquire(blocking=False):
        raise ProposalError('头部检测器忙，请稍后重试。', 503)
    try:
        model = _get_model(path)
        h, w = image.shape[:2]
        xs = sorted(set(list(range(0, max(1,w-512),448))+[max(0,w-512)]))
        ys = sorted(set(list(range(0, max(1,h-512),448))+[max(0,h-512)]))
        tiles = sorted(set([(x,0) for x in xs]+[(x,max(0,h-512)) for x in xs]+
                           [(0,y) for y in ys]+[(max(0,w-512),y) for y in ys]))
        rows=[]
        for left,top in tiles:
            result=model.predict(image[top:top+512,left:left+512], imgsz=512, conf=.5, iou=.5,
                                 device=os.getenv('HEAD_DETECTOR_DEVICE','cuda:0'), verbose=False)[0]
            for bbox,score in zip(result.boxes.xyxy.cpu().tolist(),result.boxes.conf.cpu().tolist()):
                rows.append([bbox[0]+left,bbox[1]+top,bbox[2]+left,bbox[3]+top,score])
        return rows
    finally:
        _lock.release()


def detect_frame(image):
    """Full native-resolution image, overlapping tiles; candidates are not labels."""
    path = model_path()
    if not path.is_file():
        raise ProposalError('头部检测权重缺失，自动核验未完成，请检查仓库文件或自定义路径。', 503)
    if not _lock.acquire(blocking=False):
        raise ProposalError('头部检测器忙，自动核验未完成。', 503)
    try:
        model = _get_model(path)
        h,w=image.shape[:2]
        xs=sorted(set(list(range(0,max(1,w-512),448))+[max(0,w-512)]))
        ys=sorted(set(list(range(0,max(1,h-512),448))+[max(0,h-512)]))
        rows=[]
        for top in ys:
            for left in xs:
                result=model.predict(image[top:top+512,left:left+512],imgsz=512,conf=.15,iou=.5,
                    device=os.getenv('HEAD_DETECTOR_DEVICE','cuda:0'),verbose=False)[0]
                for box,score in zip(result.boxes.xyxy.cpu().tolist(),result.boxes.conf.cpu().tolist()):
                    candidate=[box[0]+left,box[1]+top,box[2]+left,box[3]+top,float(score)]
                    rows.append(candidate)
        # Cross-tile NMS only removes near-identical detections, not nested heads.
        kept=[]
        for r in sorted(rows,key=lambda r:-r[4]):
            duplicate=False
            for k in kept:
                inter=max(0,min(r[2],k[2])-max(r[0],k[0]))*max(0,min(r[3],k[3])-max(r[1],k[1]))
                union=(r[2]-r[0])*(r[3]-r[1])+(k[2]-k[0])*(k[3]-k[1])-inter
                if inter/max(union,1e-9)>.8: duplicate=True;break
            if not duplicate:kept.append(r)
        return kept
    finally:
        _lock.release()


def detect_regions(image, boxes):
    """Bounded native tiles around tracked heads, never a new-object discoverer."""
    if not boxes:return []
    path=model_path()
    if not path.is_file():raise ProposalError('综合标注的头部检测权重缺失，请获取完整仓库文件或检查自定义路径。',503)
    if not _lock.acquire(timeout=30):raise ProposalError('头部检测器忙，综合标注未完成，请重试。',503)
    try:
        model=_get_model(path);h,w=image.shape[:2];tiles=set()
        for b in boxes:
            x,y=(b[0]+b[2])/2,(b[1]+b[3])/2
            tiles.add((max(0,min(max(0,w-512),int((x-32)//448)*448)),max(0,min(max(0,h-512),int((y-32)//448)*448))))
        tiles=sorted(tiles);rows=[]
        for offset in range(0,len(tiles),4):
            batch=tiles[offset:offset+4]
            results=model.predict([image[y:y+512,x:x+512] for x,y in batch],imgsz=512,conf=.15,iou=.5,
                                  device=os.getenv('HEAD_DETECTOR_DEVICE','cuda:0'),verbose=False)
            if len(results)!=len(batch):raise ProposalError('头部检测返回不完整，综合标注未完成。',503)
            for (x,y),result in zip(batch,results):
                boxes=result.boxes.xyxy.cpu().tolist();scores=result.boxes.conf.cpu().tolist()
                if len(boxes)!=len(scores):raise ProposalError('头部检测返回结构无效，综合标注未完成。',503)
                for b,s in zip(boxes,scores):
                    r=[b[0]+x,b[1]+y,b[2]+x,b[3]+y,float(s)]
                    if not any(_region_iou(r,k)>.8 for k in rows):rows.append(r)
        return rows
    finally:_lock.release()


def _region_iou(a,b):
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return inter/max(1e-9,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)
