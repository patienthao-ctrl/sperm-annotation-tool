"""Developer checkout contains the real pinned artifact; never runs inference."""
import hashlib
import json
from pathlib import Path
import pytest
from app import head_click

ASSETS=Path(head_click.__file__).parent/'model_assets'


def test_default_weight_is_in_checkout_and_matches_manifest(monkeypatch):
    monkeypatch.delenv('HEAD_DETECTOR_MODEL',raising=False)
    assert head_click.model_path()==ASSETS/'head-detector.pt'
    manifest=json.loads((ASSETS/'head-detector.json').read_text())
    path=head_click.model_path()
    assert path.stat().st_size==manifest['size']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==manifest['sha256']
    head_click.verify_bundled_weight(path)


def test_explicit_custom_model_path_still_takes_priority(tmp_path,monkeypatch):
    path=tmp_path/'custom.pt';path.write_bytes(b'custom')
    monkeypatch.setenv('HEAD_DETECTOR_MODEL',str(path))
    assert head_click.model_path()==path
    head_click.verify_bundled_weight(path)  # Custom artifacts are not relabelled as our model.


def test_corrupt_default_is_rejected_before_model_loading(tmp_path,monkeypatch):
    assets=tmp_path/'assets';assets.mkdir();weight=assets/'head-detector.pt';weight.write_bytes(b'version https://git-lfs.github.com/spec/v1')
    (assets/'head-detector.json').write_text(json.dumps({'size':123,'sha256':'0'*64}))
    monkeypatch.setattr(head_click,'BUNDLED_MODEL',weight)
    with pytest.raises(head_click.ProposalError,match='校验'):
        head_click.verify_bundled_weight(weight)


def test_all_detector_entry_points_use_shared_resolution(tmp_path,monkeypatch):
    import numpy as np
    path=tmp_path/'missing.pt'
    monkeypatch.setattr(head_click,'model_path',lambda:path)
    image=np.zeros((32,32,3),dtype=np.uint8)
    for call in [lambda:head_click.propose(image,10,10),lambda:head_click.detect_frame(image),lambda:head_click.detect_edges(image),lambda:head_click.detect_regions(image,[[5,5,10,10]])]:
        with pytest.raises(head_click.ProposalError):call()


def test_detector_uses_gpu_for_all_inference_paths_and_does_not_retry_cpu(tmp_path, monkeypatch):
    import numpy as np
    from types import SimpleNamespace

    weight = tmp_path / 'head.pt'
    weight.write_bytes(b'test')
    monkeypatch.setenv('HEAD_DETECTOR_MODEL', str(weight))
    monkeypatch.delenv('HEAD_DETECTOR_DEVICE', raising=False)
    calls = []

    class Values:
        def __init__(self, data): self.data = data
        def cpu(self): return self
        def tolist(self): return self.data

    result = SimpleNamespace(boxes=SimpleNamespace(xyxy=Values([[160, 160, 480, 480]]), conf=Values([0.9])))  # 32x32 frames run at 1024: x32
    def predict(images, **kwargs):
        calls.append(kwargs['device'])
        return [result] * (len(images) if isinstance(images, list) else 1)

    model = SimpleNamespace(predict=predict)
    monkeypatch.setattr(head_click, '_model', model)
    monkeypatch.setattr(head_click, '_model_path', str(weight.resolve()))
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    head_click.propose(image, 10, 10)
    head_click.detect_frame(image)
    head_click.detect_edges(image)
    head_click.detect_regions(image, [[5, 5, 15, 15]])
    assert calls and set(calls) == {'cuda:0'}

    calls.clear()
    def unavailable(*args, **kwargs):
        calls.append(kwargs['device'])
        raise RuntimeError('CUDA unavailable')
    model.predict = unavailable
    with pytest.raises(RuntimeError, match='CUDA unavailable'):
        head_click.detect_frame(image)
    assert calls == ['cuda:0']


def test_deployments_name_their_detector_device():
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[2]
    def device(name):
        return yaml.safe_load((root / name).read_text(encoding='utf-8'))['services']['backend']['environment']['HEAD_DETECTOR_DEVICE']
    # CPU-only deployments have no CUDA, so they cannot rely on the cuda:0 default.
    assert device('compose.yaml') == 'cpu'
    assert device('compose.gpu.yaml') == 'cuda:0'
