# Rare Sperm Annotation Frontend

Vue 3 + Vite 前端。

## 启动

```powershell
npm install
npm run dev
```

前端统一把 `/api/*` 代理到：

```text
http://localhost:3000
```

后端现在只有一个 FastAPI 进程，负责登录、标注、视频和本地 SAM3 Tracking。

## 视频 Tracking

```text
当前未标注帧人工 bbox
      ↓
生成 JSON
      ↓
AI Tracking
      ↓
SAM3 当前帧起连续最多 5 帧
      ↓
自动定位下一个未标注帧
```

Tracking 期间页面仍可进行人工标注；GPU Tracking 在后端单线程队列中执行。

# FastAPI + Vue + 本地 SAM3 标注项目

当前版本采用单后端架构：

```text
Vue 3 + Vite
      │
      │ /api/*
      ▼
FastAPI :3000
 ├─ 登录 / 注册 / JWT
 ├─ SQLite 人工标注
 ├─ MP4 上传 / 播放
 └─ SAM3 Tracking
      └─ 本地 SAM3 + PyTorch + CUDA
```

只启动一个 FastAPI 后端即可，不再启动 Node，也不再启动第二个 Tracking 服务。

## 视频工作流

1. 导入 MP4。
2. 后端按照视频文件名创建独立目录；如果同名视频重复导入，则自动创建 `name_001`、`name_002` 等新目录，不覆盖旧数据。
3. 页面支持输入帧号并直接跳转，跳转后当前帧已有 Tracking bbox 时会立即显示。
4. 在当前帧人工框选目标并生成该帧 JSON。
5. 点击 AI Tracking，SAM3 从当前帧开始处理配置的帧数。
6. Tracking 运行期间人工标注被锁定，后端也拒绝新的人工标注写入。
7. Tracking 完成后结果合并到当前视频目录的 `tracking_result.json`，前端自动定位到下一个未标注帧。

## 每次 Tracking 的帧数

统一由后端 `backend/app/config.py` 控制：

```python
TRACK_FRAMES = int(os.getenv("SAM3_TRACK_FRAMES", "5"))
```

默认 5 帧。也可以启动前临时设置：

```powershell
$env:SAM3_TRACK_FRAMES="10"
python -m uvicorn app.main:app --host 127.0.0.1 --port 3000
```

前端不会再写死 5；实际任务帧数由后端返回。


### Tracking implementation note
The SAM3 path is aligned with the validated `01_test` reference: default processing FPS is 15, source-frame/sample-frame mapping is preserved, and the same SAM3 session / propagation / mask decoding flow is used. The FastAPI process keeps the SAM3 model cached; each click creates a new video inference session.

