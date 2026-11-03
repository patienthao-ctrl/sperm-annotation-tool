"""Two-stage review acceptance and evidence grouping; isolated fixtures only."""
import json
import uuid
import pytest
from app import db, review_workflow as w
from .test_review_workflow import task, edit, done


def enable(sid):
    from app.concentrated_review import register
    from app import confirmation_workflow as cf
    with db.connect() as c:
        cf.migrate(c)
        register(c, sid, [], 'source-hash')
        c.commit()


def test_review_is_final_acceptance_and_replay(task):
    enable(task)
    edit(task, 'submit', [{'objectId': 1, 'bbox': [11, 20, 31, 40]}])
    edit(task, 'submit', fi=1)
    edit(task, 'submit', fi=2)
    body = {'expectedSessionRevision': w.get_session(task, 2)['revision']}
    result = w.write('finish', task, 2, 'finish-direct', body)
    assert result == w.write('finish', task, 2, 'finish-direct', body)
    sid = result['session']
    assert sid['workflow'] == 'concentrated'
    assert sid['finalVersionId']
    with db.connect() as c:
        row = c.execute('SELECT objects_json FROM final_version_frames WHERE final_version_id=? AND frame_index=0', (sid['finalVersionId'],)).fetchone()
        assert json.loads(row[0])[0]['bbox'] == [11, 20, 31, 40]
        assert c.execute('SELECT COUNT(*) FROM final_versions').fetchone()[0] == 1


def test_doubts_never_substitute_for_whole_frame_review(task):
    enable(task)
    with pytest.raises(w.ReviewError, match='未提交'):
        done(task)
    assert w.get_session(task, 2)['state'] != 'reviewed'


def test_group_contiguous_same_object_reason_only():
    from app.concentrated_review import group_findings
    rows = [{'source_frame_index': i, 'reviewEvidence': {'state': 'checked', 'findings': [
        {'objectId': 2, 'kind': 'detector_mismatch', 'message': '位置不一致', 'bbox': [1,2,3,4]}
    ]}} for i in [1,2,3,8]]
    groups = group_findings(rows)
    assert [(g['startFrame'],g['endFrame']) for g in groups] == [(1,3),(8,8)]
    assert groups[0]['samples'][0]['bbox'] == [1,2,3,4]
