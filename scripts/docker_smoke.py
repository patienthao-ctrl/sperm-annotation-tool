"""Disposable Compose acceptance: real upload → review pass → YOLO + restart check.

Requires APP_DATA_ROOT under work/ and an explicit loopback SMOKE_ORIGIN.
Never run against production. Credentials are kept only in ignored work/.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
import uuid
import zipfile

root = Path(__file__).resolve().parents[1]
data_root = Path(os.environ['APP_DATA_ROOT']).resolve()
assert data_root.is_relative_to(root/'work'), 'Use isolated work/ data'
origin = os.environ.get('SMOKE_ORIGIN', 'http://127.0.0.1:18080')
assert urlparse(origin).hostname in {'127.0.0.1', 'localhost'}
state = Path(os.environ.get('SMOKE_STATE_FILE', root/'work/docker-smoke-state.json')).resolve()
assert state.is_relative_to(root/'work'), 'Keep smoke credentials in isolated work/'
token = ''


def request(path, method='GET', data=None, raw=False, headers=None):
    h = {'Authorization':'Bearer '+token, 'X-Review-Contract':'2', 'Idempotency-Key':uuid.uuid4().hex}
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode(); h['Content-Type'] = 'application/json'
    h.update(headers or {})
    req = urllib.request.Request(origin+path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        raise AssertionError(f'{method} {path}: {error.code} {error.read().decode()}') from None
    return payload if raw else json.loads(payload)


assert b'<html' in request('/annotate', raw=True)
assert request('/api/health')['ok']
if '--verify-restart' in sys.argv:
    saved = json.loads(state.read_text()); token = saved['token']
    assert request('/api/auth/me')['username'] == saved['username']
    assert request('/api/track/workspace/'+saved['media'])['manualAnnotations']
    assert request('/api/annotation-review/media/'+saved['media'])['status'] == 'passed'
    assert request('/api/datasets/exports/'+saved['export'])['state'] == 'ready'
    assert request('/api/datasets/exports/'+saved['export']+'/download', raw=True).startswith(b'PK')
    print('PASS restart: login token, workspace, review pass and training ZIP persisted')
    sys.exit(0)

username = 'smoke-'+uuid.uuid4().hex[:10]
account = request('/api/auth/register','POST',{'username':username,'password':uuid.uuid4().hex})
token = account['token']
results = request('/api/annotation/projects/default/results')
assert isinstance(results['items'], list)
if '--fresh' in sys.argv: assert results['total'] == 0
print('PASS clean install: SPA, health, registration and annotation results')

# Generate a real two-frame video with container dependencies; no host OpenCV needed.
video = subprocess.check_output(['docker','compose','exec','-T','backend','python','-c', '''
import cv2,numpy as np,tempfile,pathlib,sys
with tempfile.TemporaryDirectory() as d:
 p=pathlib.Path(d)/'smoke.avi'
 w=cv2.VideoWriter(str(p),cv2.VideoWriter_fourcc(*'MJPG'),5,(80,60))
 for i in range(2): w.write(np.full((60,80,3),100+i*30,dtype=np.uint8))
 w.release(); sys.stdout.buffer.write(p.read_bytes())
'''],cwd=root)
boundary = 'boundary'+uuid.uuid4().hex
body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{username}.avi"\r\nContent-Type: video/x-msvideo\r\n\r\n').encode()+video+f'\r\n--{boundary}--\r\n'.encode()
media = request('/api/track/upload','POST',body,headers={'Content-Type':'multipart/form-data; boundary='+boundary})['mediaId']
objects = [{'id':f'manual-{i}', 'objectId':1, 'source':'manual', 'name':'sperm', 'frameIndex':i,'bbox':{'x':10,'y':10,'width':20,'height':20}} for i in range(2)]
request('/api/annotation/annotations/manual','POST',{'mediaId':media,'mediaName':username+'.avi','mediaType':'video','mediaWidth':80,'mediaHeight':60,'objects':objects})
assert request('/api/annotation/media/'+media)['total'] == 2
request('/api/track/workspace/'+media,'PUT',{'manualAnnotations':objects})
review = '/api/annotation-review/media/'+media
current = request(review+'/start','POST')
rid = current['review']['reviewId']
assert current['status'] == 'open' and current['review']['total'] > 0
for start, end in current['review']['frames']:
    for frame in range(start, end + 1):
        # Frames with boxes are confirmed; frames without any annotation are confirmed empty.
        result = 'ok' if any(o['frameIndex'] == frame for o in objects) else 'empty'
        request(review+'/frames/'+str(frame),'PUT',{'reviewId':rid,'result':result})
assert request(review+'/pass','POST',{'reviewId':rid})['status'] == 'passed'
preview = request('/api/datasets/annotations/'+media+'/preview')
final = request('/api/datasets/annotations/'+media+'/snapshot','POST',{'expectedSourceRevision':preview['sourceRevision'],'confirmComplete':True,'explicitEmptyFrameRanges':[]})['finalVersionId']
job = request('/api/datasets/exports','POST',{'finalVersionIds':[final],'format':'yolo','splitRatio':0.5})
for _ in range(60):
    job = request('/api/datasets/exports/'+job['exportId'])
    if job['state'] in {'ready','failed'}: break
    time.sleep(1)
assert job['state'] == 'ready', job
archive = zipfile.ZipFile(io.BytesIO(request('/api/datasets/exports/'+job['exportId']+'/download',raw=True)))
assert 'data.yaml' in archive.namelist()
assert len([n for n in archive.namelist() if n.endswith('.jpg')]) == 2
assert len([n for n in archive.namelist() if n.endswith('.txt')]) == 2
state.parent.mkdir(exist_ok=True)
state.write_text(json.dumps({'token':token,'username':username,'media':media,'export':job['exportId']}))
state.chmod(0o600)
print('PASS real video upload, workspace, explicit review pass and YOLO images/labels')
