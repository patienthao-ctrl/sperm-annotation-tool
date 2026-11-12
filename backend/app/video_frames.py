"""Bounded original-frame decoding and derived JPEG cache for a single worker."""
from collections import OrderedDict
from contextlib import contextmanager
import json
import logging
from pathlib import Path
from threading import RLock
import time
import uuid
import cv2

log = logging.getLogger('review.frames')


class FrameReadError(ValueError):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


def source_signature(path):
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ino]


def open_capture_at(path, index=0):
    """A failed/imprecise seek falls back to sequential grabs, never frame zero."""
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise FrameReadError('无法打开源视频')
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total and index >= total:
            raise FrameReadError(f'视频只有 {total} 帧，无法访问第 {index} 帧', 404)
        if index:
            positioned = cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            if not positioned or int(round(cap.get(cv2.CAP_PROP_POS_FRAMES))) != index:
                cap.release()
                cap = cv2.VideoCapture(str(path))
                if not cap.isOpened():
                    raise FrameReadError('无法打开源视频')
                for fi in range(index):
                    if not cap.grab():
                        raise FrameReadError(f'无法到达第 {index} 帧，解码停止于第 {fi} 帧', 404)
        return cap
    except Exception:
        cap.release()
        raise


class ExactFrames:
    def __init__(self, max_decoders=2, max_entries=128, max_bytes=128 * 1024 * 1024):
        self.max_decoders, self.max_entries, self.max_bytes = max_decoders, max_entries, max_bytes
        self.lock = RLock()
        self.decoders = OrderedDict()

    def close(self):
        with self.lock:
            for cap, _ in self.decoders.values():
                cap.release()
            self.decoders.clear()

    @contextmanager
    def removing(self, video):
        """Release one cached decoder and exclude frame reads while it is removed.

        Windows does not allow ``unlink`` while OpenCV still owns the source
        file.  Keep the decoder lock for the whole filesystem operation so a
        concurrent exact-frame request cannot reopen the same video between
        ``release`` and deletion.  If deletion fails, leaving the context makes
        the source readable again and the next request opens a fresh decoder.
        """
        key = str(Path(video).resolve())
        with self.lock:
            prior = self.decoders.pop(key, None)
            if prior is not None:
                prior[0].release()
            yield

    def _cache(self, video, signature):
        directory = video.parent / '.frame_cache'
        directory.mkdir(exist_ok=True)
        marker = directory / 'source.json'
        try:
            same = json.loads(marker.read_text()) == signature
        except (OSError, ValueError):
            same = False
        if not same:
            # Only derived frame JPEGs belong to this cache. Never touch inputs.
            for old in directory.glob('frame_*.jpg'):
                old.unlink(missing_ok=True)
            self._atomic(marker, json.dumps(signature).encode())
        return directory

    @staticmethod
    def _atomic(path, content):
        tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            tmp.write_bytes(content)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def _prune(self, directory):
        files = sorted(((p.stat().st_mtime_ns, p.stat().st_size, p) for p in directory.glob('frame_*.jpg')), key=lambda x:x[0])
        total, count = sum(x[1] for x in files), len(files)
        for _, size, path in files:
            if count <= self.max_entries and total <= self.max_bytes:
                break
            path.unlink(missing_ok=True)
            count -= 1
            total -= size

    def read(self, video, index):
        if index < 0:
            raise FrameReadError('frame_index 不能小于 0', 400)
        video = Path(video)
        started = time.perf_counter()
        try:
            # Decoder count is bounded even under concurrent browser prefetches.
            # This is independent of the annotation/publication lock.
            with self.lock:
                signature = source_signature(video)
                key = str(video.resolve())
                directory = self._cache(video, signature)
                cached = directory / f'frame_{index:08d}.jpg'
                if cached.is_file() and cached.stat().st_size:
                    data = cached.read_bytes()
                    cached.touch()
                    self._prune(directory)
                    return data
                prior = self.decoders.pop(key, None)
                if prior and prior[1] != signature:
                    prior[0].release()
                    prior = None
                cap = prior[0] if prior else None
                if cap is not None:
                    current = int(round(cap.get(cv2.CAP_PROP_POS_FRAMES)))
                    if index < current or index - current > 8:
                        cap.release()
                        cap = None
                    else:
                        for _ in range(index - current):
                            if not cap.grab():
                                cap.release()
                                cap = None
                                break
                if cap is None:
                    while len(self.decoders) >= self.max_decoders:
                        _, (old, _) = self.decoders.popitem(last=False)
                        old.release()
                    cap = open_capture_at(video, index)
                self.decoders[key] = (cap, signature)
                ok, frame = cap.read()
                if not ok or frame is None:
                    cap.release()
                    self.decoders.pop(key, None)
                    raise FrameReadError(f'无法解码第 {index} 帧', 404)
                width, height = int(cap.get(3)), int(cap.get(4))
                if frame.shape[:2] != (height, width):
                    raise FrameReadError(f'第 {index} 帧尺寸与源视频不一致')
                if source_signature(video) != signature:
                    raise FrameReadError('读取期间源视频发生变化，请重试', 409)
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if not ok:
                    raise FrameReadError(f'第 {index} 帧 JPEG 编码失败')
                data = encoded.tobytes()
                self._atomic(cached, data)
                self._prune(directory)
                elapsed = (time.perf_counter() - started) * 1000
                if elapsed >= 250:
                    log.info('frame.decode_slow media=%s frame=%s width=%s height=%s elapsed_ms=%.1f', video.parent.name, index, width, height, elapsed)
                return data
        except Exception:
            log.exception('frame.read_failed media=%s frame=%s elapsed_ms=%.1f', video.parent.name, index, (time.perf_counter() - started) * 1000)
            raise


    def read_enhanced(self, video, index):
        """The frame as the reviewers saw training crops: denoised minus a median background, gain 8."""
        import numpy as np
        video = Path(video)
        raw = self.read(video, index)
        with self.lock:
            directory = self._cache(video, source_signature(video))
            cached = directory / f'frame_{index:08d}_enh.jpg'
            if cached.is_file() and cached.stat().st_size:
                data = cached.read_bytes()
                cached.touch()
                return data
        gray = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise FrameReadError(f'第 {index} 帧无法增强')
        ok, encoded = cv2.imencode('.jpg', enhance_gray(gray), [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise FrameReadError(f'第 {index} 帧增强图编码失败')
        data = encoded.tobytes()
        with self.lock:
            self._atomic(cached, data)
            self._prune(directory)
        return data


def enhance_gray(gray):
    """Median-background enhancement used for the training review sheets (make_review.py).

    The sheets worked at 4096-wide scale: pre-blur 1, median 81, background blur 6, gain 8,
    and a 1.2 px denoise at native resolution. The background is low-frequency, so it is
    estimated on a 1024-wide copy (median 21, blur 1.5) and scaled back; a median rather
    than Gaussian background avoids white halos that would hide the tail root.
    """
    import numpy as np
    h, w = gray.shape[:2]
    k = max(w, h) / 4096
    f = gray.astype(np.float32)
    den = cv2.GaussianBlur(f, (0, 0), 1.2)
    pre = np.clip(cv2.GaussianBlur(f, (0, 0), max(.3, k)), 0, 255).astype(np.uint8)
    s = 1024 / max(w, h)
    small = cv2.resize(pre, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    size = max(3, int(round(81 * k * s)) | 1)
    bg = cv2.GaussianBlur(cv2.medianBlur(small, size).astype(np.float32), (0, 0), max(.5, 6 * k * s))
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(128 + 8 * (den - bg), 0, 255).astype(np.uint8)


frames = ExactFrames()
