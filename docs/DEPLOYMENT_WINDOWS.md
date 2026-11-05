> 当前部署指引；业务规则见 [WORKFLOW.md](WORKFLOW.md)，统一文档入口见 [README.md](README.md)。

# Windows 本机开发部署说明

第三方直接使用请优先按 [Docker 部署说明](DOCKER_DEPLOYMENT.md) 运行 `deploy.ps1`；本页面向需要本机 Python/Node 环境的开发者。

本项目是前后端分离架构：Vue/Vite 前端通过 `/api` 调用 FastAPI 后端；SAM3 模型和视频、SQLite 数据都在后端机器上。因此，把项目复制到另一台电脑后，需要同时部署两个进程（开发模式）或将前端构建为静态文件并交给反向代理（生产模式）。

## 1. 目标机器准备

- Windows 10/11 x64；Python 与 Node 环境请对齐项目 Dockerfile（Python 3.12、Node 22）及锁定依赖。
- 若使用 AI Tracking：NVIDIA GPU、与 `torch==2.14.0` 安装包相匹配的显卡驱动/CUDA 运行环境，并有足够显存。没有 NVIDIA GPU 时可设 `SAM3_DEVICE=cpu`，但速度会显著下降。
- 复制或重新取得 SAM3 模型目录。模型不在 `requirements.txt` 中，默认路径为 `backend/track_modul/facebook--sam3/snapshots/master`，也可通过 `SAM3_MODEL_ID` 指定绝对路径。

不要把开发机的 `.venv` 直接复制到新电脑；在新电脑重新创建虚拟环境并安装依赖。需要保留账号、人工标注、已上传视频和追踪结果时，一并迁移 `backend/data/app.db` 与 `backend/storage/`；视频较大时可改为受控的备份/共享存储。旧部署的 `backend/track_data/` 也应一并迁移，随后按[存储目录说明](STORAGE_LAYOUT.md)整理。

## 2. 首次安装与配置

在项目根目录创建 `backend/.env`（不要提交到版本库），至少设置：

```dotenv
JWT_SECRET=请替换为足够长的随机字符串
SAM3_MODEL_ID=E:\\rare-sperm\\backend\\track_modul\\facebook--sam3\\snapshots\\master
SAM3_DEVICE=cuda
SAM3_DTYPE=bfloat16
BACKEND_HOST=0.0.0.0
BACKEND_PORT=3000
```

然后分别执行：

```powershell
cd E:\rare-sperm\backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

cd ..\frontend
npm ci
npm run build
```

安装 PyTorch 时要确认下载的是与目标 GPU/驱动相容的 CUDA wheel；如官方 PyTorch 指令与 `requirements.txt` 的固定版本冲突，以项目锁定版本和实际测试结果为准。

## 3. 本机开发与测试

后端保持单进程/单 worker。PowerShell 不便激活环境时，可直接运行 `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 3000`，无需更改机器执行策略。

第二个终端在 `frontend` 执行 `npm run dev`。默认端口 5173，代理到本机 3000。连接隔离后端时先设置 `$env:API_PROXY_TARGET="http://127.0.0.1:3301"` 再启动 Vite。

`SAM3_TRACK_FRAMES` 当前默认 120（含 seed），可在后端环境中覆盖；异常参数入口见 [代码地图](PROJECT_STRUCTURE.md)。当前交互和快捷键见 [ANNOTATION.md](ANNOTATION.md)，不以旧启动文档描述为准。

执行测试前设置独立数据路径。PowerShell 示例，在项目根目录：

```powershell
$env:APP_DATA_DIR="$PWD/work/windows-test-data"
$env:APP_DB_FILE="$PWD/work/windows-test-data/app.db"
$env:APP_STORAGE_DIR="$PWD/work/windows-test-storage"
$env:PYTHONPATH="backend"
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
npm run build --prefix frontend
```

测试夹具、浏览器脚本及顺序见 [TESTING.md](TESTING.md)。测试变量只用于这个终端，启动业务后端前使用新的终端或明确恢复配置。

## 4. 对局域网开放（推荐先这样验收）

开发前端需将代理目标改为实际后端地址或保持同机运行。更稳妥的做法是构建前端，然后由 Nginx/Caddy/IIS 将静态文件与 `/api` 一起代理。后端可先这样启动：

```powershell
cd E:\rare-sperm\backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000
```

在 Windows 防火墙仅对可信网段放行 TCP 3000（或后续反向代理的 80/443）。不要把带默认 `JWT_SECRET` 的开发服务直接暴露到互联网。

## 5. 生产前端与反向代理

`frontend/dist` 是可部署的静态站点。将其交给 Nginx/Caddy/IIS，并将 `/api/` 反向代理至 `http://127.0.0.1:3000`；浏览器仍然使用相对路径 `/api`，无需在前端代码中写服务器 IP。反向代理应同时支持大文件上传（项目允许的视频最大值是 2 GB）和长时间的 Tracking 请求/状态轮询。

示例 Nginx 核心配置：

```nginx
server {
    listen 80;
    server_name annotation.example.internal;
    client_max_body_size 8g;

    root E:/rare-sperm/frontend/dist;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

生产环境还应：使用 HTTPS；把 `JWT_SECRET` 放入机器秘密管理或受限 `.env`；让 Uvicorn、反向代理以 Windows 服务/任务计划启动并自动重启；定期备份 `app.db`、`storage` 和模型版本；仅允许受信任用户访问。由于 GPU tracking 被刻意串行化，一台 4 GB GPU 的机器同一时间只会执行一个 Tracking 任务，适合小团队共享而非高并发公网服务。

## 6. 上线检查

1. 在目标机访问前端，注册/登录、上传一个小视频。
2. 验证视频播放、人工框保存、首尾双向追踪、审查逐帧确认并“审查通过”，然后导出训练集；确认审查通过前不能导出。
3. 执行一次 AI Tracking，确认模型实际使用 GPU（后端日志及 `/api/track/sam3/health`）。
4. 从另一台局域网设备验证登录、API 和大文件上传。
5. 重启服务后确认数据仍在，并验证备份可恢复。
