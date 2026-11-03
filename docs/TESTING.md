# 测试与日志排查

> 要点：开发中只跑相关专项；提交前跑全部单元/构建/仓库检查，涉及保存、认证、公共 API、缓存或依赖时再跑一次完整七组浏览器回归；测试必须使用隔离的 `APP_DATA_DIR` / `APP_DB_FILE` / `APP_STORAGE_DIR`。细节按需读 `details/testing-*.md`。

## 测试分层

| 时机 | 必须运行 | 不能代替 |
| --- | --- | --- |
| 开发中每次修改 | “按修改选择测试”表中的专项 pytest / 前端单元测试；涉及页面时 `run_browser_regression.py --only <相关套件>` | 只是定位和快速反馈 |
| 提交前（每批改动一次） | `backend/tests` 全部 pytest、`test:workbench` / `test:compatibility` / `test:annotation`、`npm run build`、`python3 scripts/check_repository.py`；改动涉及工作区加载、认证、保存/重试协议、公共 API、缓存或依赖/构建时，再跑一次不带筛选参数的完整七组浏览器回归 | 分批或筛选运行的结果 |
| 推送 / CI | CI 重跑完整门禁 | 本地通过记录 |

纯样式、文案或使用说明的小改动，提交前运行相关专项、构建，并对受影响页面做 `--only workbench-consistency`；不要求完整七组。汇报时分别列出实际通过、失败、未执行和模拟的范围。

## 审查与导出专项

各专项的覆盖说明见 [details/testing-specialty.md](details/testing-specialty.md#审查与导出)。

## 固定回归入口与发布门禁

提交前，若本批改动涉及工作区加载、认证、持久化/重试、公共 API、缓存、依赖或构建，在专项测试之外运行一次下列固定入口（开发中只用 `--only` 定位，见“测试分层”）。它从新的隔离目录启动自己的服务，顺序生成各套夹具，执行全部七组浏览器回归，结束后只关闭自己启动的进程。不能手工挑掉失败的套件后宣称完整回归通过。

```bash
npm ci --prefix frontend
npx --prefix frontend playwright install chromium
python3 scripts/run_browser_regression.py --python work/review-venv/bin/python --channel chromium
```

也可用本机 Chrome：`--channel chrome`。CI 使用已构建的 Python 3.12 后端镜像：`python3 scripts/run_browser_regression.py --backend-image annotation-ci-backend:cpu --channel chromium`。镜像模式仍使用独立 `work/e2e-confirm-ux-regression-*` 数据和单后端进程。`--critical-only` 或可重复的 `--only <套件名>` 仅用于定位问题，不能同时使用，也不能用于完整验收。例如 `--only workbench-consistency --only independent-integrity` 只运行这两组；最终验收须去掉筛选参数重新运行全部七组。

`run.json` 的 `requiredSuites` 始终列出七组固定范围，`selectedSuites` 记录本次实际选择，`scope` 区分完整与诊断运行。子集可记录本次 `status: passed`，但 `fullRegressionPassed` 仍为 `false`；只有未使用筛选参数的完整运行全部通过时才为 `true`。不能拼接分批通过记录代替同一批源码的一次完整回归。运行器在占用共享夹具锁前校验参数，初始化失败也清理自己取得的锁；其他进程持有的锁不得删除。

固定套件：`critical-workflows`、`workflow-transitions`、`annotation-ux`、`tracking-recovery`、`training-export`、`workbench-consistency`、`independent-integrity`。每组前按依赖重建夹具；不得并行消费或重置共享夹具。执行记录、版本/未提交文件清单、各组退出状态、耗时与日志保存在该次隔离目录；失败会非零退出并记录尚未执行的范围，不能把旧报告当成本次结果。CI 保留服务/套件日志及截图子目录；夹具 JSON 可能包含 token，禁止上传。

容器模式按宿主 UID/GID 生成夹具，避免 Linux CI 中 root 文件阻止后续翻译或清理。`tracking-recovery` 的模拟结果与暂停夹具通过 `TEST_PYTHON` 在后端相同运行环境原子发布；不要从宿主机替换 Docker 共享目录文件后假定容器立即可读，也不要用固定等待放宽故障断言。刷新断言须等待目标原帧、图像及结果加载完成，单看初始 idle 不等于恢复完成。运行记录注明后端模式、解释器/镜像及浏览器；结束时将实际后端 `data/logs/review.log*` 脱敏复制到回归目录的 `review*.log`，与现有 CI 日志一并保留。

各套件具体覆盖的流程与断言见 [details/testing-browser-suites.md](details/testing-browser-suites.md)。

完整回归中的模型输出由仅存在于 `backend/tests/browser_server.py` 的测试引擎模拟；视频解码、追踪编排、任务状态、结果发布与其余 API/数据库均走真实代码。测试入口拒绝业务数据路径，生产服务不导入它。CPU 镜像用例验证“AI 未启用后仍可人工完成流程”。两者都不代表真实 GPU 或模型准确率验收。医院 GPU 验收须另列，不能混入这些通过数量。

### 用例设计必须检查的状态组合

| 变化点 | 必须覆盖的正常路径 | 成对故障与恢复路径 |
| --- | --- | --- |
| 认证/首次进入 | 真实登录、空服务器、默认本地图片、首次上传可操作 | 账号变化后的锁只保护需要重新读取的服务器数据，不能永久锁住本地图片和导入 |
| AI 结果读取 | 尚未生成、有效零对象、有对象、人工与 AI 合并 | 断网、超时、404 媒体缺失、500、200 坏 JSON、已生成文件丢失；保留数据，恢复后可再次操作 |
| 列表/详情/原始结果下载 | 文件/标记四种组合，列表不读取全量 JSONL；旧文件首次读取补记 | marker 存在而结果丢失均明确异常；恢复真实文件后各端点恢复；坏格式/几何不能下载为成功结果 |
| 删除/覆盖 | 删除后重传、覆盖后刷新和新标注保存、取消保留旧数据；旧审查历史不阻止覆盖 | 版本冲突、提交成功但响应丢失的原键重放 |
| 审查与导出 | 开始审查→逐项检查/就地纠错→显式通过→真实导出；改动后重新审查只列改动 | 开始/检查失败不产生假进度；全部勾完前不能通过；通过后任何改动立即锁住导出、旧包停止下载 |

- 新增读取失败保护时，必须同时证明健康空态不会被锁；新增重试时，既测试持续故障，也测试恢复后再次完成实际操作。
- 回归缺陷先补行为测试，并记录修复前的预期失败；不能用搜索某段源码存在代替行为验证。性能夹具预置结果可以用于测量，但不能替代全新上传的验收。
- 断言检查实际画框/保存/刷新后的数据和用户下一步能否完成，不能止于“接口返回 200”“页面显示空列表”“错误条消失”。不能为了通过而删除保护断言；业务契约改变时同时解释并更新正常/失败两侧用例。
- CI 的浏览器步骤与后端/单元测试同为发布条件，不使用 `continue-on-error`。发布/交付说明分别列出实际通过、失败、未执行及模拟范围。分支保护是否将工作流设为必需检查属于仓库设置，不能仅凭本地 YAML 声称已经启用。
- 子套件打印 PASS 但运行器整体退出非零时，只能记录该子套件通过及进程/清理故障，不能判定完整验收通过；修正执行环境后重跑取得实际退出码。`--critical-only` 也不能代替七组固定回归。

## 先隔离数据

后端导入/启动会初始化 DB 和表。运行 pytest、夹具、调试服务器前同时指定 `APP_DATA_DIR`、`APP_DB_FILE`、`APP_STORAGE_DIR`，不要使用业务默认目录。夹具只允许 `work/` 下规定的路径。旧 `backend/track_data` 仍可能被兼容读取，浏览器标注测试应筛选测试媒体，不能随便点用户素材。

下列命令在项目根目录执行。`work/review-venv/bin/python` 是本机已有测试解释器；其他机器替换为安装了后端依赖的 Python，例如 `backend/.venv/bin/python`；Windows 见 [部署说明](DEPLOYMENT_WINDOWS.md)。本机测试额外安装 `backend/requirements-dev.txt`（pytest、httpx）；生产镜像不安装测试依赖。

```bash
APP_DATA_DIR="$PWD/work/agent-test-data" \
APP_DB_FILE="$PWD/work/agent-test-data/app.db" \
APP_STORAGE_DIR="$PWD/work/agent-test-storage" \
PYTHONPATH=backend work/review-venv/bin/python -m pytest backend/tests -q

npm run test:workbench --prefix frontend
npm run test:compatibility --prefix frontend
npm run test:annotation --prefix frontend
npm run build --prefix frontend
```

构建包含类型检查。已有混合静态/动态导入和 Python 弃用警告应与新错误区分，不能因为日志有 warning 就猜测业务失败。

## 按修改选择测试

先按下表找到修改面；各专项的详细覆盖与限制见 [details/testing-specialty.md](details/testing-specialty.md)。

| 修改面 | 后端/单元 | 浏览器 |
| --- | --- | --- |
| 标注手势、性能、续标、快捷键 | `test:annotation`；必要时 `test_frontend_tracking_contract.py`、`test_manual_baseline_restore.py` | `annotation-ux-browser.mjs`（48）；`annotation-ui-consistency.mjs`（14） |
| 删除范围、人工正常反馈、重试 | `test:annotation`（含写入队列重放/账号隔离/版本冲突）；`test_annotation_controls.py`、`test_anomaly_detector.py`、`test_tracking_adaptive_feedback.py` | `annotation-delete-feedback-browser.mjs`（67；反馈/删除真后端，GPU模拟） |
| Tracking 调用/完成定位 | seed、rewind、timing、manual baseline、frontend tracking contract pytest | `annotation-tracking-ui.mjs`（14，模拟响应，无真实 GPU） |
| 4K 准备、保存锁与失败恢复 | `test_tracking_performance.py`、`test_video_timing.py`、`test_tracking_adaptive_feedback.py`；真实 4K 连续窗口/坐标、慢模型期间读写、启动重放、预览缓存和原子发布失败 | `tracking-recovery-browser.mjs`（25；真实 4K 视频/保存/抽帧，模拟模型与网络故障，查询恢复/原键重放/冲突备份） |
| 工作区初始状态、结果生命周期、删除重传 | `test_tracking_result_lifecycle.py`；尚未生成/零对象/损坏/丢失、重置回滚、缺失结果不能当空帧送审 | 固定七组回归；`critical-workflows-browser.mjs` 必须从真实登录和原视频上传开始，不预置结果 |
| 帧解码、追踪内存、大量修改项性能 | `test_video_frames.py`（逐帧身份/随机访问/失败 seek/缓存限量与原子失败/真实 processor 输入一致/掩码几何一致/首次模型 dtype）、`test_confirmation_performance.py`（5,000 项/紧凑回执/升级前重放/精确进度） | `performance-browser.mjs`（27；外部真实 4K 副本、50,000 AI 对象、初次读取失败/坏 JPEG 原位重试、C 同帧图像复用/增量回复/并发版本跳变） |
| Chrome 93 / HTTP、素材删除 | `test:compatibility`、`test_media_deletion.py` | `browser-compat-media.mjs`；[Chrome 93 专项回归](details/testing-browser-suites.md#chrome-93--http-专项回归) |
| 重复视频覆盖、旧历史不锁定 | `test_workflow_transitions.py`、`test_media_reimport.py` | `workflow-transitions-browser.mjs`（重复导入/覆盖/响应丢失重放/旧审查历史不再阻止覆盖） |
| 逐帧审查/纠错/通过/失效 | `test_annotation_review.py`、`test_annotation_export.py`（首轮全部帧、重审只列改动帧、无目标确认沿用、旧清单改建、真实 ZIP） | `training-export-browser.mjs`、`critical-workflows-browser.mjs` |
| 训练导出/门禁/版本 | `test_training_export.py`、`test_quality_audit.py`、`test_export_audit_script.py` | `training-export-browser.mjs`（审查门槛、按视频划分、附原视频、多视频合并、响应丢失重放、改动后失效、统计导出） |
| 标注/审查工作台一致性 | 上述对应页面回归、构建 | `workbench-consistency-browser.mjs`：两种模式两尺寸浅深主题、同一画布与工具、审查隐藏追踪、清单定位与帧条、专注、760/390 窄屏与使用说明隔离 |
| 数据库查询/升级、Docker 发布 | `test_annotation_result_dedupe.py`、`test_deployment_preflight.py`、`test_docker_deployment_contract.py`、`test_model_distribution.py` | [容器验收](details/testing-delivery.md)；不是只运行前端构建 |
| 完整离线包、旧版识别、备份升级与回滚 | `test_offline_update.py`、`test_offline_release.py`（Releases 分块、`--assemble`）、`test_offline_oneclick.py`（一键下载与服务器一键升级）、`scripts/test_build_offline.ps1` | [离线升级验收](details/testing-delivery.md#完整离线升级验收)；模拟 Docker 不代替真实导入和数据恢复 |

括号为当前脚本检查数量，**不是每次修改自动通过的结果**。测试源位于 `backend/tests/` 与 `frontend/tests/`。

## 真实浏览器复现

使用 Playwright 库和已安装 Chrome，项目保存的 `.mjs` 脚本是测试入口。脚本默认导入 `playwright`；若库在其他位置，以环境变量 `PLAYWRIGHT_MODULE` 指向其绝对 `index.mjs` 路径。不要把某个开发机的绝对路径当项目要求。

### 1. 准备同一套环境和夹具

以下环境仅用于测试终端，后续夹具和后端服务共用：

```bash
export APP_DATA_DIR="$PWD/work/e2e-confirm-ux-agent-data"
export APP_DB_FILE="$PWD/work/e2e-confirm-ux-agent-data/app.db"
export APP_STORAGE_DIR="$PWD/work/e2e-confirm-ux-agent-storage"
export PYTHONPATH=backend
mkdir -p work output/playwright
work/review-venv/bin/python backend/tests/confirmation_browser_fixture.py
work/review-venv/bin/python backend/tests/annotation_ux_fixture.py
```

先生成 C 夹具建立固定测试用户 ID，再生成其他夹具。UX 脚本要求路径含 `/work/e2e-confirm-ux`，生成横/竖两个 60 帧真实 AVI，并**重置测试媒体工作区**。C 夹具生成 main、zero、failure、many、portrait 五个独立任务；many 含 45 个真实修改项，用于分页导航，portrait 是 450×800 真实竖向视频，用于检查全幅主图比例和可见边界。B 夹具生成一个三帧任务。token 在 `work/*fixture.json`，不要输出到报告。

撤回/覆盖/本帧重审另运行 `work/review-venv/bin/python backend/tests/workflow_transitions_browser_fixture.py`，随后 `CONFIRMATION_ORIGIN=http://127.0.0.1:5373 node frontend/tests/workflow-transitions-browser.mjs`。使用上述同一隔离环境；夹具生成 free/pending/started/confirm 四个独立视频，每批内容含唯一标记，防止误撞上次夹具的真实 SHA。浏览器用例仅让这四个素材进入列表，消费本批任务，覆盖失败响应的原键重试会真实提交；重复运行应先生成新夹具。测试后端设 `SAM3_ENABLED=false`：只模拟 rewind 返回以检查新轮次输入，人工记录/seed 真实保存，Tracking 在 CPU 禁用边界返回 503，不执行模型。截图及 37 项结果保存在 `output/playwright/`。

### 2. 启动测试服务

检查 3301 / 5373 未被占用；已占用时选择其他端口并设置脚本对应 `REVIEW_ORIGIN` / `CONFIRMATION_ORIGIN`，不要结束不属于自己的进程。

同一环境启动后端：

```bash
work/review-venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 3301
```

另一个终端启动前端：

```bash
API_PROXY_TARGET=http://127.0.0.1:3301 npm run dev --prefix frontend -- --host 127.0.0.1 --port 5373 --strictPort
```

### 3. 单独运行套件

固定套件可用运行器的 `--only <套件名>` 单独定位问题（不能代替完整回归）。手动运行时，按上面的环境启动服务并生成对应夹具后执行，例如：

```bash
CONFIRMATION_ORIGIN=http://127.0.0.1:5373 node frontend/tests/training-export-browser.mjs
CONFIRMATION_ORIGIN=http://127.0.0.1:5373 node frontend/tests/workbench-consistency-browser.mjs
```

`training-export` 与 `workbench-consistency` 需要 `confirmation_browser_fixture.py`（测试用户与统计导出用的历史数据）和 `annotation_ux_fixture.py`（两段 60 帧视频与追踪结果）；脚本先为两段视频保存空工作区，使追踪结果可以审查。`performance-browser.mjs` 只保留标注页部分。`browser-compat-media.mjs` 仍点击早已移除的“完成标注并送审”按钮，在本次改动前就已过时，需要单独更新后才能使用。

外部真实视频性能测量与 Chrome 93 / HTTP 专项回归见 [details/testing-browser-suites.md](details/testing-browser-suites.md)；Docker、离线升级验收见 [details/testing-delivery.md](details/testing-delivery.md)。

## 日志驱动排查

1. 从页面错误获取请求号和动作；查看控制台与 Network 的实际响应，先分清客户端模拟故障还是服务端真实拒绝。
2. B/C/训练导出服务日志在 `$APP_DATA_DIR/logs/review.log`；默认 `backend/data/logs/review.log`，约 5 MB/文件、保留 5 个历史文件。按 `req_...` 查请求，再以幂等键、session/change/export ID 关联业务记录。
3. 追踪与媒体读取同时检查 Uvicorn 输出、`[ai-track]` / `[tracking-rewind]` 和对应媒体 JSONL；不要凭画面推测请求发到了哪里。
4. 复现后修复并重跑失败用例及相关正常路径，保留证据；不删契约断言来让测试通过。

| 事件 | 关注内容 |
| --- | --- |
| `review.request`、`review.*` | 路由、状态、请求号、幂等及事务失败 |
| `confirmation.committed/replay/rejected/storage_failed` | 选择、版本冲突、原键恢复、回滚堆栈 |
| `dataset.queued/started/progress/ready/download` | 任务阶段与真实处理帧数 |
| `dataset.failed/enqueue_failed/interrupted` | 提帧/写包/发布/重启失败 |
| `audit.captured`、`audit.export_failed/uncovered/extracted` | 自动固定快照、只读提取失败、旧包覆盖范围与提取数量；CLI 诊断在 stderr，不混入 ZIP |
| `annotation.frame_slow/frame_failed/prefetch_failed` | 媒体、目标帧、耗时；慢阈值 250ms |
| `annotation.workspace_save_failed/tracking_load_failed` | 媒体、保存序号与实际读取异常 |
| `annotation.tracking_read_failed/tracking_results_missing/completion_results_missing`、`tracking.expected_result_missing` | 区分损坏、已有结果丢失、送审被阻止及任务报告完成却无结果；正常尚未追踪不记录为故障 |
| `media.deleted/delete_failed/delete_rejected`、`annotation.media_deleted/media_delete_failed/media_list_failed` | 删除结果、媒体 ID、操作者、文件系统异常；前端网络失败不得当删除成功 |
| `annotation.local_save_failed/selection_save_failed`、`ui.preference_save_failed` | 本机存储不可用 |
| `annotation.feedback_saved`、`tracking.anomaly_pause/completed` | 正常确认原因、尺寸参照帧/来源/完整框、全部已认可对象对、实际载入参照与持续次数、首次风险帧及决定帧、运动样本及续追起止帧；区分清除当前暂停与后续检测是否读取确认参照 |
| `tracking.rewind/decoded/task_started/task_finished/task_failed/start_replay` | JSONL 回退耗时、本轮原始尺寸/帧数/RGB 字节、模型任务与耗时、启动重放及失败堆栈；与前端 `annotation.tracking_failed` 的阶段/帧号/taskId 对齐 |
| `tracking.preview_started/preview_completed/preview_failed` | 按需整段预览编码及耗时，区分预览失败与追踪或保存失败 |
| `source.lock_slow` | 来源锁等待 ≥500ms 或占用 ≥1s 时记录操作名与 wait_ms/work_ms；用来识别其他上传/保存是否仍在阻塞，不能只看客户端 timeout 猜模型耗时 |

日志不记录 JWT/密码；不要记录每个鼠标移动。浏览器 route 注入的 503 不会出现在服务端日志；数据库触发器故障测试验证真实事务回滚与日志。
