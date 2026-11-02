"""Temporal head-box synthesis. Public rows contain a single editable geometry.

Independent directions and local detector observations are candidates, not votes
or approvals. Detector absence is unknown. No internal candidate creates an ID.
"""
import copy
import logging
import math
from .endpoint_tracking import iou
from .review_evidence import valid_box

log = logging.getLogger('review.tracking')
POLICY = 'joint-head-v2'
# Decision 0002: when the detector is not adopted for a frame (low confidence,
# a competing candidate such as debris next to the head), SAM3 boxes may have
# swallowed the debris. Offer the object's usual detector size around the
# tracker centre; the temporal path still chooses.
SIZE_SUPPORT_MIN = 3
SIZE_LOOSE_RATIO = 1.3


def center(b):return ((b[0]+b[2])/2,(b[1]+b[3])/2)
def scale(b):return max(4.,math.hypot(b[2]-b[0],b[3]-b[1]))
def distance(a,b):return math.dist(center(a),center(b))
def near_same(a,b):return iou(a,b)>.85 and distance(a,b)<.2*min(scale(a),scale(b))


def detector_assignments(anchors, candidates):
    """Unique, local association; crowded/competing detections stay uncertain."""
    by_object={oid:[] for oid in anchors}
    for d in candidates:
        if d[4]<.7:continue
        ranked=[]
        for oid,boxes in anchors.items():
            if not boxes:continue
            norm=min(distance(d,b)/scale(b) for b in boxes)
            overlap=max(iou(d,b) for b in boxes)
            if norm>.5 and overlap<.2:continue
            ranked.append((norm+.2*(1-overlap),oid))
        ranked.sort()
        if not ranked or (len(ranked)>1 and ranked[1][0]-ranked[0][0]<.25):continue
        value,oid=ranked[0]
        # A well-agreed pair must not snap sideways onto a nearby unknown head.
        boxes=anchors[oid]
        if len(boxes)==2 and iou(*boxes)>.65 and min(distance(d,b)/scale(b) for b in boxes)>.2:continue
        by_object[oid].append((value+.1*(1-d[4]),d))
    result={}
    for oid,items in by_object.items():
        items.sort(key=lambda x:x[0])
        if not items:continue
        if len(items)>1 and items[1][0]-items[0][0]<.2 and not near_same(items[0][1],items[1][1]):continue
        result[oid]=items[0][1][:4]
    return result


def typical_size(boxes):
    """Median detector width/height of one identity, or None without enough support."""
    if len(boxes)<SIZE_SUPPORT_MIN:return None
    widths=sorted(b[2]-b[0] for b in boxes);heights=sorted(b[3]-b[1] for b in boxes)
    return widths[len(widths)//2],heights[len(heights)//2]


def resized(b,size,width,height):
    (cx,cy),(w,h)=center(b),size
    x0=min(max(0.,cx-w/2),max(0.,width-w));y0=min(max(0.,cy-h/2),max(0.,height-h))
    return [x0,y0,min(float(width),x0+w),min(float(height),y0+h)]


def options(f,r,d,manual,fraction,typical=None,width=None,height=None):
    if manual is not None:return [('M',manual,0.)]
    available=[(k,b) for k,b in [('F',f),('R',r),('D',d)] if b is not None]
    sized=[]
    if d is None and typical is not None and width and height:
        limit=SIZE_LOOSE_RATIO*math.hypot(*typical)
        for b in (f,r):
            if b is not None and scale(b)>limit:sized.append(resized(b,typical,width,height))
    # Consensus only among geometrically close boxes. Never average heads that
    # disagree in position, and never interpolate a missing observation.
    groups=[]
    for k,a in available:
        group=[b for _,b in available if iou(a,b)>=.5 and distance(a,b)<=.35*min(scale(a),scale(b))]
        if len(group)>=2:groups.append(group)
    if groups:
        group=max(groups,key=len)
        median=[sorted(b[i] for b in group)[len(group)//2] for i in range(4)]
        available.append(('C',median))
    scored=[]
    for b in sized:
        # Same centre as the loose tracker box: judged by size continuity in
        # temporal_path, not by its (intended) disagreement with that box.
        scored.append(('S',b,.25))
    for kind,b in available:
        cost={'F':.3*fraction,'R':.3*(1-fraction),'D':.15,'C':.05}[kind]
        if f is not None:cost+=.6*(1-iou(b,f))
        if r is not None:cost+=.6*(1-iou(b,r))
        if d is not None:cost+=2*(1-iou(b,d))
        scored.append((kind,b,cost))
    return scored


def temporal_path(frames, choices):
    selected={};active=[]
    def finish():
        if not active:return
        previous={};costs=[];links=[]
        for fi in active:
            current={};back={}
            for j,(kind,b,unary) in enumerate(choices[fi]):
                if not previous:current[j]=unary;back[j]=None;continue
                possibilities=[]
                prior_fi=active[len(costs)-1]
                for k,value in previous.items():
                    pk,pb,_=choices[prior_fi][k]
                    transition=.6*min(5.,distance(b,pb)/max(scale(b),scale(pb)))
                    # Intermittent detector confidence must not alternate
                    # between tight detection and loose tracker sizes. Human
                    # boxes can intentionally include padding, so their fixed
                    # anchor geometry does not impose that size penalty.
                    manual_edge=pk=='M' or kind=='M'
                    transition+=(.1 if manual_edge else 1.)*min(3.,abs(math.log(scale(b)/scale(pb))))
                    transition+=.12*(pk!=kind and not manual_edge)
                    possibilities.append((value+transition,k))
                best,parent=min(possibilities)
                current[j]=unary+best;back[j]=parent
            costs.append(current);links.append(back);previous=current
        state=min(previous,key=previous.get)
        for i in range(len(active)-1,-1,-1):
            selected[active[i]]=choices[active[i]][state]
            state=links[i][state]
        active.clear()
    for fi in frames:
        if not choices[fi]:finish();continue
        active.append(fi)
    finish()
    return selected


def combine_tracks(rows,reverse,matches,candidates,*,width,height,manual=None,excluded=None):
    """Validate every input, compute all paths, then change rows atomically in RAM."""
    manual=manual or {}
    excluded=excluded or {}
    frames={r['source_frame_index']:r for r in rows}
    if len(frames)!=len(rows) or not frames or set(frames)!=set(range(max(frames)+1)):
        raise ValueError('综合标注缺少完整帧，不能生成成功结果')
    order=sorted(frames);end=order[-1]
    scope={o['object_id'] for row in rows for o in row['objects']}
    inverse={int(fid):int(tid) for tid,fid in matches.items()}
    if len(inverse)!=len(matches) or not set(inverse)<=scope:raise ValueError('综合标注身份对应无效')
    def checked(b):
        if not valid_box(b) or b[0]<0 or b[1]<0 or b[2]>width or b[3]>height:raise ValueError('综合标注框坐标无效')
    forward={}
    for fi,row in frames.items():
        forward[fi]={int(o['object_id']):o['bbox'] for o in row['objects']}
        if len(forward[fi])!=len(row['objects']):raise ValueError('综合标注出现重复身份')
        for b in forward[fi].values():checked(b)
        if fi not in candidates:raise ValueError('检测观测缺失，不能冒充检测为空')
        for d in candidates[fi]:
            if not isinstance(d,(list,tuple)) or len(d)!=5 or type(d[4]) not in (float,int) or not math.isfinite(d[4]) or not 0<=d[4]<=1:raise ValueError('头部检测结果无效')
            checked(d[:4])
        for b in reverse.get(fi,{}).values():checked(b)
        for oid,b in manual.get(fi,{}).items():
            if oid not in scope:raise ValueError('人工参考包含未知身份')
            checked(b)
    assigned={}
    for fi in order:
        anchors={oid:[b for b in [forward[fi].get(oid),reverse.get(fi,{}).get(inverse.get(oid))] if b is not None] for oid in scope if oid not in excluded.get(fi,set())}
        assigned[fi]=detector_assignments(anchors,candidates[fi])
    sizes={oid:typical_size([assigned[fi][oid] for fi in order if oid in assigned[fi]]) for oid in scope}
    paths={};choices={}
    lifetimes={oid:[fi for fi in order if oid in forward[fi] or inverse.get(oid) in reverse.get(fi,{})] for oid in scope}
    for oid in sorted(scope):
        choices[oid]={fi:([] if oid in excluded.get(fi,set()) else options(forward[fi].get(oid),reverse.get(fi,{}).get(inverse.get(oid)),assigned[fi].get(oid),manual.get(fi,{}).get(oid),fi/max(1,end),sizes[oid],width,height)) for fi in order}
        paths[oid]=temporal_path(order,choices[oid])
    # Keep identities separate. If selected boxes collapse onto one visible
    # head, prefer a distinct supported alternative; otherwise request review.
    conflicts={fi:set() for fi in order}
    for fi in order:
        ids=[oid for oid in sorted(scope) if fi in paths[oid]]
        for i,oid in enumerate(ids):
            for other in ids[:i]:
                if not near_same(paths[oid][fi][1],paths[other][fi][1]):continue
                alternatives=[v for v in choices[oid][fi] if not near_same(v[1],paths[other][fi][1]) and (v[0]=='M' or (assigned[fi].get(oid) is not None and iou(v[1],assigned[fi][oid])>=.5))]
                if alternatives:paths[oid][fi]=min(alternatives,key=lambda v:v[2])
                else:conflicts[fi].update((oid,other))
    drafts=copy.deepcopy(rows);by_frame={r['source_frame_index']:r for r in drafts}
    templates={o['object_id']:o for r in rows for o in r['objects']}
    changed=recovered=review_count=0;selection_counts={}
    for fi,row in by_frame.items():
        objects=[];uncertain=set(conflicts[fi]);supported=set()
        for oid in sorted(scope):
            value=paths[oid].get(fi)
            if value is None:
                lifetime=lifetimes[oid]
                if oid not in excluded.get(fi,set()) and lifetime and min(lifetime)<fi<max(lifetime):uncertain.add(oid)
                continue
            kind,b,_=value;f=forward[fi].get(oid);r=reverse.get(fi,{}).get(inverse.get(oid));d=assigned[fi].get(oid)
            selection_counts[kind]=selection_counts.get(kind,0)+1
            if d is not None and iou(b,d)>=.5:supported.add(oid)
            if f is not None and r is not None and iou(f,r)<.5 and oid not in supported:uncertain.add(oid)
            if f is None and oid not in supported:uncertain.add(oid)
            obj=copy.deepcopy(next((o for o in row['objects'] if o['object_id']==oid),templates[oid]))
            if f is None or any(abs(a-c)>.001 for a,c in zip(f,b)):
                changed+=1;recovered+=int(f is None)
                obj.update(bbox=[round(float(v),3) for v in b],source='joint_tracking',score=None,mask_area=None)
                for key in ['anomaly','anomaly_level','anomaly_reasons','anomaly_details']:obj.pop(key,None)
            objects.append(obj)
        row['objects']=objects
        evidence=row.setdefault('reviewEvidence',dict(state='not_sampled',findings=[],backwardCheck='not_run'))
        resolved=supported-uncertain
        removable=('endpoint_disagreement','backward_disagreement','tracking_lost','detector_mismatch','detector_unavailable_for_object','head_boundary','subject_identity')
        evidence['findings']=[f for f in evidence.get('findings',[]) if not (f.get('objectId') in resolved and (f['kind'] in removable or (f['kind']=='endpoint_identity_uncertain' and f.get('objectId') in inverse)))]
        # A former unassociated detector candidate may now be this exact
        # synthesized head. Only remove a near-identical, uniquely used sample.
        evidence['findings']=[f for f in evidence['findings'] if not (f['kind']=='unlabelled_candidate' and valid_box(f.get('bbox')) and any(oid in resolved and iou(f['bbox'],d)>.8 for oid,d in assigned[fi].items()))]
        for oid in sorted(uncertain):
            evidence['findings'].append(dict(objectId=oid,kind='joint_uncertain',message='自动标注仍有不确定性，请结合前后画面核对头部与身份',bbox=paths[oid].get(fi,('',None,0))[1]))
        evidence['jointCheck']=dict(state='checked',policy=POLICY,uncertainObjectIds=sorted(uncertain))
        review_count+=len(uncertain)
    from .track_lifecycle import protect_boundary_exits
    drafts=protect_boundary_exits(drafts,width=width,height=height,manual=manual,trajectory=rows)
    for row,draft in zip(rows,drafts):row.clear();row.update(draft)
    report=dict(version=1,policy=POLICY,state='checked',checkedFrames=len(rows),changedObjects=changed,recoveredObjects=recovered,reviewCount=review_count)
    log.info('tracking.joint_completed frames=%s changed=%s recovered=%s review=%s selections=%s',len(rows),changed,recovered,review_count,selection_counts)
    return report


def synthesize_video(video,rows,reverse,endpoint,workspace,progress=None):
    """One native frame at a time; no detector failure masquerades as empty."""
    from .video_frames import open_capture_at
    from .head_click import detect_regions
    from .annotation_state import is_deleted
    if endpoint.get('state') not in ('checked','insufficient'):raise ValueError('反向追踪未完成，无法完成综合标注；请检查尾帧或原始视频后重试')
    reader=open_capture_at(video,0)
    try:
        import cv2
        width,height=int(reader.get(cv2.CAP_PROP_FRAME_WIDTH)),int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT))
        manual={};end=max(r['source_frame_index'] for r in rows)
        scope={o['object_id'] for row in rows for o in row['objects']}
        for obj in workspace.get('manualAnnotations',[]):
            fi=obj.get('frameIndex',0);oid=obj.get('objectId');b=obj.get('bbox')
            if obj.get('source')!='manual' or not b:continue
            if fi==end and obj.get('endpointAnchorHash')!=endpoint['anchorHash']:oid=endpoint['matches'].get(str(oid),oid)
            if oid in scope:manual.setdefault(fi,{})[oid]=[b['x']*width/100,b['y']*height/100,(b['x']+b['width'])*width/100,(b['y']+b['height'])*height/100]
        observed={}
        for row in rows:
            fi=row['source_frame_index'];ok,image=reader.read()
            if not ok or image.shape[:2]!=(height,width):raise ValueError(f'原始第 {fi+1} 帧读取失败，综合标注未完成')
            if progress:progress(stage='checking',phase='combine',phaseFrom=0,phaseTo=end,phaseCurrent=fi,message=f'正在生成标注：第 {fi+1} / {end+1} 帧')
            boxes=[o['bbox'] for o in row['objects']]+list(reverse.get(fi,{}).values())
            observed[fi]=detect_regions(image,boxes) if fi not in (0,end) else []
        excluded={r['source_frame_index']:{oid for oid in scope if is_deleted(workspace,r['source_frame_index'],oid)} for r in rows}
        result=combine_tracks(rows,reverse,endpoint['matches'],observed,width=width,height=height,manual=manual,excluded=excluded)
        from .endpoint_tracking import project_endpoints
        projected=project_endpoints(rows,width=width,height=height)
        for row,draft in zip(rows,projected):
            if row is not draft:row.clear();row.update(draft)
        return result
    finally:reader.release()
