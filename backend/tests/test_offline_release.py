"""Split release assets and server-side --assemble; fake Git/Docker, small parts, no daemon."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
REVISION = "a" * 40
PART = 64


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def updater():
    return load("offline_update_parts_under_test", "scripts/offline_update.py")


@pytest.fixture
def release():
    return load("offline_release_under_test", "scripts/offline_release.py")


def split_bundle(release, directory, payload, part_size=PART):
    """Bundle as published: manifest names the whole images.tar, only parts are present."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "images.tar").write_bytes(payload)
    manifest = {"files": {"images.tar": hashlib.sha256(payload).hexdigest()}}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    entry = release.split_file(directory / "images.tar", part_size)
    (directory / "parts.json").write_text(json.dumps({"format": 1, "partSize": part_size, "files": [entry]}))
    return entry


def payload_of(size):
    return bytes((i * 7 + 3) % 256 for i in range(size))


@pytest.mark.parametrize("size,count", [(1, 1), (PART - 1, 1), (PART, 1), (PART + 1, 2), (2 * PART, 2), (2 * PART + 1, 3)])
def test_parts_never_exceed_limit_and_reassemble_byte_identical(updater, release, tmp_path, size, count):
    payload = payload_of(size)
    entry = split_bundle(release, tmp_path / "bundle", payload)
    assert [p["asset"] for p in entry["parts"]] == ["images.tar.part%02d" % i for i in range(1, count + 1)]
    assert all(0 < p["size"] <= PART for p in entry["parts"]) and entry["parts"][0]["size"] == min(size, PART)
    assert not (tmp_path / "bundle/images.tar").exists()
    updater.assemble(tmp_path / "bundle")
    assert (tmp_path / "bundle/images.tar").read_bytes() == payload
    assert not list((tmp_path / "bundle").glob(".images.tar.assembling-*"))


def test_stream_split_matches_file_split(release, tmp_path):
    import io
    payload = payload_of(3 * PART + 5)
    (tmp_path / "s").mkdir()
    streamed = release.write_parts(io.BytesIO(payload), tmp_path / "s", "images.tar", PART)
    filed = split_bundle(release, tmp_path / "f", payload)
    assert streamed == filed and streamed["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("bad", [0, 2 * 1024**3])
def test_part_size_must_fit_github_assets(release, tmp_path, bad):
    with pytest.raises(release.ReleaseError):
        release.write_parts(open(os.devnull, "rb"), tmp_path, "images.tar", bad)


def assert_rejected(updater, directory, text):
    with pytest.raises(updater.UpdateError) as error:
        updater.assemble(directory)
    assert text in str(error.value)
    assert not (directory / "images.tar").exists()
    assert not list(directory.glob(".images.tar.assembling-*"))


def test_missing_part_is_named_and_nothing_is_written(updater, release, tmp_path):
    split_bundle(release, tmp_path / "b", payload_of(3 * PART))
    (tmp_path / "b/images.tar.part02").unlink()
    assert_rejected(updater, tmp_path / "b", "images.tar.part02")


def test_tampered_part_with_same_size_is_named(updater, release, tmp_path):
    split_bundle(release, tmp_path / "b", payload_of(3 * PART))
    part = tmp_path / "b/images.tar.part03"
    data = bytearray(part.read_bytes())
    data[5] ^= 0xFF
    part.write_bytes(bytes(data))
    assert_rejected(updater, tmp_path / "b", "分块校验失败（已损坏或被修改），请重新下载：images.tar.part03")


def test_truncated_part_is_rejected_before_merging(updater, release, tmp_path):
    split_bundle(release, tmp_path / "b", payload_of(3 * PART))
    part = tmp_path / "b/images.tar.part01"
    part.write_bytes(part.read_bytes()[:-1])
    assert_rejected(updater, tmp_path / "b", "分块大小不符")


def test_part_from_another_release_is_rejected(updater, release, tmp_path):
    split_bundle(release, tmp_path / "b", payload_of(PART + 1))
    (tmp_path / "b/images.tar.part03").write_bytes(b"other version")
    assert_rejected(updater, tmp_path / "b", "images.tar.part03")


@pytest.mark.parametrize("change", ["manifest", "order", "sizes", "format", "name"])
def test_inconsistent_parts_list_is_rejected(updater, release, tmp_path, change):
    split_bundle(release, tmp_path / "b", payload_of(2 * PART + 1))
    plan = json.loads((tmp_path / "b/parts.json").read_text())
    entry = plan["files"][0]
    if change == "manifest":
        (tmp_path / "b/manifest.json").write_text(json.dumps({"files": {"images.tar": "0" * 64}}))
    elif change == "order":
        entry["parts"].reverse()
    elif change == "sizes":
        entry["size"] += 1
    elif change == "format":
        plan["format"] = 2
    elif change == "name":
        entry["name"] = "../images.tar"
    (tmp_path / "b/parts.json").write_text(json.dumps(plan))
    with pytest.raises(updater.UpdateError):
        updater.assemble(tmp_path / "b")
    assert not (tmp_path / "b/images.tar").exists()


def test_existing_archive_is_rechecked_and_parts_can_be_removed(updater, release, tmp_path):
    payload = payload_of(2 * PART)
    split_bundle(release, tmp_path / "b", payload)
    updater.assemble(tmp_path / "b")
    updater.assemble(tmp_path / "b", remove_parts=True)
    assert (tmp_path / "b/images.tar").read_bytes() == payload
    assert not list((tmp_path / "b").glob("images.tar.part*"))
    (tmp_path / "b/images.tar").write_bytes(b"x" + payload[1:])
    with pytest.raises(updater.UpdateError, match="已存在但与清单不符"):
        updater.assemble(tmp_path / "b")


# --- complete release packaging with fake Git/Docker -------------------------

FAKE = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
name = os.path.basename(sys.argv[0])
if name == "git":
    if args[:2] == ["rev-parse", "--is-shallow-repository"]: print("false")
    elif args[:2] == ["rev-parse", "HEAD"]: print(os.environ["FAKE_REVISION"])
    elif args[0] == "rev-list": print(os.environ["FAKE_REVISION"]); print("d" * 40)
    elif args[0] == "status": pass
    else: sys.exit(9)
else:
    if args[:2] == ["image", "inspect"]:
        mode = os.environ.get("FAKE_LABEL_MODE", "gpu")
        labels = {{"org.opencontainers.image.revision": os.environ["FAKE_REVISION"],
                  "org.opencontainers.image.version": os.environ["FAKE_VERSION"],
                  "io.sperm-annotation.offline-contract": "1", "io.sperm-annotation.mode": mode}}
        digit = "b" if "backend" in args[2] else "c"
        print(json.dumps([{{"Id": "sha256:" + digit * 64, "Size": 12345, "Os": "linux", "Architecture": "amd64",
                           "Config": {{"Labels": labels}}}}]))
    elif args[0] == "save":
        if os.environ.get("FAKE_SAVE_FAIL"): sys.exit(3)
        with open(os.environ["FAKE_IMAGES"], "rb") as f: sys.stdout.buffer.write(f.read())
    else: sys.exit(9)
'''


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("git", "docker"):
        path = bin_dir / name
        path.write_text(FAKE.format(python=sys.executable))
        path.chmod(0o755)
    images = tmp_path / "fake-images.tar"
    images.write_bytes(payload_of(5 * PART + 9))
    source = tmp_path / "source"
    for relative in ["update-offline.sh", "scripts/offline_update.py", "install-offline.sh"]:
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, source / relative)
    for relative in ["export-audit.sh", "scripts/offline_legacy.json", "compose.yaml", "compose.gpu.yaml", ".env.docker.example"]:
        (source / relative).write_text("fixture " + relative + "\n")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_REVISION", REVISION)
    monkeypatch.setenv("FAKE_VERSION", "v1.3.0")
    monkeypatch.setenv("FAKE_IMAGES", str(images))
    return source, images


def test_release_assets_download_assemble_and_verify(updater, release, fake_tools, tmp_path):
    source, images = fake_tools
    assets = tmp_path / "assets"
    manifest, parts, tools = release.package(source, "v1.3.0", REVISION, "gpu", assets, PART)
    names = sorted(p.name for p in assets.iterdir())
    assert names == sorted(["SHA256SUMS", "install-offline.sh", tools.name] + ["images.tar.part%02d" % i for i in range(1, 7)])
    assert all((assets / n).stat().st_size <= PART for n in names if ".part" in n)
    for line in (assets / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == digest
    assert manifest["files"]["images.tar"] == hashlib.sha256(images.read_bytes()).hexdigest()
    assert manifest["revision"] == REVISION and manifest["sourceHistory"] == [REVISION, "d" * 40]
    assert manifest["images"]["backend"]["tag"] == "sperm-annotation-backend:v1.3.0-gpu"

    # Hospital: every asset copied into one directory, tools extracted beside the parts.
    server = tmp_path / "server package"
    shutil.copytree(assets, server)
    with tarfile.open(server / tools.name) as archive:
        assert all(not m.name.startswith(("/", "..")) and ".env" != m.name for m in archive.getmembers())
        archive.extractall(server)
    with pytest.raises(updater.UpdateError, match="--assemble"):
        updater.verify_bundle(server)
    result = subprocess.run(["bash", str(server / "update-offline.sh"), "--assemble"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (server / "images.tar").read_bytes() == images.read_bytes()
    assert updater.verify_bundle(server) == manifest


def test_assemble_cli_reports_bad_part_and_rejects_deployment_argument(release, fake_tools, tmp_path):
    source, _ = fake_tools
    assets = tmp_path / "assets"
    _, _, tools = release.package(source, "v1.3.0", REVISION, "gpu", assets, PART)
    with tarfile.open(tools) as archive:
        archive.extractall(assets)
    (assets / "images.tar.part04").write_bytes(b"\0" * PART)
    script = str(assets / "update-offline.sh")
    result = subprocess.run(["bash", script, "--assemble"], capture_output=True, text=True)
    assert result.returncode == 1 and "images.tar.part04" in result.stderr
    assert not (assets / "images.tar").exists()
    result = subprocess.run(["bash", script, str(tmp_path), "--assemble"], capture_output=True, text=True)
    assert result.returncode == 2


@pytest.mark.parametrize("failure", ["label", "save", "existing"])
def test_packaging_failure_publishes_nothing(release, fake_tools, tmp_path, monkeypatch, failure):
    source, _ = fake_tools
    assets = tmp_path / "assets"
    if failure == "label":
        monkeypatch.setenv("FAKE_LABEL_MODE", "cpu")
    elif failure == "save":
        monkeypatch.setenv("FAKE_SAVE_FAIL", "1")
    else:
        assets.mkdir()
        (assets / "keep").write_text("delivered")
    with pytest.raises(release.ReleaseError):
        release.package(source, "v1.3.0", REVISION, "gpu", assets, PART)
    if failure == "existing":
        assert [p.name for p in assets.iterdir()] == ["keep"]
    else:
        assert not assets.exists()
