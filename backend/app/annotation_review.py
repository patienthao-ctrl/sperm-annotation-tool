"""Annotation review: a checklist over the shared workspace, a pass record, and the export gate.

Reviewers correct boxes in the same workspace the annotator uses; there is no second
copy to compare. A round lists the frames worth a human look (tracking doubts, count
jumps, box size/exit geometry, whole-frame coverage, unknown frames, random samples, and
a re-check around each correction). Passing records what
changed since the round started, and export requires a pass on the current content.
"""

import json
import logging
import math
import random
import statistics
import threading
import uuid
from collections import OrderedDict
from contextlib import closing

from . import annotation_completion as annotation
from .db import connect
from .review_workflow import ReviewError, digest, now, packed
from .review_evidence import valid_box

log = logging.getLogger("review.annotation_review")

SAMPLE_RATE = .02
SAMPLE_MIN = 5
SAMPLE_MAX = 20
REREVIEW_SAMPLE_RATE = .01
REREVIEW_SAMPLE_MIN = 3
REREVIEW_SAMPLE_MAX = 10
COVERAGE_SEGMENTS = 100
COVERAGE_MAX = 10
# A correction usually has the same cause in the frames around it (decision 0002):
# re-check that window instead of drawing more random frames.
FOLLOWUP_SPAN = 5
COUNT_JUMP = 3
COUNT_WINDOW = 3
COVERAGE_POINTS = (0.25, 0.5, 0.75)
RESULTS = {"ok", "fixed", "empty", "not_target"}
# Review candidate association has its own policy. Tuning endpoint identity
# matching must not silently change what is considered a missed annotation.
CANDIDATE_MATCH_POLICY = dict(iou=.5, centerRatio=.35, minCenterPx=2., containment=.85, maxSideRatio=2.)
# A shifted or looser box around an already labelled head (each box holds the
# other's centre) is a duplicate detection, not a missed head.
DUPLICATE_POLICY = dict(mutualCenter=True, maxSideRatio=3.)
REVERSE_MIN_FRAMES = 2
REVERSE_GAP_FRAMES = 2
EDGE_MARGIN_RATIO = .5
AREA_RATIO_LIMIT = 3.0
DETECTOR_STEP = 5
DETECTOR_LINK_RATIO = 5.0
STATIC_RATIO = .5
MISSING_POLICY = dict(minFrames=REVERSE_MIN_FRAMES, gap=REVERSE_GAP_FRAMES, edge=EDGE_MARGIN_RATIO,
                      area=AREA_RATIO_LIMIT, link=DETECTOR_LINK_RATIO, static=STATIC_RATIO)
COUNT_POLICY = dict(sampleRate=SAMPLE_RATE,sampleMin=SAMPLE_MIN,sampleMax=SAMPLE_MAX,
                    rereviewRate=REREVIEW_SAMPLE_RATE,rereviewMin=REREVIEW_SAMPLE_MIN,rereviewMax=REREVIEW_SAMPLE_MAX,
                    coverageSegments=COVERAGE_SEGMENTS,coverageMax=COVERAGE_MAX,
                    followupSpan=FOLLOWUP_SPAN)
# Box geometry of tracked objects: an object's box suddenly much larger/smaller
# than its own neighbours (debris merged into the mask), an object far larger
# than the clip's typical head, and tracks ending at the frame edge (residual
# boxes after the sperm left).
SIZE_WINDOW = 5
SIZE_JUMP_HIGH = 1.6
SIZE_JUMP_LOW = .6
SIZE_OVERSIZED = 1.8
SIZE_MIN_FRAMES = 3
EXIT_TAIL_FRAMES = 3
GEOMETRY_POLICY = dict(window=SIZE_WINDOW, high=SIZE_JUMP_HIGH, low=SIZE_JUMP_LOW, oversized=SIZE_OVERSIZED,
                       minFrames=SIZE_MIN_FRAMES, exitTail=EXIT_TAIL_FRAMES)
EVIDENCE_POLICY = 'actionable-review-v7-' + digest([CANDIDATE_MATCH_POLICY, DUPLICATE_POLICY, MISSING_POLICY, COUNT_POLICY, GEOMETRY_POLICY])[7:23]


def migrate(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS annotation_reviews (
      id TEXT PRIMARY KEY, media_id TEXT NOT NULL, round INTEGER NOT NULL,
      state TEXT NOT NULL, started_by INTEGER NOT NULL, started_at TEXT NOT NULL,
      start_revision TEXT NOT NULL, start_frames_json TEXT NOT NULL,
      items_json TEXT NOT NULL, escalated INTEGER NOT NULL DEFAULT 0,
      passed_by INTEGER, passed_at TEXT, passed_revision TEXT, passed_frames_json TEXT,
      passed_stamp TEXT, changes_json TEXT,
      UNIQUE(media_id, round)
    );
    CREATE TABLE IF NOT EXISTS annotation_review_checks (
      review_id TEXT NOT NULL REFERENCES annotation_reviews(id), item_id TEXT NOT NULL,
      result TEXT NOT NULL, actor_id INTEGER NOT NULL, checked_at TEXT NOT NULL,
      PRIMARY KEY(review_id, item_id)
    );
    CREATE TABLE IF NOT EXISTS annotation_review_exclusions (
      id TEXT PRIMARY KEY, media_id TEXT NOT NULL, review_id TEXT NOT NULL,
      item_id TEXT NOT NULL, source TEXT NOT NULL,
      frames_json TEXT NOT NULL,
      actor_id INTEGER NOT NULL, created_at TEXT NOT NULL,
      revoked_at TEXT, revoked_by INTEGER
    );
    CREATE INDEX IF NOT EXISTS annotation_review_exclusions_active
      ON annotation_review_exclusions(media_id, revoked_at);
    """)
    columns={r[1] for r in c.execute('PRAGMA table_info(annotation_reviews)')}
    for name in ('evidence_policy','checklist_history_json'):
        if name not in columns:c.execute(f'ALTER TABLE annotation_reviews ADD COLUMN {name} TEXT')


# ---------------------------------------------------------------- content


def content(frames, frame_count):
    """Canonical frame list: objects per known frame, None for frames never annotated."""
    return [
        None if i not in frames else sorted(frames[i].values(), key=lambda o: o["objectId"])
        for i in range(frame_count)
    ]


def revision(frames_list):
    return digest(frames_list)


def stamp(directory):
    """Cheap change indicator for list views; the content digest stays authoritative."""
    from .annotation_state import read_state
    tracker = directory / "tracker_results.json"
    st = tracker.stat() if tracker.exists() else None
    return packed([int(read_state(directory).get("revision", 0)), st and st.st_size, st and st.st_mtime_ns])


_LOAD_CACHE = OrderedDict()
_LOAD_CACHE_LOCK = threading.Lock()
_LOAD_CACHE_SIZE = 8


def clear_load_cache():
    with _LOAD_CACHE_LOCK:_LOAD_CACHE.clear()


def load_cached(media_id, directory, info):
    """Reuse the parsed result view only while workspace revision and file stamp match."""
    key = (media_id, stamp(directory), EVIDENCE_POLICY)
    with _LOAD_CACHE_LOCK:
        if key in _LOAD_CACHE:
            _LOAD_CACHE.move_to_end(key)
            return _LOAD_CACHE[key]
    value = load(directory, info)
    with _LOAD_CACHE_LOCK:
        _LOAD_CACHE[key] = value
        _LOAD_CACHE.move_to_end(key)
        while len(_LOAD_CACHE) > _LOAD_CACHE_SIZE:
            _LOAD_CACHE.popitem(last=False)
    return value


def load(directory, info):
    _, frames, rows = annotation.source(directory, None, info, require_author=False, with_rows=True)
    from .annotation_state import read_state, is_deleted
    from .endpoint_tracking import terminal_aliases
    state=read_state(directory)
    terminal=next((r for r in rows if 'terminalAnnotations' in (r.get('reviewEvidence') or {}).get('endpointCheck',{})),None)
    check=(terminal.get('reviewEvidence') or {}).get('endpointCheck',{}) if terminal else {}
    aliases=terminal_aliases(terminal) if terminal else {}
    manual={}
    for obj in state.get('manualAnnotations',[]):
        fi=obj.get('frameIndex',0);oid=obj.get('objectId')
        if obj.get('source')!='manual' or not obj.get('bbox') or is_deleted(state,fi,oid):continue
        if terminal and fi==terminal['source_frame_index']:
            original=obj.get('endpointOriginalObjectId',oid) if obj.get('endpointAnchorHash') and obj.get('endpointAnchorHash')==check.get('anchorHash') else oid
            oid=aliases.get(original,oid)
        if oid in frames.get(fi,{}):manual.setdefault(fi,set()).add(oid)
    # Review-only provenance, never written to tracking results or content hashes.
    rows=[dict(r,_reviewManualIds=sorted(manual.get(r['source_frame_index'],set())),
               _reviewObjects=list(frames.get(r['source_frame_index'],{}).values())) for r in rows]
    return content(frames, info["frameCount"]), rows


# ---------------------------------------------------------------- checklist


def sample_count(frame_count, rereview=False):
    rate,low,high=(REREVIEW_SAMPLE_RATE,REREVIEW_SAMPLE_MIN,REREVIEW_SAMPLE_MAX) if rereview else (SAMPLE_RATE,SAMPLE_MIN,SAMPLE_MAX)
    return min(high,max(low,math.ceil(frame_count*rate)))


def coverage_frames(frame_count):
    if frame_count<=0:return []
    if frame_count<=COVERAGE_SEGMENTS:
        return [round(point*(frame_count-1)) for point in COVERAGE_POINTS]
    segments=min(COVERAGE_MAX,math.ceil(frame_count/COVERAGE_SEGMENTS))
    return [min(frame_count-1,math.floor((index+.5)*frame_count/segments)) for index in range(segments)]


def spans(frames):
    out = []
    for i in sorted(frames):
        if out and out[-1][1] == i - 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return out


def count_jumps(frames_list):
    counts = [None if f is None else len(f) for f in frames_list]
    flagged = {}
    for i, n in enumerate(counts):
        if n is None:
            continue
        near = [c for j, c in enumerate(counts[max(0, i - COUNT_WINDOW): i + COUNT_WINDOW + 1], max(0, i - COUNT_WINDOW)) if j != i and c is not None]
        if len(near) >= 2:
            median = statistics.median(near)
            if abs(n - median) >= COUNT_JUMP:
                flagged[i] = (n, median)
    items = []
    for start, end in spans(flagged):
        worst = max(range(start, end + 1), key=lambda i: abs(flagged[i][0] - flagged[i][1]))
        n, median = flagged[worst]
        items.append(dict(kind="count", startFrame=start, endFrame=end, frame=worst,
                          message=f"{n} 个框，前后帧约 {median:g} 个"))
    return items


# Short names for tracking findings; the checklist shows these instead of full sentences.
FINDING_LABELS = {
    "detector_mismatch": "框偏离头部",
    "head_boundary": "框偏离头部",
    "association_uncertain": "可能重复或跟错",
    "unlabelled_candidate": "可能漏标",
    "subject_identity": "身份存疑",
    "joint_uncertain": "自动标注不确定",
    "backward_disagreement": "正反向追踪不一致",
    "endpoint_disagreement": "首尾追踪不一致",
    "endpoint_identity_uncertain": "首尾身份不明",
    "check_failed": "自动检查失败",
    "detector_unavailable_for_object": "检测器没有对应头部",
}
DOUBT_SPAN = 5
MAX_REVIEW_MARKS = 3
IDENTITY_FINDINGS = {'endpoint_disagreement','endpoint_identity_uncertain','joint_uncertain','subject_identity','backward_disagreement'}
CATEGORIES = {'unlabelled_candidate':'missing','reverse_only':'missing','reverse_disagreement':'missing',
              'association_uncertain':'association','tracking_lost':'association',
              'detector_mismatch':'position','head_boundary':'position'}
CATEGORY_LABELS = {'missing':'可能漏标','association':'可能重复或跟错','exit':'出画尾帧','position':'框偏离头部',
                   'size':'框大小异常','static':'静止目标','check':'自动检查失败'}
CATEGORY_PRIORITY = {'missing':0,'association':1,'exit':1,'position':2,'size':2,'static':3,'check':4}


def item_order(item):
    priority={'unknown':0,'changed':3,'followup':3,'count':3,'coverage':4,'sample':5}.get(item['kind'],3)
    if item['kind']=='doubt':priority=CATEGORY_PRIORITY.get(item.get('category'),4)
    source={'reverse':0,'disagreement':1,'detector':2}.get(item.get('source'),0) if item.get('category')=='missing' else 0
    return priority,source,item['startFrame'],item.get('index',0),item['endFrame'],item.get('category',''),item.get('message','')


def candidate_overlaps(a, b):
    """A detector candidate already describes this annotation, allowing padding."""
    if not valid_box(a) or not valid_box(b):
        return False
    aw, ah, bw, bh = a[2]-a[0], a[3]-a[1], b[2]-b[0], b[3]-b[1]
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    area_a, area_b = aw*ah, bw*bh
    if intersection / (area_a+area_b-intersection) >= CANDIDATE_MATCH_POLICY['iou']:
        return True
    distance = math.hypot((a[0]+a[2]-b[0]-b[2])/2, (a[1]+a[3]-b[1]-b[3])/2)
    return (distance <= max(CANDIDATE_MATCH_POLICY['minCenterPx'], CANDIDATE_MATCH_POLICY['centerRatio']*min(aw, ah, bw, bh))
            and intersection/min(area_a, area_b) >= CANDIDATE_MATCH_POLICY['containment']
            and max(aw/bw, bw/aw, ah/bh, bh/ah) <= CANDIDATE_MATCH_POLICY['maxSideRatio'])


def same_head(a, b):
    """Two boxes of comparable size around one head: each contains the other's centre."""
    if not valid_box(a) or not valid_box(b):
        return False
    aw, ah, bw, bh = a[2]-a[0], a[3]-a[1], b[2]-b[0], b[3]-b[1]
    if max(aw/bw, bw/aw, ah/bh, bh/ah) > DUPLICATE_POLICY['maxSideRatio']:
        return False
    (ax, ay), (bx, by) = _center(a), _center(b)
    return b[0] <= ax <= b[2] and b[1] <= ay <= b[3] and a[0] <= bx <= a[2] and a[1] <= by <= a[3]


def covered(box, objects):
    """Whether any current-content object already covers this pixel xyxy box."""
    return any(candidate_overlaps(box, obj.get('bbox')) or same_head(box, obj.get('bbox')) for obj in objects)


def head_stats(frames_list):
    boxes=[obj.get('bbox') for objects in frames_list for obj in (objects or []) if valid_box(obj.get('bbox'))]
    if not boxes:
        log.warning('annotation_review.head_stats_unavailable; area and motion thresholds skipped')
        return None
    widths=[box[2]-box[0] for box in boxes]
    heights=[box[3]-box[1] for box in boxes]
    return dict(area=statistics.median(w*h for w,h in zip(widths,heights)),
                width=statistics.median(widths),height=statistics.median(heights),
                diagonal=statistics.median(math.hypot(w,h) for w,h in zip(widths,heights)))


def near_edge(box, width, height, head_w):
    if not valid_box(box) or width<=0 or height<=0 or head_w is None:return False
    margin=head_w*EDGE_MARGIN_RATIO
    return box[0]<margin or box[1]<margin or width-box[2]<margin or height-box[3]<margin


def area_ok(box, median_area):
    if median_area is None:return True
    area=(box[2]-box[0])*(box[3]-box[1])
    return median_area/AREA_RATIO_LIMIT<=area<=median_area*AREA_RATIO_LIMIT


def active_exclusions(c, media_id):
    out=[];round_items={}
    for row in c.execute("SELECT * FROM annotation_review_exclusions WHERE media_id=? AND revoked_at IS NULL",(media_id,)):
        try:frames=json.loads(row['frames_json'])
        except (TypeError,json.JSONDecodeError):
            log.warning('annotation_review.exclusion_invalid media=%s exclusion=%s',media_id,row['id'])
            continue
        source=row['source']
        if source=='detector':
            rid=row['review_id']
            if rid not in round_items:
                review=c.execute('SELECT items_json,checklist_history_json FROM annotation_reviews WHERE id=?',(rid,)).fetchone()
                round_items[rid]=[] if review is None else json.loads(review['items_json'])+[i for version in json.loads(review['checklist_history_json'] or '[]') for i in version['items']]
            if any(i.get('id')==row['item_id'] and i.get('category')=='static' and any(
                o.get('frameIndex')==f.get('frameIndex') and o.get('candidates',[None])[0]==f.get('bbox')
                for o in i.get('observations',[]) if o.get('candidates') for f in frames if isinstance(f,dict)) for i in round_items[rid]):source='static'
        out.append(dict(id=row['id'],source=source,frames=[
            item for item in frames if isinstance(item,dict) and isinstance(item.get('frameIndex'),int)
            and valid_box(item.get('bbox'))]))
    return out


def excluded_candidate(frame, box, exclusions, *, any_frame=False):
    for exclusion in exclusions or []:
        for observed in exclusion['frames']:
            if (any_frame and exclusion.get('source')=='static') or abs(observed['frameIndex']-frame)<=DETECTOR_STEP:
                if candidate_overlaps(box,observed['bbox']):return True
    return False


def box_region(box, info):
    width,height=(info or {}).get('width',0),(info or {}).get('height',0)
    if width<=0 or height<=0:return ''
    regions=(('左上','上方','右上'),('左侧','中央','右侧'),('左下','下方','右下'))
    cx,cy=(box[0]+box[2])/2,(box[1]+box[3])/2
    return regions[min(2,max(0,int(3*cy/height)))][min(2,max(0,int(3*cx/width)))]


def frame_findings(rows):
    """Findings per source frame, from the evidence stored in each tracking row."""
    out = {}
    for row in rows:
        frame = row.get("source_frame_index", row.get("frame_index"))
        objects=row.get('_reviewObjects',row.get('objects',[]))
        manual=set(row.get('_reviewManualIds',[]))|{o.get('object_id',o.get('objectId')) for o in objects if o.get('source') in ('manual','manual_endpoint')}
        for f in (row.get("reviewEvidence") or {}).get("findings", []):
            if f['kind']=='endpoint_identity_uncertain' and f.get('objectId') is None and f.get('tailObjectId') is not None:
                box=f.get('bbox')
                if valid_box(box) and not covered(box,objects):
                    out.setdefault(frame,[]).append(dict(kind='reverse_only',tailObjectId=f['tailObjectId'],bbox=box))
                continue
            if f['kind']=='endpoint_disagreement':
                box=f.get('reverseBox')
                if f.get('objectId') is not None and f['objectId'] in manual:continue
                if valid_box(box) and not covered(box,objects):
                    out.setdefault(frame,[]).append(dict(kind='reverse_disagreement',tailObjectId=f.get('tailObjectId'),
                                                        objectId=f.get('objectId'),bbox=box))
                continue
            if f['kind'] in IDENTITY_FINDINGS or f['kind']=='detector_unavailable_for_object':continue
            if f.get('objectId') is not None and f['objectId'] in manual:continue
            if f['kind']=='unlabelled_candidate' and not valid_box(f.get('bbox')):
                log.warning('annotation_review.candidate_invalid frame=%s',frame)
                out.setdefault(frame,[]).append(dict(kind='check_failed',objectId=None,message='候选位置数据无效'))
                continue
            if f['kind']=='unlabelled_candidate' and valid_box(f.get('bbox')):
                associated=[o for o in objects if candidate_overlaps(f['bbox'],o.get('bbox'))]
                if len(associated)==1:continue
                if not associated and covered(f['bbox'],objects):continue
                if len(associated)>1:
                    ids=sorted({o.get('object_id',o.get('objectId')) for o in associated})
                    for obj in associated:
                        oid=obj.get('object_id',obj.get('objectId'))
                        if oid in manual:continue
                        out.setdefault(frame,[]).append(dict(f,kind='association_uncertain',objectId=oid,
                            bbox=obj['bbox'],candidateBox=f['bbox'],relatedObjects=ids))
                    continue
            out.setdefault(frame, []).append(f)
    return out


def _frames_from_rows(rows, info=None):
    count=(info or {}).get('frameCount',0)
    count=max(count,max((r.get('source_frame_index',r.get('frame_index',-1)) for r in rows),default=-1)+1)
    frames=[None]*count
    for row in rows:
        fi=row.get('source_frame_index',row.get('frame_index'))
        if fi is not None and 0<=fi<count:
            frames[fi]=list(row.get('_reviewObjects',row.get('objects',[])))
    return frames


def _location_item(category, source, path, info, **extra):
    anchor=path[0][1]
    label=CATEGORY_LABELS[category]
    item=dict(kind='doubt',category=category,source=source,startFrame=path[0][0],endFrame=path[-1][0],frame=path[0][0],
              objectIds=[],objectId=None,anchor=anchor,region=box_region(anchor,info),labels=[label],message=label,
              candidates=[anchor],bbox=None,
              observations=[dict(frameIndex=fi,objectIds=[],candidates=[box]) for fi,box in path],
              detectorAgrees=category=='static' or source=='detector')
    item.update(extra)
    return item


def reverse_items(findings_by_frame, info, stats, frames_list=None, rows=None, exclusions=None):
    """Continuous reverse-only SAM3 observations, without rerunning a model."""
    groups={}
    for fi,findings in findings_by_frame.items():
        for finding in findings:
            if finding['kind'] not in ('reverse_only','reverse_disagreement'):continue
            tail=finding.get('tailObjectId');box=finding.get('bbox')
            if tail is None or not valid_box(box):continue
            groups.setdefault((finding['kind'],tail),{})[fi]=box
    width,height=(info or {}).get('width',0),(info or {}).get('height',0)
    median_area=stats['area'] if stats else None
    head_w=stats['width'] if stats else None
    items=[]
    used={obj.get('objectId',obj.get('object_id')) for objects in frames_list or [] for obj in objects or []}
    reserved={}
    next_id=max((oid for oid in used if isinstance(oid,int)),default=0)+1
    for (kind,tail),observed in sorted(groups.items(),key=lambda pair:(pair[0][0],str(pair[0][1]))):
        kept={fi:box for fi,box in observed.items() if not near_edge(box,width,height,head_w)
              and area_ok(box,median_area) and not excluded_candidate(fi,box,exclusions)}
        segments=[]
        for fi in sorted(kept):
            if segments and fi-segments[-1][-1]<=REVERSE_GAP_FRAMES+1:segments[-1].append(fi)
            else:segments.append([fi])
        for segment in segments:
            if len(segment)<REVERSE_MIN_FRAMES:continue
            path=[(fi,kept[fi]) for fi in segment]
            canonical=tail
            if rows:
                from .endpoint_tracking import terminal_aliases
                terminal=max(rows,key=lambda r:r.get('source_frame_index',r.get('frame_index',-1)))
                canonical=terminal_aliases(terminal).get(tail,tail)
            # A canonical ID already present anywhere may belong to another head.
            if tail not in reserved:
                if canonical in used or canonical in reserved.values():
                    while next_id in used or next_id in reserved.values():next_id+=1
                    reserved[tail]=next_id;next_id+=1
                else:reserved[tail]=canonical
            adopt=reserved[tail]
            source='reverse' if kind=='reverse_only' else 'disagreement'
            items.append(_location_item('missing',source,path,info,tailObjectId=tail,adoptObjectId=adopt))
    return items


def _center(box):
    return ((box[0]+box[2])/2,(box[1]+box[3])/2)


def _global_translation(first,last,frames_list):
    # Swimming targets are not a camera-motion estimator. Until an independent
    # image-registration estimate is available, measure in original pixels.
    return 0.,0.


def detector_items(findings_by_frame, info, stats, claimed, frames_list=None, exclusions=None):
    """Link the detector's sparse observations into reviewable paths."""
    candidates=[]
    for fi,findings in sorted(findings_by_frame.items()):
        for finding in findings:
            if finding['kind']!='unlabelled_candidate' or not valid_box(finding.get('bbox')):continue
            box=finding['bbox']
            if excluded_candidate(fi,box,exclusions):continue
            agreeing=next((entry for entry in claimed if entry['frame']==fi and candidate_overlaps(box,entry['box'])),None)
            if agreeing is not None:
                agreeing['item']['detectorAgrees']=True
            else:
                candidates.append((fi,box))
    diagonal=stats['diagonal'] if stats else None
    paths=[]
    for fi in sorted({frame for frame,_ in candidates}):
        boxes=sorted([box for frame,box in candidates if frame==fi])
        edges=[]
        for pi,path in enumerate(paths):
            last_fi,last_box=path[-1]
            if abs((fi-last_fi)-DETECTOR_STEP)>1:continue
            for bi,box in enumerate(boxes):
                a,b=_center(last_box),_center(box)
                distance=math.hypot(b[0]-a[0],b[1]-a[1])
                if diagonal is not None and distance<diagonal*DETECTOR_LINK_RATIO:
                    edges.append((distance,pi,bi))
        assigned_paths=set();assigned_boxes=set()
        for _,pi,bi in sorted(edges):
            if pi in assigned_paths or bi in assigned_boxes:continue
            paths[pi].append((fi,boxes[bi]));assigned_paths.add(pi);assigned_boxes.add(bi)
        paths.extend([[(fi,box)] for bi,box in enumerate(boxes) if bi not in assigned_boxes])
    items=[]
    for path in paths:
        if len(path)<2:
            log.debug('annotation_review.detector_single_dropped frame=%s',path[0][0])
            continue
        first,last=path[0][0],path[-1][0]
        dx,dy=_global_translation(first,last,frames_list or [])
        a,b=_center(path[0][1]),_center(path[-1][1])
        displacement=math.hypot(b[0]-a[0]-dx,b[1]-a[1]-dy)
        category='static' if diagonal is not None and displacement<diagonal*STATIC_RATIO else 'missing'
        if category=='static' and any(excluded_candidate(box[0],box[1],exclusions,any_frame=True) for box in path):
            continue
        items.append(_location_item(category,'detector',path,info))
    return items


def track_ended_items(rows, frames_list, info, stats, only=None):
    """A non-manual identity vanished inside the frame without an exit or tail match."""
    last={};objects_at={};manual_at={};exited=set();matched=set()
    for row in rows:
        fi=row.get('source_frame_index',row.get('frame_index'))
        evidence=row.get('reviewEvidence') or {}
        exited.update((evidence.get('boundaryExit') or {}).get('exitedObjectIds') or [])
        matched.update(v for v in ((evidence.get('endpointCheck') or {}).get('matches') or {}).values() if isinstance(v,int))
        objects=row.get('_reviewObjects',row.get('objects',[]))
        objects_at[fi]=objects
        manual_at[fi]=set(row.get('_reviewManualIds',[]))|{o.get('objectId',o.get('object_id')) for o in objects if o.get('source') in ('manual','manual_endpoint')}
        for obj in objects:
            oid=obj.get('objectId',obj.get('object_id'))
            if isinstance(oid,int) and valid_box(obj.get('bbox')):last[oid]=fi
    if not frames_list:return []
    final=len(frames_list)-1;width,height=(info or {}).get('width',0),(info or {}).get('height',0)
    head_w=stats['width'] if stats else None;items=[]
    for oid,fi in sorted(last.items()):
        if only is not None and not only[0]<=fi+1<=only[1]:continue
        if fi>=final or oid in exited or oid in matched or oid in manual_at.get(fi,set()):continue
        box=next(obj['bbox'] for obj in objects_at[fi] if obj.get('objectId',obj.get('object_id'))==oid)
        if near_edge(box,width,height,head_w):continue
        items.append(dict(kind='doubt',category='association',source='track_ended',objectIds=[oid],objectId=oid,
                          startFrame=fi+1,endFrame=min(fi+DOUBT_SPAN,final),frame=fi+1,lastFrame=fi,lastBox=box,
                          anchor=box,region=box_region(box,info),labels=['轨迹中途结束'],message='轨迹中途结束',
                          candidates=[],bbox=None,observations=[dict(frameIndex=fi+1,objectIds=[],candidates=[])]))
    return items


def _manual_ids(rows):
    return {r.get('source_frame_index',r.get('frame_index')):
            set(r.get('_reviewManualIds',[]))|{o.get('objectId',o.get('object_id')) for o in r.get('_reviewObjects',r.get('objects',[]))
                                               if o.get('source') in ('manual','manual_endpoint')} for r in rows}


def _area(box):
    return (box[2]-box[0])*(box[3]-box[1])


def object_series(frames_list):
    series={}
    for fi,objects in enumerate(frames_list):
        for obj in objects or []:
            oid=obj.get('objectId',obj.get('object_id'));box=obj.get('bbox')
            if isinstance(oid,int) and valid_box(box):series.setdefault(oid,[]).append((fi,box))
    return series


def _overlaps(start, end, only):
    return only is None or (start<=only[1] and only[0]<=end)


def _object_item(category, source, oid, frames, frame, message, **extra):
    item=dict(kind='doubt',category=category,source=source,objectIds=[oid],objectId=oid,startFrame=frames[0],endFrame=frames[-1],
              frame=frame,labels=[CATEGORY_LABELS[category]],message=message,candidates=[],bbox=None,
              observations=[dict(frameIndex=fi,objectIds=[oid],candidates=[]) for fi in frames])
    item.update(extra)
    return item


def size_items(frames_list, rows, info, stats, only=None):
    """Boxes much larger or smaller than the same object's neighbours, and objects far above the clip's head size."""
    manual=_manual_ids(rows);width,height=(info or {}).get('width',0),(info or {}).get('height',0)
    head_w=stats['width'] if stats else None;items=[]
    for oid,path in sorted(object_series(frames_list).items()):
        flagged={}
        for k,(fi,box) in enumerate(path):
            if oid in manual.get(fi,()):continue
            near=[_area(b) for j,(fj,b) in enumerate(path) if j!=k and abs(fj-fi)<=SIZE_WINDOW]
            if len(near)<2:continue
            ratio=_area(box)/statistics.median(near)
            # A box clipped by the frame edge shrinks naturally; exits are listed separately.
            if ratio<=SIZE_JUMP_LOW and near_edge(box,width,height,head_w):continue
            if ratio>=SIZE_JUMP_HIGH or ratio<=SIZE_JUMP_LOW:flagged[fi]=ratio
        for start,end in spans(flagged):
            if not _overlaps(start,end,only):continue
            frames=list(range(start,end+1));worst=max(frames,key=lambda fi:abs(math.log(flagged[fi])))
            items.append(_object_item('size','size_jump',oid,frames,worst,f'框面积约为前后帧的 {flagged[worst]:.1f} 倍',ratio=round(flagged[worst],2)))
        if stats and len(path)>=SIZE_MIN_FRAMES:
            typical=statistics.median(_area(b) for _,b in path)
            ratio=typical/stats['area'] if stats['area']>0 else 0
            usable=[(fi,b) for fi,b in path if oid not in manual.get(fi,()) and _overlaps(fi,fi,only)]
            if ratio>=SIZE_OVERSIZED and usable:
                fi=min(usable,key=lambda x:(abs(_area(x[1])-typical),x[0]))[0]
                items.append(_object_item('size','oversized',oid,[fi],fi,f'整条轨迹的框约为本视频头框的 {ratio:.1f} 倍',ratio=round(ratio,2)))
    return items


def exit_items(rows, frames_list, info, stats, only=None):
    """Tracks ending at the frame edge: the last boxes may remain after the sperm left."""
    if not frames_list:return []
    exited=set()
    for row in rows:exited.update(((row.get('reviewEvidence') or {}).get('boundaryExit') or {}).get('exitedObjectIds') or [])
    manual=_manual_ids(rows);final=len(frames_list)-1
    width,height=(info or {}).get('width',0),(info or {}).get('height',0);head_w=stats['width'] if stats else None
    items=[]
    for oid,path in sorted(object_series(frames_list).items()):
        last,box=path[-1]
        if last>=final or oid in exited or oid in manual.get(last,()) or not near_edge(box,width,height,head_w):continue
        frames=[fi for fi,_ in path[-EXIT_TAIL_FRAMES:]]
        if not _overlaps(frames[0],frames[-1],only):continue
        items.append(_object_item('exit','edge_exit',oid,frames,last,'轨迹在画面边缘结束',lastFrame=last,lastBox=box,
                                  anchor=box,region=box_region(box,info)))
    return items


def doubt_items(rows, only=None, *, info=None, frames_list=None, exclusions=None):
    """Actionable reasons only, with bounded marks and exact per-frame locations."""
    frames_list=frames_list if frames_list is not None else _frames_from_rows(rows,info)
    findings_by_frame={}
    grouped={}
    for fi,findings in sorted(frame_findings(rows).items()):
        if fi is None or (only is not None and not only[0]<=fi<=only[1]):continue
        findings_by_frame[fi]=findings
        for f in findings:grouped.setdefault(CATEGORIES.get(f['kind'],'check'),[]).append((fi,f))
    stats=head_stats(frames_list)
    items=reverse_items(findings_by_frame,info,stats,frames_list,rows,exclusions)
    claimed=[dict(frame=o['frameIndex'],box=o['candidates'][0],item=item)
             for item in items for o in item['observations']]
    items+=detector_items(findings_by_frame,info,stats,claimed,frames_list,exclusions)
    items+=track_ended_items(rows,frames_list,info,stats,only)
    items+=size_items(frames_list,rows,info,stats,only)
    items+=exit_items(rows,frames_list,info,stats,only)
    for category,findings in grouped.items():
        if category in ('missing','static'):continue
        batches=[]
        for fi,f in findings:
            oid=f.get('objectId');eligible=[]
            for b in reversed(batches):
                if fi-b[0][0]>=DOUBT_SPAN or fi>b[-1][0]+1:continue
                ids={x[1]['objectId'] for x in b if x[1].get('objectId') is not None}
                if oid is not None and len(ids|{oid})>MAX_REVIEW_MARKS:continue
                eligible.append((oid in ids,b))
            batch=next((b for same,b in eligible if same),eligible[0][1] if eligible else None)
            if batch is None:
                batches.append([]);batch=batches[-1]
            batch.append((fi,f))
        for batch in batches:
            observations=[]
            for fi in sorted({fi for fi,_ in batch}):
                fs=[f for frame,f in batch if frame==fi]
                ids=sorted({f['objectId'] for f in fs if f.get('objectId') is not None})
                observations.append(dict(frameIndex=fi,objectIds=ids,candidates=[]))
            ids=sorted({oid for o in observations for oid in o['objectIds']})
            label=CATEGORY_LABELS[category]
            items.append(dict(kind='doubt',category=category,startFrame=observations[0]['frameIndex'],endFrame=observations[-1]['frameIndex'],frame=observations[0]['frameIndex'],
                              objectIds=ids,objectId=ids[0] if len(ids)==1 else None,labels=[label],message=label,
                              candidates=[],bbox=None,observations=observations))
    for fi in sorted({i['frame'] for i in items if i.get('category') in ('missing','static')}):
        same=sorted([i for i in items if i.get('category') in ('missing','static') and i['frame']==fi],
                    key=lambda i:(i['anchor'][1]+i['anchor'][3],i['anchor'][0]+i['anchor'][2],i['anchor']))
        for index,item in enumerate(same,1):item.update(index=index,countInFrame=len(same))
    return sorted(items,key=item_order)


def changed_frames(old, new):
    return [i for i, (a, b) in enumerate(zip(old, new)) if a != b]


def build_items(review_id, frames_list, rows, previous=None, *, info=None, exclusions=None):
    n = len(frames_list)
    items = []
    unknown = [i for i, f in enumerate(frames_list) if f is None]
    for start, end in spans(unknown):
        items.append(dict(kind="unknown", startFrame=start, endFrame=end, frame=start,
                          message="没有任何标注"))
    if previous is None:
        items += doubt_items(rows,info=info,frames_list=frames_list,exclusions=exclusions)
        items += count_jumps(frames_list)
        for i in coverage_frames(n):
            if not any(x["kind"] == "coverage" and x["frame"] == i for x in items):
                items.append(dict(kind="coverage", startFrame=i, endFrame=i, frame=i,
                                  message="整帧查漏标"))
        sample_total = sample_count(n)
    else:
        # Re-review after edits: what changed, doubts touching it, and fresh samples.
        changed = changed_frames(previous, frames_list)
        windows=[]
        for start, end in spans(changed):
            items.append(dict(kind="changed", startFrame=start, endFrame=end, frame=start,
                              message="通过后有改动"))
            left,right=max(0,start-2),min(n-1,end+2)
            if windows and left<=windows[-1][1]+1:windows[-1][1]=max(windows[-1][1],right)
            else:windows.append([left,right])
        for window in windows:
            items += doubt_items(rows,only=window,info=info,frames_list=frames_list,exclusions=exclusions)
        items += [x for x in count_jumps(frames_list) if any(s <= x["frame"] + 2 and x["frame"] - 2 <= e for s, e in spans(changed))]
        sample_total = sample_count(n, rereview=True)
    items += samples(review_id, frames_list, items, sample_total, "sample")
    items.sort(key=item_order)
    for k, item in enumerate(items, 1):
        item["id"] = f"{item['kind']}-{k}"
    return items


def samples(seed, frames_list, items, count, prefix):
    covered = set()
    for item in items:
        covered.update(range(item["startFrame"], item["endFrame"] + 1))
    pool = [i for i, f in enumerate(frames_list) if f is not None and i not in covered]
    picked = sorted(random.Random(seed + prefix).sample(pool, min(count, len(pool))))
    return [dict(kind="sample", startFrame=i, endFrame=i, frame=i,
                 message="随机抽检") for i in picked]


# ---------------------------------------------------------------- state


def latest(c, media_id):
    return c.execute("SELECT * FROM annotation_reviews WHERE media_id=? ORDER BY round DESC LIMIT 1", (media_id,)).fetchone()


def last_passed(c, media_id):
    return c.execute("SELECT * FROM annotation_reviews WHERE media_id=? AND state='passed' ORDER BY round DESC LIMIT 1", (media_id,)).fetchone()


def checks(c, review_id):
    return {r["item_id"]: dict(result=r["result"], actorId=r["actor_id"], checkedAt=r["checked_at"])
            for r in c.execute("SELECT * FROM annotation_review_checks WHERE review_id=?", (review_id,))}


def review_decisions(c, media_id, source_sha256):
    """Latest passed human judgement on candidate heads, for detector hard negatives."""
    by_frame={}
    for row in c.execute("SELECT * FROM annotation_reviews WHERE media_id=? AND state='passed' ORDER BY round",(media_id,)):
        versions=json.loads(row['checklist_history_json'] or '[]')
        versions.append(dict(items=json.loads(row['items_json']),checks=checks(c,row['id'])))
        for version in versions:
            for item in version['items']:
                if item.get('kind')!='doubt' or item.get('category') not in ('missing','static'):continue
                check=version['checks'].get(item['id'])
                if not check:continue
                for observation in item.get('observations') or []:
                    candidates=observation.get('candidates') or []
                    if not candidates or not valid_box(candidates[0]):continue
                    frame=observation['frameIndex'];box=list(candidates[0])
                    # IDs repeat between rounds. Resolve by actual frame/location,
                    # including a newer 'ok' which supersedes an older judgement.
                    out=[d for d in by_frame.get(frame,[]) if not candidate_overlaps(box,d['bbox'])]
                    by_frame[frame]=out
                    if check['result'] in ('fixed','not_target'):
                        out.append(dict(sourceSha256=source_sha256,frameIndex=frame,bbox=box,
                            category=item['category'],source=item.get('source'),result=check['result'],
                            reviewId=row['id'],itemId=item['id']))
    return [d for frame in sorted(by_frame) for d in by_frame[frame]]


def review_edited(c, media_id):
    """Objects a reviewer added or changed in any passed round: {frameIndex: {objectId}}.

    Training exports mark these boxes so model work can weight or study what the
    tracker got wrong (decision 0002). Annotator edits before review are not included.
    """
    out={}
    for row in c.execute("SELECT changes_json FROM annotation_reviews WHERE media_id=? AND state='passed' AND changes_json IS NOT NULL",(media_id,)):
        for frame in json.loads(row['changes_json']).get('frames',[]):
            out.setdefault(frame['frameIndex'],set()).update(frame.get('added',[])+frame.get('moved',[]))
    return out


def refresh_doubts(c, row, frames_list, rows, info=None):
    """Upgrade an open checklist once, without reusing a check for another risk.

    Keep coverage/sampling/unknown items and their IDs. Archive the entire old
    list and decisions before replacing doubts; never mark a new risk checked.
    Completed review history is immutable and never silently reopened here.
    """
    if row is None or row['state']!='open' or row['evidence_policy']==EVIDENCE_POLICY:return row
    original=json.loads(row['items_json']);done=checks(c,row['id'])
    passed=last_passed(c,row['media_id'])
    previous=json.loads(passed['passed_frames_json']) if passed else None
    fresh=[i for i in build_items(row['id'],frames_list,rows,previous,info=info,exclusions=active_exclusions(c,row['media_id'])) if i['kind']=='doubt']
    def identity(item):
        return digest({k:v for k,v in item.items() if k not in ('id','check')})
    by_identity={identity(i):i for i in original if i['kind']=='doubt'}
    for item in fresh:
        old=by_identity.get(identity(item))
        item['id']=old['id'] if old else 'doubt-tolerance-'+identity(item)[:24]
    history=json.loads(row['checklist_history_json'] or '[]')
    history.append(dict(at=now(),policy=row['evidence_policy'],items=original,checks=done))
    items=sorted([i for i in original if i['kind']!='doubt']+fresh,key=item_order)
    c.execute('UPDATE annotation_reviews SET items_json=?,evidence_policy=?,checklist_history_json=? WHERE id=?',
              (packed(items),EVIDENCE_POLICY,packed(history),row['id']))
    log.info('annotation_review.evidence_refreshed media=%s review=%s old_doubts=%s new_doubts=%s policy=%s',
             row['media_id'],row['id'],sum(i['kind']=='doubt' for i in original),len(fresh),EVIDENCE_POLICY)
    return latest(c,row['media_id'])


def round_data(c, row):
    if row is None:
        return None
    done = checks(c, row["id"])
    items = [dict(item, check=done.get(item["id"])) for item in json.loads(row["items_json"])]
    latest_check = c.execute("SELECT actor_id,checked_at FROM annotation_review_checks WHERE review_id=? ORDER BY checked_at DESC,rowid DESC LIMIT 1",
                             (row["id"],)).fetchone()
    return dict(reviewId=row["id"], round=row["round"], state=row["state"], startedBy=row["started_by"],
                startedAt=row["started_at"], escalated=bool(row["escalated"]), items=items,
                lastActorId=latest_check["actor_id"] if latest_check else row["started_by"],
                lastActivityAt=latest_check["checked_at"] if latest_check else row["started_at"],
                checked=sum(1 for i in items if i["check"]), total=len(items),
                passedBy=row["passed_by"], passedAt=row["passed_at"],
                changes=json.loads(row["changes_json"]) if row["changes_json"] else None)


def status(media_id, directory, info):
    try:
        frames_list, rows = load_cached(media_id, directory, info)
    except ReviewError as e:
        if e.code != "SOURCE_UNAVAILABLE":
            raise
        # Nothing annotated yet: not an error for the review page, just not ready.
        return dict(mediaId=media_id, status="none", currentRevision=None, unknownFrames=info["frameCount"],
                    review=None, lastPassed=None, notReady=e.message)
    current = revision(frames_list)
    with closing(connect()) as c, c:
        row = refresh_doubts(c,latest(c, media_id),frames_list,rows,info)
        passed = last_passed(c, media_id)
        if row is None:
            state = "none"
        elif row["state"] == "open":
            state = "open"
        elif passed and passed["passed_revision"] == current:
            state = "passed"
            fresh = stamp(directory)
            if passed["passed_stamp"] != fresh:  # A save without content change keeps the pass.
                c.execute("UPDATE annotation_reviews SET passed_stamp=? WHERE id=?", (fresh, passed["id"]))
        else:
            state = "stale"
        return dict(mediaId=media_id, status=state, currentRevision=current,
                    unknownFrames=sum(f is None for f in frames_list),
                    review=round_data(c, row),
                    lastPassed=None if passed is None else dict(reviewId=passed["id"], round=passed["round"],
                                                                passedBy=passed["passed_by"], passedAt=passed["passed_at"]))


def summary(media_dirs):
    """Status for the media list without reading tracking files; 'changed' means re-check on open."""
    out = {}
    with closing(connect()) as c:
        for media_id, directory in media_dirs:
            row = latest(c, media_id)
            if row is None:
                out[media_id] = "none"
            elif row["state"] == "open":
                out[media_id] = "open"
            else:
                passed = last_passed(c, media_id)
                try:
                    same = passed is not None and passed["passed_stamp"] == stamp(directory)
                except OSError:
                    same = False
                out[media_id] = "passed" if same else "stale"
    return out


# ---------------------------------------------------------------- actions


def start(media_id, directory, info, actor):
    frames_list, rows = load_cached(media_id, directory, info)
    with closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        row = latest(c, media_id)
        if row is not None and row["state"] == "open":
            return round_data(c, refresh_doubts(c,row,frames_list,rows,info))
        passed = last_passed(c, media_id)
        previous = json.loads(passed["passed_frames_json"]) if passed else None
        if passed and passed["passed_revision"] == revision(frames_list):
            raise ReviewError("REVIEW_ALREADY_PASSED", "当前标注已经审查通过，没有需要重新审查的改动", 409)
        rid = "arev_" + uuid.uuid4().hex
        items = build_items(rid, frames_list, rows, previous,info=info,exclusions=active_exclusions(c,media_id))
        c.execute(
            "INSERT INTO annotation_reviews(id,media_id,round,state,started_by,started_at,start_revision,start_frames_json,items_json,evidence_policy) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, media_id, (row["round"] + 1) if row else 1, "open", actor, now(), revision(frames_list), packed(frames_list), packed(items),EVIDENCE_POLICY),
        )
        log.info("annotation_review.started media=%s review=%s actor=%s items=%s rereview=%s", media_id, rid, actor, len(items), previous is not None)
        return round_data(c, latest(c, media_id))


def open_round(c, media_id, review_id):
    row = latest(c, media_id)
    if row is None or row["id"] != review_id:
        raise ReviewError("REVIEW_NOT_CURRENT", "这一轮审查已不是当前轮次，请刷新", 409)
    if row["state"] != "open":
        raise ReviewError("REVIEW_CLOSED", "这一轮审查已结束", 409)
    return row


def check(media_id, review_id, item_id, result, actor, current_frames=None):
    """Record one item. ``current_frames`` lets an "already boxed" answer on a
    location item become ``fixed`` when the reviewer edited those frames."""
    if result not in RESULTS:
        raise ReviewError("INVALID_RESULT", "检查结果无效", 422)
    with closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        row = open_round(c, media_id, review_id)
        items = json.loads(row["items_json"])
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            raise ReviewError("ITEM_NOT_FOUND", "检查项不存在", 404)
        if result == "empty" and item["kind"] != "unknown":
            raise ReviewError("INVALID_RESULT", "只有未标注的帧可以确认为无目标", 422)
        if result == "not_target" and item.get("category") not in ("missing", "static"):
            raise ReviewError("INVALID_RESULT", "只有漏标或静止目标项可以标记为不是精子", 422)
        if result == "ok" and item.get("category") in ("missing", "static") and current_frames is not None:
            start_frames = json.loads(row["start_frames_json"])
            touched = [o["frameIndex"] for o in item.get("observations", [])
                       if 0 <= o["frameIndex"] < min(len(start_frames), len(current_frames))
                       and start_frames[o["frameIndex"]] != current_frames[o["frameIndex"]]]
            if touched:
                result = "fixed"
                log.info("annotation_review.boxed_by_reviewer media=%s review=%s item=%s frames=%s", media_id, review_id, item_id, touched)
        previous = c.execute("SELECT result FROM annotation_review_checks WHERE review_id=? AND item_id=?",
                             (review_id, item_id)).fetchone()
        checked_at = now()
        c.execute(
            "INSERT INTO annotation_review_checks VALUES (?,?,?,?,?) ON CONFLICT(review_id,item_id) DO UPDATE SET result=excluded.result,actor_id=excluded.actor_id,checked_at=excluded.checked_at",
            (review_id, item_id, result, actor, checked_at),
        )
        if result == "not_target":
            already = previous is not None and previous["result"] == "not_target"
            if not already:
                c.execute("UPDATE annotation_review_exclusions SET revoked_at=?,revoked_by=? WHERE review_id=? AND item_id=? AND revoked_at IS NULL",
                          (checked_at, actor, review_id, item_id))
                frames = [dict(frameIndex=o["frameIndex"], bbox=list(o["candidates"][0]))
                          for o in item.get("observations", []) if o.get("candidates") and valid_box(o["candidates"][0])]
                c.execute("INSERT INTO annotation_review_exclusions(id,media_id,review_id,item_id,source,frames_json,actor_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
                          ("arex_"+uuid.uuid4().hex, media_id, review_id, item_id,
                           ("static" if item.get("category")=="static" else item.get("source") or item.get("category") or ""), packed(frames), actor, checked_at))
                log.info("annotation_review.exclusion_recorded media=%s review=%s item=%s frames=%s", media_id, review_id, item_id, len(frames))
        else:
            c.execute("UPDATE annotation_review_exclusions SET revoked_at=?,revoked_by=? WHERE review_id=? AND item_id=? AND revoked_at IS NULL",
                      (checked_at, actor, review_id, item_id))
        extra = followup_item(item, items, json.loads(row["start_frames_json"]), current_frames) if result == "fixed" else None
        if extra:
            items.append(extra)
            c.execute("UPDATE annotation_reviews SET items_json=? WHERE id=?", (packed(items), review_id))
            log.info("annotation_review.followup_added media=%s review=%s item=%s frames=%s-%s objects=%s",
                     media_id, review_id, item_id, extra["startFrame"], extra["endFrame"], extra["objectIds"])
        return round_data(c, latest(c, media_id))


def followup_item(item, items, start_frames, current_frames):
    """One re-check of the frames around a correction, once per corrected item."""
    if item["kind"] in ("unknown", "followup") or item.get("category") in ("missing", "static"):return None
    if any(i.get("followupOf") == item["id"] for i in items):return None
    n = len(start_frames)
    if n == 0:return None
    start, end = max(0, item["startFrame"] - FOLLOWUP_SPAN), min(n - 1, item["endFrame"] + FOLLOWUP_SPAN)
    ids = set()
    if current_frames is not None:
        for fi in range(item["startFrame"], min(item["endFrame"], n - 1, len(current_frames) - 1) + 1):
            before = {o["objectId"]: o for o in start_frames[fi] or []}
            after = {o["objectId"]: o for o in current_frames[fi] or []}
            ids.update(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    ids = sorted(ids)
    return dict(id=f"followup-{item['id']}", kind="followup", followupOf=item["id"], startFrame=start, endFrame=end,
                frame=start, objectIds=ids, objectId=ids[0] if len(ids) == 1 else None, message="修正处前后帧复查")


def diff(old, new):
    frames, totals = [], dict(added=0, removed=0, moved=0)
    for i, (a, b) in enumerate(zip(old, new)):
        if a == b:
            continue
        before = {o["objectId"]: o for o in a or []}
        after = {o["objectId"]: o for o in b or []}
        change = dict(frameIndex=i,
                      added=sorted(set(after) - set(before)),
                      removed=sorted(set(before) - set(after)),
                      moved=sorted(k for k in set(before) & set(after) if before[k] != after[k]))
        for key in totals:
            totals[key] += len(change[key])
        frames.append(change)
    return dict(totals, frames=frames)


def confirmed_empty(c, review_id):
    row = c.execute("SELECT items_json FROM annotation_reviews WHERE id=?", (review_id,)).fetchone()
    done = checks(c, review_id)
    out = set()
    for item in json.loads(row["items_json"]):
        if item["kind"] == "unknown" and done.get(item["id"], {}).get("result") == "empty":
            out.update(range(item["startFrame"], item["endFrame"] + 1))
    return out


def pass_review(media_id, directory, info, review_id, actor):
    frames_list, rows = load(directory, info)
    with closing(connect()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        row = refresh_doubts(c,open_round(c, media_id, review_id),frames_list,rows,info)
        items = json.loads(row["items_json"])
        done = checks(c, review_id)
        missing = [i for i in items if i["id"] not in done]
        if missing:
            raise ReviewError("REVIEW_INCOMPLETE", f"还有 {len(missing)} 项没有检查", 409)
        empty = confirmed_empty(c, review_id)
        unknown = [i for i, f in enumerate(frames_list) if f is None and i not in empty]
        if unknown:
            raise ReviewError("UNKNOWN_FRAMES", f"还有 {len(unknown)} 帧没有标注，也没有确认为无目标（第 {unknown[0] + 1} 帧起）", 409)
        changes = diff(json.loads(row["start_frames_json"]), frames_list)
        checkers = {}
        for item in items:
            for fi in range(item["startFrame"], item["endFrame"] + 1):
                checkers.setdefault(fi, done[item["id"]]["actorId"])
        for change in changes["frames"]:
            change["checkedBy"] = checkers.get(change["frameIndex"])
        changes["reviewers"] = sorted({d["actorId"] for d in done.values()})
        c.execute(
            "UPDATE annotation_reviews SET state='passed',passed_by=?,passed_at=?,passed_revision=?,passed_frames_json=?,passed_stamp=?,changes_json=? WHERE id=?",
            (actor, now(), revision(frames_list), packed(frames_list), stamp(directory), packed(changes), review_id),
        )
        log.info("annotation_review.passed media=%s review=%s actor=%s added=%s removed=%s moved=%s", media_id, review_id, actor, changes["added"], changes["removed"], changes["moved"])
        return round_data(c, latest(c, media_id))


def require_passed(c, media_id, frames, frame_count):
    """Export gate: the frames to be exported must equal the last passed review."""
    passed = last_passed(c, media_id)
    row = latest(c, media_id)
    if passed is None:
        raise ReviewError("REVIEW_REQUIRED", "该视频还没有审查通过，不能导出训练数据集", 409)
    if row["state"] == "open":
        raise ReviewError("REVIEW_REQUIRED", "该视频正在重新审查，审查通过后才能导出", 409)
    if passed["passed_revision"] != revision(content(frames, frame_count)):
        raise ReviewError("REVIEW_REQUIRED", "审查通过后标注又有改动，请重新审查后再导出", 409)
    return confirmed_empty(c, passed["id"])
