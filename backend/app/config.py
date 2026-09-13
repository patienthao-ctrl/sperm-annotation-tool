from __future__ import annotations

import os
from pathlib import Path

# 读取 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

BACKEND_DIR = Path(__file__).resolve().parents[1]
TRACK_DATA_DIR = BACKEND_DIR / "track_data"
TRACK_MODUL_DIR = BACKEND_DIR / "track_modul"
DATA_DIR = BACKEND_DIR / "data"
DB_FILE = DATA_DIR / "app.db"

HOST = os.getenv("BACKEND_HOST", "127.0.0.1")
PORT = int(os.getenv("BACKEND_PORT", "3000"))
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
TOKEN_EXPIRES_IN_SEC = 7 * 24 * 60 * 60

DEFAULT_MODEL = TRACK_MODUL_DIR / "facebook--sam3" / "snapshots" / "master"
MODEL_ID = os.getenv("SAM3_MODEL_ID", str(DEFAULT_MODEL))
DEVICE = os.getenv("SAM3_DEVICE", "cuda")
DTYPE = os.getenv("SAM3_DTYPE", "bfloat16")

MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
# 每次点击 AI Tracking 最多处理的帧数。
# 修改这里即可全局调整，例如改成 10；前后端都会自动使用这个值。
TRACK_FRAMES = int(os.getenv("SAM3_TRACK_FRAMES", "20"))
if TRACK_FRAMES < 1:
    TRACK_FRAMES = 1

# Raw-frame mode: SAM3 uses the original video FPS and source frame numbers.
