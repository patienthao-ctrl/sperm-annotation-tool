"""Endpoint geometry tolerance, and rebuilding an open checklist of an older policy."""
import pytest
from app import annotation_review as review, db
from .test_review_completion import source, INFO
from .test_review_workflow import task  # noqa: F401 (fixture used by source)


def finding(a=None, b=None, **extra):
    return dict(kind='endpoint_disagreement',objectId=1,message='人工首尾独立追踪位置不一致',
                bbox=a or [51,503,82,539],reverseBox=b or [45,501,92,559],**extra)


def test_threshold_is_resolution_relative():
    from app.endpoint_tracking import endpoint_disagreement_requires_review
    f=finding()
    for multiplier in [1,2,4]:
        scaled={**f,'bbox':[v*multiplier for v in f['bbox']],'reverseBox':[v*multiplier for v in f['reverseBox']]}
        assert not endpoint_disagreement_requires_review(scaled)
    # A substantially shifted head must still be reviewed, even with similar size.
    assert endpoint_disagreement_requires_review(finding([0,0,30,30],[14,0,44,30]))


def test_refresh_failure_does_not_discard_saved_items_and_recovers(source,monkeypatch):
    state=review.start('complete-media',source,INFO,1)
    with db.connect() as c:
        c.execute('UPDATE annotation_reviews SET evidence_policy=NULL WHERE id=?',(state['reviewId'],))
        before=dict(review.latest(c,'complete-media'))
    build=review.build_target_items
    def fail(*a,**kw):raise RuntimeError('injected checklist rebuild failure')
    monkeypatch.setattr(review,'build_target_items',fail)
    with pytest.raises(RuntimeError):review.status('complete-media',source,INFO)
    with db.connect() as c:assert dict(review.latest(c,'complete-media'))==before
    monkeypatch.setattr(review,'build_target_items',build)
    assert review.status('complete-media',source,INFO)['status']=='open'
