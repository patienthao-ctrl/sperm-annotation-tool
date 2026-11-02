"""One-command offline update: Release download scripts and install-offline.sh.

A local fake GitHub serves the Release JSON and assets; packages come from
scripts/offline_release.py with fake Git/Docker. No network, daemon or business data.
"""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest


ROOT = Path(__file__).resolve().parents[2]
PART = 64
REVISION = "a" * 40
HOST = os.environ.get("OFFLINE_TEST_HOST", "127.0.0.1")  # e.g. host.docker.internal for a pwsh container


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- fake GitHub Releases -----------------------------------------------------

class FakeGitHub:
    def __init__(self):
        self.files, self.json, self.hits, self.corrupt = {}, {}, {}, {}
        handler = self._handler()
        self.server = http.server.ThreadingHTTPServer(("0.0.0.0" if HOST != "127.0.0.1" else HOST, 0), handler)
        self.base = "http://%s:%d" % (HOST, self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0]
                fake.hits[path] = fake.hits.get(path, 0) + 1
                if path in fake.json:
                    body, kind = json.dumps(fake.json[path]).encode(), "application/json"
                elif path in fake.files:
                    body, kind = fake.files[path], "application/octet-stream"
                    remaining = fake.corrupt.get(path, 0)
                    if remaining:
                        fake.corrupt[path] = remaining - 1
                        body = b"X" + body[1:]
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        return Handler

    def release(self, tag, assets, prerelease=False, sums=True):
        listed = dict(assets)
        if sums:
            listed["SHA256SUMS"] = "".join("%s  %s\n" % (hashlib.sha256(d).hexdigest(), n) for n, d in sorted(assets.items())).encode()
        for name, data in listed.items():
            self.files["/download/%s/%s" % (tag, name)] = data
        return {"tag_name": tag, "draft": False, "prerelease": prerelease,
                "assets": [{"name": n, "browser_download_url": self.base + "/download/%s/%s" % (tag, n)} for n in listed]}

    def publish(self, *releases):
        self.json["/repos/o/r/releases"] = list(releases)
        for release in releases:
            self.json["/repos/o/r/releases/tags/" + release["tag_name"]] = release


def package_assets(version):
    data = {"sperm-annotation-%s-gpu-linux-amd64-files.tar.gz" % version: b"tools " + version.encode(),
            "images.tar.part01": b"A" * 70, "images.tar.part02": b"B" * 9, "install-offline.sh": b"#!/bin/bash\n"}
    return data


@pytest.fixture
def github():
    fake = FakeGitHub()
    newest = package_assets("v1.3.0")
    fake.publish(
        fake.release("sam3-model-6d06f0a5", {"model.safetensors.part01": b"m", "manifest.json": b"{}"}, sums=False),
        fake.release("v1.4.0", package_assets("v1.4.0"), prerelease=True),
        fake.release("v1.3.0", newest),
        fake.release("v1.2.0", package_assets("v1.2.0")),
    )
    yield fake, newest
    fake.server.shutdown()


def downloaders():
    runners = [pytest.param(lambda args: [sys.executable, str(ROOT / "download-offline-release.py")] + args, id="python")]
    # Windows CI has both: prefer Windows PowerShell 5.1, the hospital-side packaging baseline.
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell:
        def powershell(args):
            names = {"--version": "-Version", "--output": "-OutputDirectory", "--repository": "-Repository", "--api-base": "-ApiBase"}
            return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "download-offline-release.ps1")] + [names.get(a, a) for a in args]
        runners.append(pytest.param(powershell, id="powershell"))
    return runners


def run_download(command, github, tmp_path, *extra):
    fake, _ = github
    out = tmp_path / "out"
    args = ["--repository", "o/r", "--api-base", fake.base, "--output", str(out), *extra]
    return subprocess.run(command(args), capture_output=True, text=True, timeout=120), out


@pytest.mark.parametrize("command", downloaders())
def test_latest_skips_model_and_prerelease_and_verifies_every_asset(command, github, tmp_path):
    fake, assets = github
    result, out = run_download(command, github, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in out.iterdir()) == sorted(list(assets) + ["SHA256SUMS"])
    assert all((out / name).read_bytes() == data for name, data in assets.items())
    # A second run re-verifies existing files instead of downloading them again.
    before = dict(fake.hits)
    result, _ = run_download(command, github, tmp_path)
    assert result.returncode == 0
    assert fake.hits["/download/v1.3.0/images.tar.part01"] == before["/download/v1.3.0/images.tar.part01"]


@pytest.mark.parametrize("command", downloaders())
def test_specific_version_and_transient_corruption_is_redownloaded(command, github, tmp_path):
    fake, _ = github
    fake.corrupt["/download/v1.2.0/images.tar.part01"] = 1
    result, out = run_download(command, github, tmp_path, "--version", "v1.2.0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (out / "images.tar.part01").read_bytes() == b"A" * 70
    assert fake.hits["/download/v1.2.0/images.tar.part01"] == 2
    assert not list(out.glob("*.partial"))


@pytest.mark.parametrize("command", downloaders())
def test_persistent_corruption_stops_without_a_bad_file(command, github, tmp_path):
    fake, _ = github
    fake.corrupt["/download/v1.3.0/images.tar.part02"] = 99
    result, out = run_download(command, github, tmp_path)
    assert result.returncode == 1
    assert "images.tar.part02" in result.stdout + result.stderr
    assert not (out / "images.tar.part02").exists()


@pytest.mark.parametrize("command", downloaders())
@pytest.mark.parametrize("problem", ["not-offline-package", "missing-version", "unlisted-asset"])
def test_wrong_release_or_incomplete_checksums_are_rejected(command, github, tmp_path, problem):
    fake, _ = github
    version = {"not-offline-package": "sam3-model-6d06f0a5", "missing-version": "v0.0.1"}.get(problem, "v1.3.0")
    if problem == "unlisted-asset":
        release = fake.json["/repos/o/r/releases/tags/v1.3.0"]
        fake.files["/download/v1.3.0/extra.bin"] = b"unchecked"
        release["assets"].append({"name": "extra.bin", "browser_download_url": fake.base + "/download/v1.3.0/extra.bin"})
    result, out = run_download(command, github, tmp_path, "--version", version)
    assert result.returncode == 1, result.stdout
    assert not (out / "images.tar.part01").exists()


# --- install-offline.sh -------------------------------------------------------

RECORDER = """#!/usr/bin/env bash
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
echo "$*" >> "$here/calls.log"
if [[ "${FAIL_AT:-}" == "$1 ${2:-}" || "${FAIL_AT:-}" == "$*" ]]; then echo "simulated failure: $*" >&2; exit 1; fi
if [[ $1 == --assemble ]]; then exec python3 "$here/scripts/offline_update.py" --assemble; fi
"""


@pytest.fixture
def release_folder(tmp_path, monkeypatch):
    """Release assets as downloaded, built by the real packager; update-offline.sh records calls."""
    release = load("offline_release_oneclick", "scripts/offline_release.py")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = (ROOT / "backend/tests/test_offline_release.py").read_text().split("FAKE = r'''", 1)[1].split("'''", 1)[0]
    for name in ("git", "docker"):
        (bin_dir / name).write_text(fake.format(python=sys.executable))
        (bin_dir / name).chmod(0o755)
    images = tmp_path / "images.tar"
    images.write_bytes(bytes(range(256)) * 2)
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "update-offline.sh").write_text(RECORDER)
    shutil.copy2(ROOT / "scripts/offline_update.py", source / "scripts/offline_update.py")
    shutil.copy2(ROOT / "install-offline.sh", source / "install-offline.sh")
    for relative in ["export-audit.sh", "scripts/offline_legacy.json", "compose.yaml", "compose.gpu.yaml", ".env.docker.example"]:
        (source / relative).write_text("fixture\n")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_REVISION", REVISION)
    monkeypatch.setenv("FAKE_VERSION", "v1.3.0")
    monkeypatch.setenv("FAKE_IMAGES", str(images))
    folder = tmp_path / "download"
    release.package(source, "v1.3.0", REVISION, "gpu", folder, PART)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    (deployment / ".env").write_text("JWT_SECRET=x\n")
    return folder, deployment, images


def install(folder, deployment, answer="yes\n", **env):
    return subprocess.run(["bash", str(folder / "install-offline.sh"), str(deployment)], input=answer,
                          capture_output=True, text=True, env={**os.environ, **env}, timeout=120)


def calls(folder):
    log = folder / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def test_one_command_runs_every_step_in_order(release_folder):
    folder, deployment, images = release_folder
    result = install(folder, deployment)
    assert result.returncode == 0, result.stdout + result.stderr
    d = str(deployment)
    assert calls(folder) == ["--assemble", d + " --check", d, d + " --status"]
    assert (folder / "images.tar").read_bytes() == images.read_bytes()
    assert "[6/6]" in result.stdout and "Ctrl+F5" in result.stdout and "AI Tracking" in result.stdout


def test_without_confirmation_nothing_is_stopped_or_upgraded(release_folder):
    folder, deployment, _ = release_folder
    result = install(folder, deployment, answer="no\n")
    assert result.returncode == 1 and "未确认" in result.stderr
    assert calls(folder) == ["--assemble", str(deployment) + " --check"]


@pytest.mark.parametrize("failing,expected", [
    ("--assemble ", ["--assemble"]),
    ("{d} --check", ["--assemble", "{d} --check"]),
    ("{d}", ["--assemble", "{d} --check", "{d}"]),
])
def test_any_failed_step_stops_the_rest(release_folder, failing, expected):
    folder, deployment, _ = release_folder
    d = str(deployment)
    result = install(folder, deployment, FAIL_AT=failing.format(d=d))
    assert result.returncode == 1 and "步失败" in result.stderr
    assert calls(folder) == [e.format(d=d) for e in expected]


def test_damaged_download_stops_before_unpacking(release_folder):
    folder, deployment, _ = release_folder
    part = folder / "images.tar.part02"
    part.write_bytes(b"\0" + part.read_bytes()[1:])
    result = install(folder, deployment)
    assert result.returncode == 1 and "images.tar.part02（损坏）" in result.stderr
    assert not (folder / "manifest.json").exists() and calls(folder) == []


@pytest.mark.parametrize("problem", ["no-env", "package-as-deployment", "two-tool-archives", "missing-argument"])
def test_wrong_folders_are_rejected_before_any_step(release_folder, problem):
    folder, deployment, _ = release_folder
    args = [str(deployment)]
    if problem == "no-env":
        (deployment / ".env").unlink()
    elif problem == "package-as-deployment":
        (folder / ".env").write_text("x")
        args = [str(folder)]
    elif problem == "two-tool-archives":
        tools = next(folder.glob("*-files.tar.gz"))
        shutil.copy2(tools, folder / ("other-" + tools.name))
        (folder / "SHA256SUMS").unlink()
    elif problem == "missing-argument":
        args = []
    result = subprocess.run(["bash", str(folder / "install-offline.sh"), *args], input="yes\n", capture_output=True, text=True)
    assert result.returncode in (1, 2), result.stdout
    assert calls(folder) == []


def test_manual_package_without_parts_skips_download_steps(release_folder):
    folder, deployment, images = release_folder
    tools = next(folder.glob("*-files.tar.gz"))
    subprocess.run(["tar", "-xzf", str(tools)], cwd=folder, check=True)
    subprocess.run(["bash", "update-offline.sh", "--assemble"], cwd=folder, check=True, capture_output=True)
    for path in [tools, folder / "SHA256SUMS", folder / "parts.json", folder / "calls.log", *folder.glob("images.tar.part*")]:
        path.unlink()
    result = install(folder, deployment)
    assert result.returncode == 0, result.stdout + result.stderr
    d = str(deployment)
    assert calls(folder) == [d + " --check", d, d + " --status"]
