"""Run independent browser probes without consuming shared fixture files."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def video(path, width, height, count):
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25, (width, height))
    assert out.isOpened()
    for fi in range(count):
        frame = np.full((height, width, 3), 25 + fi % 80, dtype=np.uint8)
        cv2.circle(frame, (width//3, height//3), 12, (230, 230, 230), -1)
        out.write(frame)
    out.release()


def references(video_path, output):
    # Decode the encoded input independently; never compare to pre-encode pixels.
    capture = cv2.VideoCapture(str(video_path))
    values = {}
    try:
        for fi in range(60):
            ok, image = capture.read()
            assert ok and image is not None
            if fi in (0, 29, 59):
                ok, jpeg = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 95])
                assert ok
                values[str(fi)] = hashlib.sha256(jpeg.tobytes()).hexdigest()
    finally:
        capture.release()
    output.write_text(json.dumps(values))


def main():
    os.chdir(ROOT)
    base = ROOT / "work" / ("e2e-confirm-ux-independent-" + uuid.uuid4().hex[:10])
    base.mkdir(parents=True)
    video(base/"small.avi", 640, 480, 3)
    video(base/"4k.avi", 3840, 2160, 60)
    references(base/"4k.avi", base/"reference-frames.json")
    api, web = port(), port()
    while api == web:
        web = port()
    env = dict(os.environ, APP_DATA_DIR=str(base/"data"), APP_DB_FILE=str(base/"data/app.db"),
               APP_STORAGE_DIR=str(base/"storage"), PYTHONPATH=str(ROOT/"backend"),
               SAM3_ENABLED="true", BROWSER_SIMULATED_MODEL="1", SAM3_DEVICE="cpu", HEAD_DETECTOR_DEVICE="cpu",
               API_PROXY_TARGET=f"http://127.0.0.1:{api}", INDEPENDENT_AUDIT_ROOT=str(base),
               INDEPENDENT_AUDIT_ORIGIN=f"http://127.0.0.1:{web}")
    subprocess.run([sys.executable,'backend/tests/independent_browser_fixture.py'], env=env, check=True)
    processes, handles = [], []
    report = {"directory": str(base), "model": "simulated; real decoding and API; no GPU inference",
              "status": "running", "python": sys.version, "video": {"width":3840,"height":2160,"frames":60}}
    print("Independent browser evidence: " + str(base), flush=True)
    try:
        for name, command in [("backend", [sys.executable,"backend/tests/browser_server.py","--port",str(api)]),
                              ("frontend", ["npm","run","dev","--prefix","frontend","--","--host","127.0.0.1","--port",str(web),"--strictPort"])]:
            handle = (base/(name+".log")).open("w"); handles.append(handle)
            processes.append(subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True))
        for process, url in zip(processes, [f"http://127.0.0.1:{api}/api/health", f"http://127.0.0.1:{web}"]):
            for _ in range(150):
                if process.poll() is not None:
                    raise RuntimeError("Isolated server stopped before readiness")
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, TimeoutError):
                    time.sleep(.2)
            else:
                raise RuntimeError("Isolated server readiness timed out")
        started = time.monotonic()
        with (base/"browser.log").open("w") as log:
            result = subprocess.run(["node","frontend/tests/independent-integrity-browser.mjs"], env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=600)
        report.update(status="passed" if result.returncode==0 else "failed", exit=result.returncode,
                      seconds=round(time.monotonic()-started,2))
        print((base/"browser.log").read_text()[-4500:], flush=True)
        return result.returncode
    finally:
        (base/"run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
