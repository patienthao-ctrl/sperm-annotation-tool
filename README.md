# 微流控标注工作台

自训练头部检测权重已随源码提供（约 19.4 MiB，普通 Git 文件）。开发者正常 `git clone` / `git pull --ff-only` 即可获得；执行 `python -m pip install -r backend/requirements.txt` 安装依赖，重启后端即可使用默认检测器，无需另行下载或配置 `HEAD_DETECTOR_MODEL`。模型位于 `backend/app/model_assets/head-detector.pt`，保留自定义路径覆盖。模型来源、校验值和许可见[模型说明](backend/app/model_assets/README.md)。SAM3 大模型仍使用原有 Release 准备流程。

当前流程：首尾帧标头框 → 首尾双向追踪（默认识别边缘入画）→ 检查微调 → **审查**（按系统生成的清单逐项检查，直接在画面上纠错）→ 审查通过后导出 YOLO / COCO 数据集（默认按视频划分训练 / 验证 / 测试集，可附原视频）。

用于视频人工标注、SAM3 辅助追踪、审查纠错及 YOLO / COCO 训练数据集导出。浏览器即可操作；数据保存在部署方自己的服务器。

**只有审查通过、且之后没有再修改的视频才能导出训练集。** 同一账号可以既标注又审查，也可由团队分工完成。升级前的旧审查、对比确认记录作为历史数据保留，用于统计与审计导出，界面不再提供入口。

## 快速开始：默认启用 AI 的 Docker 部署

默认部署启用 SAM3 AI Tracking，面向半自动视频标注。服务器先准备 NVIDIA GPU、驱动、容器 GPU 支持、Docker + Compose（2.20+），模型由本仓库 Release 提供，部署脚本会自动下载并校验（见[部署说明](docs/DOCKER_DEPLOYMENT.md)）。使用者的电脑只需要浏览器，无需 Python、Node.js 或模型环境。

```bash
git clone https://github.com/huangwww11222/sperm-annotation.git
cd sperm-annotation
# 首次自动获取本仓库提供的 SAM3 模型并启用 AI
bash deploy.sh
```

Windows PowerShell：

```powershell
git clone https://github.com/huangwww11222/sperm-annotation.git
cd sperm-annotation
# 首次自动获取本仓库提供的 SAM3 模型并启用 AI
.\deploy.ps1
```

成功后访问 **http://服务器IP:8080**，在登录页注册账号。脚本自动生成 `.env` 和随机登录密钥，构建镜像并等待两个服务健康；再次执行不会覆盖已有配置和数据。首次构建需要访问镜像、Python 和 npm 下载源，不能离线凭空安装依赖。

| 模式 | 功能 | 额外条件 |
| --- | --- | --- |
| `cpu` | 人工标注 → 检查 → 直接导出 | 不需要 GPU 或模型；AI Tracking 显示未启用提示 |
| `gpu`（默认） | 上述功能 + SAM3 AI Tracking | NVIDIA GPU、驱动、Container Toolkit / WSL2 GPU 支持；脚本自动准备模型 |

首次不传模式即选择 GPU + AI，也可显式运行 `bash deploy.sh gpu`（Windows：`.\deploy.ps1 -Mode gpu`）。仅需人工标注时才显式选择 `bash deploy.sh cpu` / `.\deploy.ps1 -Mode cpu`。缺少 GPU 或模型时会报错，不自动降级。已有 `.env` 的模式切换及驱动要求见 [Docker 部署说明](docs/DOCKER_DEPLOYMENT.md)。SAM3 模型作为[本仓库 Release 附件](https://github.com/huangwww11222/sperm-annotation/releases/tag/sam3-model-6d06f0a5)随项目提供；首次部署自动下载至 `models/sam3/`、校验 SHA256 后启用，无需另行申请模型下载权限。约 3.45 GB 下载量，下载与组装需约 6.5 GiB 可用空间。重复部署复用已验证模型，失败保留下载缓存供重试。模型附带原始 SAM License；源码 Git 和应用镜像不重复存储大权重。

默认数据保存在 `runtime/database/` 与 `runtime/storage/`，日志在 `runtime/database/logs/`。`.env` 和这些目录需要保留、备份，不能提交到 Git 或随代码清理。

本工具面向**可信团队共享使用**，注册账号不等于素材隔离。默认不提供租户隔离、管理员审批注册或实时协同编辑；多人避免同时修改同一视频。公网部署应由 HTTPS 反向代理和组织访问控制保护，不直接将共享标注环境开放给所有人。

## 医院内网：持续交付离线更新

已安装的医院服务器通过完整离线更新包升级。推送 `vX.Y.Z` 版本标签后，GitHub Actions 自动制作 GPU 更新包并发布到仓库 [Releases](https://github.com/huangwww11222/sperm-annotation/releases)；在联网电脑运行 `.\download-offline-release.ps1 -Version vX.Y.Z`（macOS/Linux 用 `python3 download-offline-release.py`）下载全部附件并核对 SHA256，把文件夹传入医院服务器新目录后执行 `bash install-offline.sh /原部署目录`，脚本依次完成核对、合并、检查、确认停机、升级和状态显示。也可在联网 Windows 电脑（Git、Docker Desktop Linux 容器模式）于干净的仓库目录手工制作：

```powershell
git pull --ff-only
.\build-offline.ps1 -Ref HEAD
```

默认构建 Linux amd64 的 GPU 后端和前端，生成 `output/sperm-annotation-git-<提交号前12位>-gpu-linux-amd64/`。版本固定到本次 Git 提交，包内含完整镜像、校验清单和升级工具，不带业务数据、密码或模型。也可用 `-Ref v1.2.0 -Version v1.2.0` 制作已存在的发布标签，显式 `-Mode cpu` 才制作仅人工模式的包。

手工包将**整个输出目录**传入医院内网。保留原部署目录。在服务器先检查，再于无人使用时更新：

```bash
bash /新包目录/update-offline.sh /原部署目录 --check
bash /新包目录/update-offline.sh /原部署目录
```

工具识别旧版本、检查配置与任务、停写备份、替换镜像并验收启动，保留原有账号、视频、标注、登录密钥和模型路径。未知旧版本或配置不一致会停止并给出原因；首次升级以实际检查结果为准。日志、备份、失败恢复和回滚见 [离线升级流程](docs/DOCKER_DEPLOYMENT.md#医院内网可复用的离线升级流程)。此入口用于升级已有服务；首次安装仍按上面的部署说明。推送 GitHub 只更新源码，医院需接收并安装对应更新包。

## 使用与维护

- [用户使用说明](frontend/src/help/user-guide.md)：操作步骤、快捷键、保存续做、常见问题；前端顶部也可阅读、搜索和下载。
- [Docker 部署说明](docs/DOCKER_DEPLOYMENT.md)：模型配置、预构建镜像、日志、升级、备份与恢复。
- 内网客户端支持 Chrome 93，上传抽帧和审查流程已适配 HTTP。仅替换部署脚本不会更新旧镜像中的应用代码，医院升级使用上述完整离线包。
- 查看状态：`docker compose ps`；排查：`docker compose logs --tail=100 backend frontend`。
- 联网源码部署更新前先备份数据；更新代码后执行 `bash deploy.sh`，Windows 执行 `.\deploy.ps1`。已经由离线升级工具接管的部署继续使用 `update-offline.sh`。
- 仅运行 **一个 backend 实例、一个 worker**，SQLite 放在服务器本地磁盘。

## 仓库组织与开发

```text
README.md / deploy.sh / deploy.ps1     第三方安装入口
build-offline.ps1 / update-offline.sh  Windows 制作完整更新包、Linux 服务器离线升级
compose.yaml / compose.gpu.yaml        基础服务与默认启用的 GPU 覆盖配置
.env.docker.example                   可提交的配置模板
backend/                              API、数据库、追踪、测试、Dockerfile
frontend/                             界面、用户手册、测试、Dockerfile、Nginx
scripts/                              离线升级与旧版本识别、模型安装、仓库检查和容器验收
model-distribution/                   固定模型清单、来源与 SAM 许可
.github/workflows/                    PR 验证与版本镜像发布
AGENTS.md / docs/                     AI 开发入口和当前技术文档
docs/archive/                         历史资料，仅供追溯
runtime/ / models/ / work/ / output/   本机数据、模型与测试产物，不纳入 Git
```

开发者先读 [AGENTS.md](AGENTS.md) 和 [当前文档索引](docs/README.md)。Python 基线为 **3.12**，Node 为 **22**；测试方法见 [TESTING.md](docs/TESTING.md)。合并前运行 `python3 scripts/check_repository.py`，避免重复定义、冲突标记及业务数据再次进入仓库。

维护者推送 `vX.Y.Z` 标签后，镜像发布工作流会先验证再构建 Linux amd64 的 CPU/GPU 后端和前端镜像。**工作流文件不代表镜像已经发布**；以 GitHub Actions / Packages 的实际结果为准。首次发布后确认 Packages 可见性，第三方才可免构建拉取；具体流程见部署文档。发布前还需由代码所有者明确项目 LICENSE；本次不擅自选择授权协议，随项目提供的 SAM3 模型遵循其独立 SAM License。

生成训练数据集时自动留存版本、人员与审查/确认历史的审计关联，医院人员无需增加操作。运维批量提取命令及图片追溯见 [Docker 部署说明](docs/DOCKER_DEPLOYMENT.md#服务器批量提取质量审计)，独立分析工具见 [statistics_system](https://github.com/huangwww11222/statistics_system)。
