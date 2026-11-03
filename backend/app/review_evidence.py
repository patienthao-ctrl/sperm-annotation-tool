"""Per-frame tracking evidence kept with tracking results; never approvals."""
import logging
import math

log = logging.getLogger('review.tracking')


def valid_box(box):
    return (isinstance(box, (list,tuple)) and len(box)==4
            and all(type(v) in (int,float) and math.isfinite(v) for v in box)
            and box[2]>box[0] and box[3]>box[1])


class ReviewEvidence:
    """Per-frame identity risks from the tracker's own image evidence.

    Boundary-exit protection reads these. Review no longer consumes tracking
    evidence (every frame is checked by a person), so no detector sampling or
    reverse spot checks run here.
    """
    def __init__(self, video, first_frame=0):
        self.video = video
        self.first_frame = first_frame

    def observe(self, frame, boxes, identity=None):
        findings = []
        for oid, item in (identity or {}).items():
            if item.get('status') == 'risk' and not all('tail' in reason for reason in item.get('reasons', []) or ['unknown']):
                findings.append(dict(objectId=int(oid), kind='subject_identity', message='头部位置或身份证据存在疑点，请查看前后帧', bbox=boxes.get(int(oid)), reasons=item.get('reasons', [])))
        return dict(version=1, state='not_sampled', findings=findings, backwardCheck='not_run')
