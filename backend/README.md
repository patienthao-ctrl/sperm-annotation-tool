# Unified FastAPI Backend

现在整个后端只有 **一个 FastAPI 进程、一个端口 3000**。

```text
Vue + Vite
   │ /api/*
   ▼
FastAPI :3000
   ├─ 登录 / 注册 / JWT
   ├─ 人工标注 SQLite
   ├─ MP4 上传与视频播放
   └─ SAM3 Tracking
        └─ 本地 SAM3 + PyTorch + GPU
```

不再启动 Node，也不再启动第二个 uvicorn Worker。

## 启动

第一次安装：

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

确保本地 SAM3 位于：

```text
backend/track_modul/facebook--sam3/snapshots/master
```

模型目录不同可以设置：

```powershell
$env:SAM3_MODEL_ID="E:\add_login\backend\track_modul\facebook--sam3\snapshots\master"
```

GPU 默认：

```text
SAM3_DEVICE=cuda
SAM3_DTYPE=bfloat16
```

启动唯一后端：

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 3000
```

或者 Windows：

```text
backend/start-backend.bat
```

然后再启动前端：

```powershell
cd frontend
npm run dev
```

## Tracking

点击“AI Tracking”后：

1. FastAPI 创建任务；
2. 单线程 GPU 队列执行，避免 4GB 显存同时跑多个 SAM3；
3. SAM3 第一次 Tracking 时加载模型，后续复用同一个模型实例；
4. 每次从当前人工标注帧向后处理 `SAM3_TRACK_FRAMES` 帧（默认 5 帧）；
5. 结果合并到对应视频目录的 `tracker_results.json`（逐行 JSONL，结构与 01_test 的 `tracker_results.json` 一致）；
6. 前端继续按 `frameIndex` 显示对应 bbox。

Tracking 进行期间，前端人工标注工具会锁定，同时后端也会拒绝新的人工标注写入，避免 GPU 推理与人工修改产生竞争条件。登录、视频播放等非标注操作仍可用。

## Tracking 帧数配置
默认每次处理 5 帧，统一由后端配置：

```text
backend/app/config.py
TRACK_FRAMES = int(os.getenv("SAM3_TRACK_FRAMES", "5"))
```

也可以在 Windows PowerShell 启动前临时修改：

```powershell
$env:SAM3_TRACK_FRAMES="10"
python -m uvicorn app.main:app --host 127.0.0.1 --port 3000
```

前端点击 Tracking 时不再写死帧数，由后端返回本次任务实际使用的帧数。

## 同名视频
如果重复导入相同文件名，例如两次导入 `test.mp4`，后端不会覆盖之前的数据目录：

```text
track_data/test/
track_data/test_001/
track_data/test_002/
```

每个目录都有自己独立的 MP4、人工标注 JSON 和 `tracker_results.json`（逐行 JSONL，结构与 01_test 的 `tracker_results.json` 一致）。


## SAM3 Tracking Pipeline

The FastAPI Tracking endpoint reuses the SAM3 engine/session/propagation/decoding path used by the validated tracker test, but production tracking runs in **raw-frame mode**: no FPS down-sampling, no sampled-frame index, and no browser-side interpolation.

The original video FPS and frame numbers are used everywhere. `SAM3_TRACK_FRAMES` controls how many consecutive source frames one Tracking request processes (default `5`).

The SAM3 model/processor stay cached for the whole FastAPI process. Video preprocessing/storage are kept on CPU to make the setup practical for a 4 GB GPU, while model inference uses the configured CUDA device.

## Tracking 输出

每个视频目录会产生：

```text
tracker_results.json    # JSONL：每行一个原始视频 frame，frame_index == source_frame_index
tracker_overlay.mp4     # 与原视频保持相同 FPS/帧序的带框 MP4
```

浏览器继续通过 `/api/track/result/{mediaId}` 获取按原视频 `source_frame_index` 对齐的帧结果；也可以通过 `/api/track/result-file/{mediaId}` 获取原始 JSONL，通过 `/api/track/overlay/{mediaId}` 播放处理后 MP4。
