# 代码地图与运行入口

## 当前入口

标注 → 审查 → 导出。审查是 `pages/AnnotatePage.vue` 的 `mode="review"`（`App.vue` 在 `/review` 渲染），右侧换成 `components/AnnotationReviewPanel.vue`，状态共享于 `stores/annotationReview.ts` → `api/annotationReviewApi.ts` → `backend/app/annotation_review_routes.py` / `annotation_review.py`。导出：`components/AnnotationDatasetExport.vue`（审查通过才可用）→ `annotation_export.py`（`require_passed` 门槛）→ `training_export` 队列。

Vue 3 + TypeScript + Vite；FastAPI + SQLite；SAM3 在后端按需加载。前端依赖见 [package.json](../frontend/package.json)，Python 依赖见 [requirements.txt](../backend/requirements.txt)，不在说明文档复制版本号。

## 按职责找文件

| 职责 | 实现入口 |
| --- | --- |
| Docker 部署入口 | 根目录 `deploy.sh` / `deploy.ps1`、`compose.yaml` / `compose.gpu.yaml`、`.env.docker.example` |
| 完整离线更新包与服务器升级 | 根目录 `build-offline.ps1` / `update-offline.sh`、`scripts/offline_update.py`（含 `--assemble` 分块合并）/ `offline_legacy.json`；版本标签自动发布 Releases 包：`.github/workflows/release-images.yml` 的 `offline-package` 与 `scripts/offline_release.py`；一键下载 `download-offline-release.ps1` / `.py`，服务器一键升级 `install-offline.sh`；制作、版本识别、备份、恢复与回滚见 Docker 部署说明 |
| Chrome 93 / HTTP 兼容 | `frontend/src/utils/browserCompat.ts`；`scripts/build_compat_update.py` / `install_compat_update.sh` 仅保留历史专项修复用途 |
| 容器启动前检查 | `backend/app/deployment_check.py`、`backend/docker-entrypoint.sh` |
| 模型随仓库交付 | `model-distribution/manifest.json` 与 `LICENSE-SAM.txt`、`scripts/prepare_model.py` / `build_model_bundle.py` |
| 训练包按视频合并 | `scripts/merge_training_exports.py`：多个已下载 ZIP / 目录按整段视频重新划分 train/val/test，换算与服务端 `training_export.video_bucket` 一致 |
| 检测器训练与评估 | `scripts/detector_train_eval.py`：基准视频固定为测试集，微调后与当前检测器比较，写 `model-record.json`；不替换仓库权重 |
| 仓库与容器验收 | `scripts/check_repository.py`、`scripts/docker_smoke.py`、`.github/workflows/` |
| 固定浏览器回归与执行记录 | `scripts/run_browser_regression.py`；`frontend/tests/critical-workflows-browser.mjs` 从真实登录/上传走完整用户流程；测试专用模拟模型只在 `backend/tests/browser_server.py` |
| 输入完整性、读取竞态与登录恢复 | `annotation/trackingResult.ts`、`stores/workspace/restore.ts` / `trackingResult.ts`、`stores/auth.ts`；`test_independent_integrity_audit.py`、`test_integrity_recovery.py` 及固定 `independent-integrity-browser.mjs` |
| 路由/认证/布局/主题 | `frontend/src/router/index.ts`、`stores/auth.ts`、`layouts/AppLayout.vue`、`stores/appearance.ts`、`style.css` |
| 观看快捷键/平移 | `workbench/viewTransform.ts`；观看不写业务 |
| 共享工作台 | `components/WorkbenchShell.vue`、`workbench/shell.css`；站点顶栏上下文、视频抽屉、画布与可收起检查面板；帧条 `components/WorkbenchFrameStrip.vue`（审查模式显示待查/已查帧），布局约束见 WORKFLOW |
| 标注工作区 store | `stores/workspace.ts` 只组装 `useWorkspace()` 的对外接口；实现按职责在 `stores/workspace/`：`state.ts`（共享状态与派生值，含跨模块计数器 `runtime`）、`history.ts`（撤销/重做）、`editing.ts`（画布手势、点选头部、对象增删改、采用审查轨迹）、`frames.ts`（精确帧读取/缓存、定位与播放）、`save.ts`（本机草稿、构建工作区、写入队列与重试）、`collaboration.ts`（观看状态、三方合并应用、冲突解决、stamp 轮询）、`trackingActions.ts`（较早帧重追、整视频删除、异常确认反馈）、`media.ts`（素材列表/导入/删除）、`restore.ts`（切换素材/账号时恢复）、`trackingResult.ts`（追踪结果加载与暂停说明）、`tracking.ts`（启动/轮询/续追 SAM3）、`results.ts`（JSON 导出与效果预览）；合并/重放协议在 `annotation/workspaceWrites.ts` |
| 人工标注 UI / 状态 | `pages/AnnotatePage.vue`、`stores/workspace/editing.ts`、`annotation/annotation.css` / `annotation-shell.css`；缩放小地图 `components/AnnotationMinimap.vue` |
| 框绘制/坐标/缓存 | `components/AnnotationOverlay.vue`、`annotation/geometry.ts`、`annotation/frameCache.ts` |
| 审查 | `pages/AnnotatePage.vue`（`mode="review"`）、`components/AnnotationReviewPanel.vue`（逐帧确认）、`stores/annotationReview.ts`、`api/annotationReviewApi.ts`；后端 `annotation_review.py`（帧清单、按帧确认、通过与修改记录、导出门槛）与 `annotation_review_routes.py`；共享请求与日志 `api_common.py` |
| 素材文件夹与批量导入 | `components/MediaLibrary.vue`（抽屉、树、筛选、确认窗）、`components/FolderOrganizer.vue`、`stores/importQueue.ts`；后端 `backend/app/media_library.py` |
| 批量选择导出 | `components/AnnotationDatasetExport.vue`（选视频、固定快照、任务列表）→ `components/TrainingDatasetExport.vue`（设置） |
| 重复视频与覆盖重新标注 | `stores/workspace/media.ts`、`backend/app/media_reimport.py`、`main.py`；文件恢复日志与轮次保护见 STORAGE_LAYOUT |
| 导入标注包 | `backend/app/package_import.py`（训练 ZIP → 新素材 + 人工工作区）、`main.py` 的 `/api/track/import-package`；前端 `stores/workspace/media.ts` 的 `importPackage` 与素材库“标注包”按钮 |
| 追踪证据 | `backend/app/review_evidence.py`（身份风险/对象缺失）/ `endpoint_tracking.py` 写入Tracking行，供追踪自身使用，审查不读取；旧 `concentrated_review` 仅保留历史数据层 |
| 人工首尾与独立反追 | `backend/app/endpoint_tracking.py`：端点校验、反向有界窗口、多帧身份匹配与只读证据；`main.py` 编排、`annotation_completion.py` 有效尾框合并 |
| 三路综合标注 | `backend/app/joint_tracking.py`：已有身份内关联、连续路径选择、统一几何与不确定片段；`head_click.detect_regions` 批量ROI推理；新首尾请求 combineResults=true |
| 历史 A/B/C/F 数据（只读） | `review_workflow.py`、`review_repository.py`、`confirmation_workflow.py`、`concentrated_review.py`、`review_schema.py` 仅保留表结构迁移与数据层，供统计/审计导出读取；页面与路由已删除，说明见 `docs/archive/旧审查与对比确认流程_2026-11-01前.md` |
| 帧条 | 标注模式展示人工/AI/无框及追踪提示；审查模式展示清单待查/已查帧；浏览不改变审查状态 |
| 用户使用说明 | `components/UserGuide.vue`；`help/user-guide.md` 为页面阅读和下载的唯一内容源，`AppLayout.vue` 提供入口 |
| 训练集导出 | `components/TrainingDatasetExport.vue`、`api/trainingExportApi.ts`、`backend/app/training_export.py` 与 `_routes.py` |
| 自动质量审计与只读提取 | `backend/app/quality_audit.py` / `audit_export.py`、根 `export-audit.sh`；统计仓库单独检出到 `标注统计/`，不纳入主仓库或镜像 |
| 全量流程统计 ZIP | `components/StatisticsDataExport.vue`、`api/statisticsExportApi.ts`、`backend/app/statistics_export.py`、`statistics_export_jobs.py` 与 `statistics_export_routes.py`；入口在标注记录页，兼容外部 export_statistics_data.py 的格式，生产代码不放 tests |
| 记录查询 | `pages/ResultsPage.vue`、`backend/app/db.py` |
| 媒体/工作区/Tracking API | `api/trackApi.ts`、`api/httpAnnotationApi.ts`、`backend/app/main.py` |
| Tracking 编排 / 模型 / 异常 | `backend/app/tracker.py`、`services/sam3_engine.py`、`services/anomaly_detector.py`、`services/annotation_seed.py` |
| 异常后较早帧重建 | `pages/AnnotatePage.vue` 的确认窗口、`components/AnnotationTrackingFeedback.vue`、`stores/workspace/trackingActions.ts` / `api/trackApi.ts`、`backend/app/tracking_restart.py`；持久暂停在 `annotation_state.py`，版本/SQL 收据/跨文件恢复见 API 与 STORAGE_LAYOUT |
| 按需带框视频预览 | `backend/app/tracking_preview.py`；长时编码在来源锁外，输入签名校验后发布 |
| 点选头部生成框 | `backend/app/head_click.py` 与 `main.py` 的 head-box 接口；前端 `stores/workspace/editing.ts` 的 `createHeadBox`、`pages/AnnotatePage.vue` |
| 可选边缘入画新增 | `backend/app/edge_entry.py`、`head_click.detect_edges`、`tracker.track_video`；现有 AI Tracking 开关与请求正文 |
| 精确帧性能与缓存 | `backend/app/video_frames.py` 复用/限量解码器、原子 JPEG 与源签名；`frontend/src/api/sourceFrameApi.ts` 限量 Blob 缓存 |
| 数据库和增量迁移 | `db.py`、`review_schema.py`、各 workflow/export/review 模块中的 `migrate()`（`annotation_review.migrate` 随历史 schema 一起执行） |
| 日志/来源锁 | `review_logging.py`、`review_source_lock.py` |

表内未带根路径的前端项均相对 `frontend/src/`，后端项相对 `backend/app/`。具体路由与结构见 [API.md](API.md)。

### 兼容代码不等于当前流程

`review_repository.py` 等保留历史数据层；活动审查只走 `annotation_review_routes.py`，路由注册在 `review_routes.py`。`mockAnnotationApi.ts`、旧 annotation 接口注释、EffectsPage 等兼容代码不能作为“当前登录是 Mock”或“Tracking 只从第 0 帧开始”的依据。当前登录使用真实后端 JWT；实际请求以活动页面调用和 `main.py` 注册的路由为准。

## 大文件定位

下列文件超过约 800 行，先 `grep -n` 定位函数或关键字再读附近片段，不要整份读取（行数随开发变化，仅作量级参考）。

| 文件 | 约行数 | 定位建议 |
| --- | --- | --- |
| `frontend/src/stores/workspace/*.ts` | 每个 60–430 | 原 3000 行的 `workspace.ts` 已按职责拆分（见上表“标注工作区 store”），先按职责选模块；不知道函数在哪个模块时 `grep -n "export const 函数名" frontend/src/stores/workspace/*.ts` |
| `frontend/src/pages/AnnotatePage.vue` | 1000（单行很长，约 100 KB） | 按模板中的类名、`mode === 'review'` 或组件名搜索 |
| `backend/app/main.py` | 1500 | 路由集中处；按路径搜索，如 `grep -n '"/api/track/' ` |
| `backend/app/services/anomaly_detector.py` | 1500 | 仅旧暂停兼容路径的异常判定；按原因码搜索 |
| `backend/app/tracker.py` | 1200 | SAM3 追踪编排；按 `def ` 列出函数后再读 |
| `backend/app/annotation_review.py` | 380 | 帧清单、按帧确认与通过规则；按 `upgrade`、`check`、`pass_review` 搜索 |
| `backend/app/annotation_state.py`、`services/tracking_evidence.py`、`training_export.py` | 800–900 | 按 `def ` 列出函数后再读 |
| `backend/app/review_repository.py`、`review_workflow.py`、`confirmation_workflow.py` | 800–1200 | 历史 A/B/C/F 只读数据层；当前功能一般不需要读 |

## 本地运行

第三方部署先使用根 README 的 Docker 入口。以下为开发模式；后端使用 Python 3.12（锁定 NumPy 需要 3.12+），前端 Node 22。项目根目录，已安装后端依赖的环境：

```bash
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 3000
```

另一终端：

```bash
npm ci --prefix frontend
npm run dev --prefix frontend
```

Vite 默认 5173，`/api` 代理到 `http://localhost:3000`。隔离测试使用其他端口：

```bash
API_PROXY_TARGET=http://127.0.0.1:3301 npm run dev --prefix frontend -- --host 127.0.0.1 --port 5373 --strictPort
npm run build --prefix frontend
```

构建含 `vue-tsc --noEmit`；部署 `frontend/dist` 并将 `/api` 代理到 FastAPI。不要把开发测试服务指向业务数据，隔离步骤见 [TESTING.md](TESTING.md)。Windows 命令见 [DEPLOYMENT_WINDOWS.md](DEPLOYMENT_WINDOWS.md)。

## 配置与进程约束

- 后端配置集中在 `backend/app/config.py`，读取 `backend/.env`；模板 `backend/.env.example`。
- `APP_DATA_DIR`：数据库默认目录及日志；`APP_DB_FILE`：数据库文件；`APP_STORAGE_DIR`：媒体和训练 ZIP。覆盖其中一个不自动迁移其他路径。
- 自训练检测器随普通 Git 提供：`backend/app/model_assets/head-detector.pt` + 清单 / 许可；`HEAD_DETECTOR_MODEL` 仅用于显式覆盖。正常安装 `backend/requirements.txt` 已包含 Ultralytics；默认文件初次加载校验 SHA256。
- `SAM3_MODEL_ID`：默认 `backend/track_modul/facebook--sam3/snapshots/master`；模型随仓库 Release 提供，默认部署先用 `scripts/prepare_model.py` 自动准备；Git 仅保存 `model-distribution/` 中的清单和许可。
- `SAM3_DEVICE` / `SAM3_DTYPE` 默认 `cuda` / `bfloat16`；真实 GPU 可用性需单独验收。
- `SAM3_TRACK_FRAMES` 当前代码默认 **120**，包含 seed 帧；环境变量可覆盖。异常参数见 `services/anomaly_detector.py`，不要在 UI 写死单轮帧数。
- `JWT_SECRET` 生产环境应配置；不把密钥/token 写入文档或日志。
- GPU 任务、来源锁及训练导出队列使用进程内状态，保持 **单后端进程、单 Uvicorn worker**。扩容前需要单独设计，不能直接增加 workers。
- Docker 首次通过部署脚本或配置模板默认启用 GPU + AI Tracking（`compose.yaml` + `compose.gpu.yaml`）；显式 `cpu` 仅人工模式使用基础 Compose，不启用 AI。已有 `.env` 保留原模式。两种模式均支持完整人工标注→审查→确认→导出。`SAM3_ENABLED` 的本机开发默认值仍为 true，Compose 明确覆盖。
- Docker 设置 `APP_DATA_DIR=/data/database`，日志与 DB 同卷持久化；启动前校验密钥、目录和 GPU 模型条件。部署细节见 [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)。
- 医院升级使用固定 Git 提交构建的完整离线包。升级器沿用现有运行配置和绝对数据路径；成功后 `.env` 指向 `.offline/current-compose.json`。部署状态、日志、备份和中断恢复记录都在原部署目录的 `.offline/`，不能作为代码缓存清理。`offline_legacy.json` 记录已核对旧后端的全体 Python 源码指纹；`contract` 是人工维护的存储与运行兼容契约，不是现有 SQLite schema 版本号。改变不兼容的数据格式、运行配置或模型要求时必须同时设计迁移并更新契约，不能只递增应用版本。
