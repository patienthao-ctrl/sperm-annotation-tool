# 测试与日志排查

## 测试分层

| 时机 | 必须运行 | 不能代替 |
| --- | --- | --- |
| 开发中每次修改 | 下文“按修改选择测试”中的专项 pytest / 前端单元测试；涉及页面时 `run_browser_regression.py --only <相关套件>` | 只是定位和快速反馈 |
| 提交前（每批改动一次） | `backend/tests` 全部 pytest、`test:workbench` / `test:compatibility` / `test:annotation`、`npm run build`、`python3 scripts/check_repository.py`；改动涉及工作区加载、认证、保存/重试协议、公共 API、缓存或依赖/构建时，再跑一次不带筛选参数的完整七组浏览器回归 | 分批或筛选运行的结果 |
| 推送 / CI | CI 重跑完整门禁 | 本地通过记录 |

纯样式、文案或使用说明的小改动，提交前运行相关专项、构建，并对受影响页面做 `--only workbench-consistency`；不要求完整七组。汇报时分别列出实际通过、失败、未执行和模拟的范围。

## 审查与导出专项

当前流程为标注 → 审查 → 导出。审查是标注页的审查模式；旧审查页、对比确认页及其浏览器测试已删除（A/B/C/F 数据层的后端测试仍保留，因为统计/审计导出读取这些历史表）。新主流程必须从真实画框、首尾追踪、在审查页完成清单并通过，到下载 ZIP。

- `test_annotation_review.py`：清单五类项、计数突变与疑点范围、随机抽检随帧数缩放、整帧检查点、加严抽检只追加一次、未知帧必须处理、通过时的修改记录与检查人、通过后改动变为 stale、重新审查只列改动、未保存工作区返回 notReady、HTTP 往返与错误码；反向独有轨迹的连续/断帧/贴边/面积/单帧、检测器单点丢弃与静止分类、检测候选与反向轨迹一致性、采用编号冲突、轨迹中途结束、`not_target` 校验与同事务排除/撤销、最近操作人、状态缓存按 revision 失效。
- `test_annotation_export.py` 覆盖 `review/decisions.jsonl`：有 `not_target` 判断时行数和字段正确，没有判断时 manifest count=0 且 ZIP 不含文件。 `test_training_export.py` 的合并脚本用例验证混合多视频包与新版重复视频的两种输入顺序：只保留实际选中视频版本的判断，不重复追加其他视频的行，并拒绝判断文件缺失或行数不符。
- `test_endpoint_review_tolerance.py`：小头框低IoU但包含/近中心、原图分辨率缩放一致、真正偏移/严重尺寸变化、坏证据与独立身份风险保留；旧清单只升级一次、普通检查保留、新风险不继承旧勾选、历史归档、已通过轮次不重写及重建失败/恢复。`training-export-browser.mjs` 通过专用隔离夹具注入旧清单和小差异/真实偏移，验证真实刷新后去噪、保留强风险定位、再次刷新不复活/重复，截图 `endpoint-review-tolerance.png`；同一隔离夹具为 `ux-video` 第 20–24 帧写入尾帧对象 9 的反向独有框，浏览器实际点击“采用这段轨迹”，核对工作区五帧新增同编号人工框及 `origin=review_adopted`、检查结果改为 fixed，并在改动第 20 帧后的新一轮清单中确认不再出现。`training-export-browser.mjs` 还通过真实补框/保存/审查链路验证任务D：模拟模型返回有效框后只在当前帧新增一个人工框且保存后才记fixed；再次命中已有框不重复新增、不改检查时间；模型503保留错误、人工补框提示和原数据/检查结果。夹具清理由正常撤销与保存完成。`workbench-consistency-browser.mjs` 的Z使用该套件真实存在的反向漏标项，先展开分组，不假定一定有检测器路径。`workbench-consistency-browser.mjs` 验证卡片放大图有像素、选择工具点击圆圈放大且圆圈在视口内、Esc 恢复倍率和滚动、画框工具不触发放大，以及 `1/2/3/N/Z`、输入框隔离、已检查项“改判”入口和另一账号近期操作提示。`annotation_ux_fixture` 的末帧带显式边界退出，避免把正常清空的末帧误当轨迹中断。仍须运行完整七组回归；模型为模拟，真实业务样本仅在副本中做保存结果回放。
- `test_annotation_export.py`：导出快照先要求审查通过（REVIEW_REQUIRED），审查确认无目标的帧作为显式空帧；来源完整性、版本/账号、原键重放、真实原视频 ZIP 与编辑后旧下载撤销。
- `test_media_reimport.py` / `test_tracking_restart.py`：旧审查历史不再锁住覆盖重标和重新追踪。

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

`independent-integrity` 保留独立审查中的行为断言：坏结构的成功响应、完整结果撤销后的 SPA 重读、跨素材迟到失败/成功、真实编码 4K 与 50,000 合成对象的读入/保存/刷新。另验证匿名/无效凭据不能读视频，HTTP 下真实 H.264 原生播放和 Range 正常，退出后不能读；真实保存已提交但响应丢失，再遇 `/auth/me` 500/网络失败/401，检查原请求保留、同账号重放与恢复后再次保存。首次新上传还从真实 Tracking/异常检测进入两个对象暂停，一次整帧正常确认后继续发布结果；只有模型预测被模拟。

同组调用 `tracking-policy-checks.mjs`，新上传 8 帧视频并在真实画布画框，不导入 store 或预置工作区：普通高度约 0.64 倍持续观察后返回首次风险帧，显示确切指标/界限/参照与决定帧，数值按保存的诊断核验而非假设画框像素舍入后仍恰为 0.64；整帧确认同时包含未报异常对象，确认响应丢失后刷新沿用原请求恢复，轻微变化续追至末帧、刷新仍保留检查点；人工 seed 已有稳定重叠不中断；三个对象新增重叠保留全部 partner，一次整帧确认后续追；纯重叠确认有重置入口，重置关联对象对但保留其他独立组合和历史；极端缩框仍立即暂停。刷新前等待真实帧导航和自动保存游标完成，不能把旧游标恢复当成确认失败。检查两种窗口、浅深主题和专注模式的确认按钮及逐帧导航首屏可见性。仅模型框输出使用显式 `policy-*` 测试名称驱动，原视频读取、异常检测、反馈与结果发布均走真实后端；不是实际 GPU 精度验收。

该合成负载设置宽松 CI 停滞门槛：全量结果读取 <8 s、首次恢复 <15 s、同步人工缓存 <64 KiB、最长主线程任务 <1.5 s，并保留实测耗时。这是隔离回归预算，不是医院 GPU、GB 视频或内网延迟的 SLA。慢盘测试以可释放的哈希阻塞注入证明同事件循环的健康请求与其他媒体保存仍在 2 s 内完成；不能将它称为真实医院慢盘实测。

后端 `test_independent_integrity_audit.py` 保留独立审查用例；`test_integrity_recovery.py` 补 seed 临时写/替换失败、坏历史来源、合法点与像素边界、同事件循环慢盘上传时读取/另一视频写入、查重途中删除的竞态、长期反馈回执体积及重置后的准确重放。前端 `tracking-result.test.mjs` 通过 `npm run test:annotation` 校验结构故障组合，不能只靠静态源码断言。规范 ID 校验后，路径探针直接构造不安全目标；原子 seed 修复后，磁盘故障注入同时覆盖目标和临时文件，保留所有旧数据与错误断言。

`critical-workflows-browser.mjs` 不导入前端 store，也不预先生成工作区、AI 结果或审查记录：真实登录 → 上传原视频 → 画框保存/刷新 → 保存成功但响应丢失 → 刷新沿用原键/原正文重放 → 再次画框保存/刷新 → 删除重传 → 取消/覆盖重标 → 追踪 → 导出被锁 → 去审查（注入开始失败后重试）→ 逐项检查 → 审查通过 → 快照响应丢失后刷新原键重放 → 下载真实 ZIP → 审查页内改框使通过失效并锁住导出。夹具只产生输入视频。CI 还在实际生产前端镜像上运行同一用例的 CPU 模式，并启用旧浏览器 API 兼容检查，避免只验证 Vite 开发页面。

`training-export` 固定套件同时执行 `statistics-export-checks.mjs`：从真实 A/B/C/F 数据生成全量统计 ZIP，核验文件校验和、空帧和历史/当前 F、密码与原视频排除；验证匿名/跨账号下载拒绝、创建提交后响应丢失及刷新原键重放、查询失败恢复、下载原包重试、过期重新生成，并检查 1366×768 / 1180×760 浅深主题、弹窗焦点和共用使用说明。它使用 Chrome 93 API 兼容配置，不执行模型推理。统计 ZIP 后端专项用例见 `test_statistics_export.py`；仍须运行全部七组，不能用此新增场景代替固定门禁。

`tracking-recovery` 同时执行 `tracking-restart-checks.mjs`，在隔离 4K 原视频的较早帧修正，验证取消保留分支、暂停帧不能绕过、回退失败原键重试、成功后 seed 使用早帧原像素、未来人工框经保存/刷新保留、旧暂停不复活，以及发布成功但响应丢失后刷新重放不重复截新模型结果。恢复期间的送审状态查询失败保留原起点，后来已送审的合法回执重放不得发 seed 或模型请求。模型输出模拟，分支/工作区/收据走真实后端。`test_tracking_restart.py` 验证逐文件发布和提交故障、重启及读取恢复、已送审/轮次/版本边界、回执准确重放与持久暂停保护；包括首次工作区尚不存在的暂停写入失败、持续故障与读取恢复、legacy 人工 seed 保留、文件和目录同步失败的成对恢复。原反馈与删除/撤销用例继续运行，不能只验证新入口能点。

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

## 多个 AI 工具并行开发

同一个文件夹同一时间只让一个工具写代码，否则测试会混入别人改到一半的代码，失败无法归属，提交也挑不干净。要并行，每个任务开一个 git worktree：独立文件夹、独立分支，互相看不到未提交的改动。Claude Code 桌面版新建会话时可直接选择 worktree；其他工具在项目根目录手动执行：

```bash
git worktree add "../标注工具-审查卡片" -b feat/review-card main
```

最后一个参数是基线分支，按任务实际要接着做的分支填写。

新 worktree 只有 Git 跟踪的文件，没有被忽略的依赖、`.env`、`/models/` 和 `work/`。不改依赖的任务直接链接主目录的依赖（`<主目录>` 换成原项目文件夹的绝对路径）：

```bash
ln -s "<主目录>/frontend/node_modules" frontend/node_modules
mkdir -p work && ln -s "<主目录>/work/review-venv" work/review-venv
```

Windows 用目录联接：`mklink /J frontend\node_modules <主目录>\frontend\node_modules`，Python 解释器可直接用主目录的 `backend\.venv\Scripts\python.exe`。任务要改 `package.json`、`requirements*.txt` 时不要链接，在该 worktree 单独安装，避免改坏其他任务共用的依赖。

- **测试数据天然分开**：上面的 `APP_DATA_DIR` 等都以 `$PWD/work/` 为根，浏览器回归自选空闲端口，`work/browser-regression.lock`、`work/e2e-*` 和 `output/playwright/` 也在各自 worktree 内，两个 worktree 可以同时跑回归。同时跑多组完整回归会抢 CPU、更容易超时，失败先单独重跑确认再下结论。
- **交接记录各管各的**：`work/HANDOFF.md` 在各自 worktree 的 `work/` 下，写本任务的进度即可。
- **按文件拆任务**：`AnnotatePage.vue`、`workspace.ts`、`annotation_review.py` 这类热点文件同一时间只交给一个任务，否则分支各自通过、合并时仍会冲突。
- **需要真实模型或部署验证**（SAM3 权重、`.env`、Docker）的任务在主目录做，或明确链接所需目录；不要把 CPU/模拟结果当成 GPU 验收。
- **合并后再过一次门禁**：每个分支先按“测试分层”的提交前要求通过并提交；合并到一起后再跑一次完整门禁（七组浏览器回归）。各分支单独通过不代表合并后通过。

完成并合并后清理：

```bash
git worktree remove "../标注工具-审查卡片"
git branch -d feat/review-card
```

## 按修改选择测试

审查信号/轻量标记专项：`test_review_actionable_signals.py` 验证身份证据保留但不生成项、人工框所在帧豁免（独立尾ID转换前后均覆盖）、AI其他帧仍检查、已有框候选不重复提示、每项3对象上限及逐帧位置、密集连续分组不漏项、检查失败明确保留。`test_endpoint_review_tolerance.py` 验证候选对应的几何容差和旧清单升级、历史/检查保留、失败回滚及恢复。前端 `review-presentation.test.mjs` 验证逐帧强调、人工豁免、候选圆圈与短标签；固定 `training-export` 套件通过隔离旧清单 fixture 验证升级、排序、真实SVG不增加框、漏标只显示圆圈、普通/专注/窄屏浅深主题、几何不变及后续纠错、审查通过与导出。模型模拟，不代表算法召回率验收。完整七组入口仍必须通过。 `test_annotation_review.py` 补充同位置连续4帧合并、同帧5处编号和九宫格方位、远距离/同帧邻近双候选不混合、断帧/5帧上限、候选多框改列关联及原始证据不变；候选对应与首尾容差解耦，坏坐标保持检查失败。`review-location-checks.mjs` 随固定training-export执行：实际界面先保存人工修框，夹具只原子更新隔离Tracking证据及旧清单，不直接覆盖运行中工作区；核验自动放大、手动倍率保留、强制左上视野后右下候选落在可见画布（含elementFromPoint）、已可见不移动、序号/列表一致、前后切换、读帧失败隐藏标记且不定位旧图、显式重试恢复、迟到帧不抢定位、几何与检查进度不变，审查专注中定位可用，检查面板收起可回全宽并可重新展开。



三路综合专项：`test_joint_tracking.py` 验证正向漂移实际修框、反向漂移保留正确输出、检测细化、已知身份找回、两侧缺失不补假框、检测缺失不删目标、内部未知候选不扩范围、自然重叠不合并、人工框与删除优先、30对象身份不变、全输入结构验证后才修改、原图/检测失败释放与恢复，以及 worker 失败保留几何/记录日志、缺端点启动不写seed。`test_endpoint_tracking.py` 仍验证旧仅核验兼容。固定 critical-workflows 的 endpoint-tracking-browser 验证 combineResults 原键恢复、实际中间几何来自综合而非复制正向、画面只有一套框、微调/撤销、整段删除/刷新/撤销、旧后端缺综合结果不假成功、送审冻结后的简短状态。浏览器模型均模拟；专项不能替代完整七组固定回归。

综合回归同时覆盖隔帧漏检时不在10/16px之间闪烁（先保留失败证据），以及综合函数在修改工作副本后异常的注入：worker 仍只发布原有正向几何与失败标记，不能把部分综合框混入失败结果。真实模型观测可冻结后回放最终路径策略，和新神经推理分别记录。

结果接口的综合失败/恢复状态和浏览器刷新后的持续提示成对验证；部分帧 checked 不能声称整段完成。最终完整浏览器回归期间固定业务源码，不让 Vite 热更新重置测试中的页面/工作区状态；有热更新干扰的失败记录保留，不能当作最终通过。

真实模型组件验证在服务器 `runs/feasibility/JOINT-TRACKING-20261027-001/`，复用已有1.mp4原始80–90帧、4个提示对象；SAM3 和 YOLO 在既有独立环境顺序运行，不本机推理、不改环境。原生真实观测与受控漂移回放分开记录；提示来源是冻结AI测试夹具，不是人工首尾金标，不能将输出变化数当作准确率或医院验收。日志/图片/源码指纹下载到本地 work/joint-tracking/。

人工首尾双向专项：`test_endpoint_tracking.py` 覆盖独立尾编号、多窗口真实帧映射、一对一/歧义拒绝、持续定位分歧、进出画未对应、空尾帧证据不足、模型失败/缺帧不通过、原正向框保留和人工端点校验；A 来源合并验证匹配尾框不重复、原始人工ID保留及尾框删除生效。正常/失败均使用隔离数据。固定 critical-workflows 追加 `endpoint-tracking-browser.mjs`：真实登录、导入独立视频、首尾画框/刷新、启动成功丢响应后原键原正文恢复、双向证据发表、尾帧无重复框、普通/专注与多尺寸浅深主题、真实送审并查看冻结证据。浏览器模型输出仍明确模拟，不能作疑点召回率验收。

服务器组件验证样例位于本地 `work/endpoint-tracking/`，记录已有1.mp4短片段11帧/4对象的真实SAM3反追。其尾帧提示是已有AI观察测试夹具，不是用户人工金标；只验证模型/API/窗口映射可运行，不代表首尾人工标注后的查错准确率、全视频或医院验收。后续质量验收需要真实人工首尾与独立中间帧核对。

观看操作专项：`npm run test:workbench --prefix frontend` 验证键位、4K 适应边界和指针锚点；标注页快捷键由 `annotation-ux`、`annotation-ui-consistency` 覆盖，审查模式复用同一画布与键位。（原跨 A/B/C 三页的 `workbench-shortcuts-checks.mjs` 已随旧页面删除。）

历史 A/B/C/F 数据层专项：`test_review_workflow.py`、`test_review_object_edits.py`、`test_confirmation_workflow.py`、`test_confirmation_presence.py` 等继续验证数据层不变量（统计/审计导出读取这些表）；它们不再对应任何页面，删除路由的 HTTP 用例已移除。



整帧人工检查点专项：`test_tracking_frame_checkpoint.py` 验证一次确认含未报异常对象、已保存人工修框/新框优先、删除排除、固定基准替换与恢复、缺失/坏框/错误帧/版本冲突不部分提交、原子发布失败及原键恢复。固定 `independent-integrity` 的 tracking-policy 场景验证真实页面一次确认全部对象、刷新及续追；只有模型预测模拟。Windows 可使用 `backend/.venv/Scripts/python.exe scripts/run_browser_regression.py --python backend/.venv/Scripts/python.exe --channel chrome`，建议设置 `PYTHONUTF8=1`；运行器使用本机解释器、Vite 和自己启动的进程树，不终止已有开发服务。

原图/Mask 辅助政策专项：`test_tracking_evidence.py` 以独立合成图验证旋转与裁尾、背景厚块/多核、未知证据及对象共用主体；`test_tracking_identity_evidence_policy.py` 验证 25/30 对象轮流变形、真实主体跳跃与高 FPS 下稳定主体/变化框中心、独立图像风险、严重面积/Mask 损失、错误证据绑定与旧 BBox 回退；`test_tracking_image_evidence.py` 验证原始帧身份、显式引用、失败/恢复与读取释放；`test_tracking_native_evidence_integration.py` 验证真实编排/结果发表使用首次图像风险且释放 reader，模型/图像结果模拟；`test_tracking_evidence_feedback.py` 验证人工确认参考及主体/bbox 运动样本分离与准确重放；`test_tracking_image_pause_causes.py` 成对验证持续图像/几何风险不错误提升已恢复或未持续的另一原因。`test_video_frames.py` 比较标准全尺寸 Mask 与 ROI 框/面积/逐像素完全一致，并确认 ROI 不保留整帧 backing array；25 个整幅 4K 异常 Mask 保留几何但不分配不可用的大 ROI。真实视频须单列同 prompt 原图与真实 SAM3 结果重放；不能将合成支持证据当识别准确率。

自然运动与重叠专项：`test_tracking_natural_motion_policy.py` 使用严格绑定的本帧原图/Mask核验标志验证正常旋转、身体范围未知、参考缺失、自然双框重叠与续追恢复不误停；坏源、坏帧/框/引用、空Mask和失效标志恢复旧兜底。`test_tracking_subject_boundary_policy.py` 从实际合成图像和二值Mask验证轻微边缘欠覆盖与真漏头分流、头完整但尾不可辨与明确自身大段漏尾分流；Mask完整但BBox截头也不能漏停。两者包含生产probe→detector组合测试，不能只手填一条supported摘要就声称原图判断修好。多对象验证要在正常形变/靠近中插入一个真丢失对象，既检查不误停，也检查没有一起被豁免。

尾部归属、历史Mask面积和原因展示专项：`test_tracking_tail_attribution.py` 从真实合成原图/Mask验证弱桥接背景不冒充自身尾部、清晰自身漏尾仍暂停、近头光晕与邻居阻断、正常检查点后新风险及坏输入恢复；`test_tracking_mask_area_policy.py` 验证当前严格有效且头部可靠时面积骤减仅提示，真实head/tail/merge、空Mask、unknown、旧证据及坏绑定仍保守，实时与history prime一致，并覆盖30/60对象和稀疏INFO日志。`test_tracking_pause_reason_publication.py` 通过生产track_video发布验证对角线增长/缩小分类、新旧错误类型恢复、SOFT尺寸不盖过真运动、未知历史原因保留原分类以及不改变工作区版本/标注/反馈。固定tracking-policy浏览器场景同时验证新旧错误type、仅rawReasons记录和缩小/真运动的实际标题及依据，展示恢复不自动确认、不发反馈或改变标注；对角线原因不扩大运动学习资格。

自然重叠的反向成对用例必须包含“一方有可靠参考，另一方真实prime因多核未建立参考，随后两Mask一起跟到唯一可见主体”。仅放宽BBox重叠会使这类真风险漏停；新 `singleCoreConflict` 用原图可见核数、双向真实Mask交叠和可靠partner逐帧核验，连续12帧仍停。测试同时覆盖双头可见（即使第二头不在Mask内）、独立Mask、未读取完整双方框范围、坏诊断/坏partner、短遮挡后恢复、换partner与帧间断重置、跨追踪历史恢复，不得把未知对象补成新可信头来通过。

`test_tracking_native_history_resume.py` 经生产 `track_video` 恢复、检测及发布验证8+4、11+1两种窗口都在总第12帧暂停并返回首次风险帧；仅模拟模型/窗口IO，原图合成probe与Mask分析不替换。它同时防止源签名设在恢复之后、或历史仍只读8帧造成的漏停。坏metrics专项覆盖实时push与历史prime的拒绝/日志/健康恢复，不能只测直接构造的检测器正常输入。

快速定位优先复用冻结观察，避免每次重跑SAM3到同一帧。`scripts/replay_tracking_pause_cases.py` 加载私有 `tracking-pause-replay-v1` manifest，真实执行检测器状态转移；记录输入/政策SHA与时间，只传原样框、面积和已有摘要，不补造Mask或 `observationVerified`。输入含frameWidth/frameHeight/fps及episodes；每项包含固定manualBaselines、可选geometryReferences/overlapReferences/seed/sourceSignature、逐帧observations和可选expectPause；`provenance` 必须注明真实连续输出、单帧保持的压力输入或人工构造条件，不能混称现场完整序列。通用CLI行为由 `test_tracking_pause_replay.py` 覆盖。

```bash
work/review-venv/bin/python scripts/replay_tracking_pause_cases.py work/replay-input.json --output work/replay-after.json
work/review-venv/bin/python scripts/replay_tracking_pause_cases.py work/replay-input.json --policy-source work/frozen-code/anomaly_detector.py --output work/replay-before.json
```

原图光核可在CPU单独重算并对照日志点位和BBox核覆盖，旧Mask覆盖摘要必须标明沿用记录；这不能代替重新计算原Mask。若已保存真实模型的逐帧二值Mask/框/分数，则直接用原视频和生产 `NativeImageEvidence → tracker → 结果发布` 在隔离目录重放，无需加载模型；记录真实输出与最终源码SHA。若只有BBox/摘要、缺中间帧或Mask，明确报告缺口，不能把矩形填充Mask、重复同一帧或补填核验标志冒充真实连续推理。现场视频/Mask/工作区和私有manifest放work中，不纳入Git。真实短窗口补采需保留原视频、对象数量、prompt和逐帧Mask校验和，写明局部重新初始化与原首帧长程状态不同；只接纳完整写出的帧，采样进程错误或内存预算中止不得改记为计划全部完成。已有有效帧可独立核验并回放，原错误日志保留。完整浏览器回归仍需执行，快速回放不能替代保存、确认与续追流程验证。

主体关联专项 `test_tracking_identity_association.py` 验证较暗的自己不会被更亮的邻居替换、两个框共同包含多个头时不按 ID 顺序强行分配、粘连期间冻结可靠参考而分离后恢复，以及两个对象真正共用单一主体和自己的主体丢失仍报风险。`test_tracking_identity_reference_lifecycle.py` 使用真实编码小视频检查压缩噪声、历史人工来源恢复、确认后的紧凑身份恢复及读取释放。v2 身份证据的原始主体来源与最新几何检查点分别绑定，旧 v1、错误帧/框/源签名、非有限值不能授予新的几何豁免；普通掩膜污染与少量尾部遗漏不能冒充明确的大段框外遗漏，极端坍缩和真实丢失仍须暂停。`test_tracking_evidence.py` 的重大覆盖用例成对检查：同样的大段自身框外遗漏不能因增加邻居光核而漏停；完整自身目标加邻居仍提示；经过邻居光核的续段不归给自己，但到达邻居前已经明确遗漏的自身支路仍保留风险。

反馈范围专项 `test_tracking_object_feedback_scope.py` 验证单对象正常只清本项、正常/修正混合顺序、同次暂停双方分别正常才认可共同重叠、显式整帧确认、紧凑身份仅读取一次当前原帧、坏来源不重选亮暗峰、读取失败保持原状态并原键恢复。修改/删除/撤销后的关系 epoch、同帧新人工来源、换伙伴、重置和新 generation 必须使旧认可失效；历史决定和幂等回执仍保留。确认相关缺失读取保护要覆盖真实使用的加载路径，不能只模拟已经不再参与该流程的旧 helper。

真实模型验收使用相同视频与首帧 JSON 的校验和，记录实际设备、dtype、代码校验和、每次暂停的首次风险帧/决定帧/原因、人工实验确认范围、主体位置与掩膜。先对保存的真实掩膜和原图复核关联，再重新运行 SAM3 到约定帧；两者分别报告。若现场工作区多加了对象或修过框，作为第二组输入说明，不能称其与原始首帧完全相同。零暂停本身不证明修复：必须同时确认主体没有换到邻居，并通过真实丢失、合并和严重覆盖遗漏的负向用例。Apple MPS、本机浏览器的模拟模型与医院 CUDA 分别记录，不互相替代。

固定 `independent-integrity` 还调用 `tracking-focus-checks.mjs`：真实上传/画框/追踪进入暂停，验证初始单项、两项处理到最后一项、object 与 frame 的明确范围、正常/修正混合顺序、选择与选择工具联动、视野外框保持缩放并最小平移/视野内不移动、错帧先加载再定位、移动/缩放保存、主动检查其他框不抢选择、读帧失败/原位恢复、成功响应丢失刷新后原键原正文恢复、消失对象不能确认正常及同 ID 补框续追。两窗口浅深主题、专注/返回帧均验证。模型输出模拟；不从测试 UI 导入 store，不绕过反馈/版本保护。修复前失败截图与断言必须单独保留，不能被修复后复测覆盖。

其中 `tracking-pause-layout-checks.mjs` 比较同一真实暂停前后的图像高度，验证普通模式的确认区实际位于右侧主操作区，专注模式使用紧凑操作条。1366×768、1180×760 浅深主题均核验至少 300 px 的可编辑图像高度、桌面紧凑条不超过 96 px、检查依据独立滚动且不覆盖图像、不改变图像高度。760×900、390×844、390×480 浅深主题同时检查普通/专注、增强画面、按钮中心命中和文字不溢出、至少 180 px 图像高度；小窗口允许滚动到操作区，不以容器存在代替可用性。手动放大后展开/收起依据及切换模式，实际比较渲染帧宽度、选中身份和视野位置，而不只比较倍率文字；居中图像刚变成可滚动时仍保持图像锚点、四边均可达。把真实选框滚到图像右下角后，核验浮动缩放控件仍在可见范围内；按住 Space 平移画面后，选框四角中心均能实际命中编辑层，视野操作不改变已保存坐标。从专注模式直接启动追踪的第一次暂停也核验实际帧尺寸，不能在 100% 文字不变时暗中重适应；依据刚打开即适应画面或改选对象时，旧异步视图恢复不能覆盖新操作；后续继续原有真实修框保存、最后一项定位和确认响应丢失后的原键重试，并核验普通/专注暂停中已保存正常确认的重置入口仍可用。`pause-evidence.test.mjs` 串联原有诊断和新可视展示，验证真实触发与 SOFT 辅助指标分离、缺失阈值不补默认比较尺、多个重叠伙伴及单位准确，展示不改写原始证据。真实现场历史暂停的截图应另行标明快照来源与回放方式；它不能代替该套新导入用户流程，也不等于重新执行 GPU 模型。

| 修改面 | 后端/单元 | 浏览器 |
| --- | --- | --- |
| 标注手势、性能、续标、快捷键 | `test:annotation`；必要时 `test_frontend_tracking_contract.py`、`test_manual_baseline_restore.py` | `annotation-ux-browser.mjs`（48）；`annotation-ui-consistency.mjs`（14） |
| 删除范围、人工正常反馈、重试 | `test:annotation`（含写入队列重放/账号隔离/版本冲突）；`test_annotation_controls.py`、`test_anomaly_detector.py`、`test_tracking_adaptive_feedback.py` | `annotation-delete-feedback-browser.mjs`（67；反馈/删除真后端，GPU模拟） |
| Tracking 调用/完成定位 | seed、rewind、timing、manual baseline、frontend tracking contract pytest | `annotation-tracking-ui.mjs`（14，模拟响应，无真实 GPU） |
| 4K 准备、保存锁与失败恢复 | `test_tracking_performance.py`、`test_video_timing.py`、`test_tracking_adaptive_feedback.py`；真实 4K 连续窗口/坐标、慢模型期间读写、启动重放、预览缓存和原子发布失败 | `tracking-recovery-browser.mjs`（25；真实 4K 视频/保存/抽帧，模拟模型与网络故障，查询恢复/原键重放/冲突备份） |
| 工作区初始状态、结果生命周期、删除重传 | `test_tracking_result_lifecycle.py`；尚未生成/零对象/损坏/丢失、重置回滚、缺失结果不能当空帧送审 | 固定七组回归；`critical-workflows-browser.mjs` 必须从真实登录和原视频上传开始，不预置结果 |
| 帧解码、追踪内存、大量修改项性能 | `test_video_frames.py`（逐帧身份/随机访问/失败 seek/缓存限量与原子失败/真实 processor 输入一致/掩码几何一致/首次模型 dtype）、`test_confirmation_performance.py`（5,000 项/紧凑回执/升级前重放/精确进度） | `performance-browser.mjs`（27；外部真实 4K 副本、50,000 AI 对象、初次读取失败/坏 JPEG 原位重试、C 同帧图像复用/增量回复/并发版本跳变） |
| Chrome 93 / HTTP、素材删除 | `test:compatibility`、`test_media_deletion.py` | `browser-compat-media.mjs`；下述旧 API 回归模式 |
| 重复视频覆盖、旧历史不锁定 | `test_workflow_transitions.py`、`test_media_reimport.py` | `workflow-transitions-browser.mjs`（重复导入/覆盖/响应丢失重放/旧审查历史不再阻止覆盖） |
| 审查清单/纠错/通过/失效 | `test_annotation_review.py`、`test_annotation_export.py` | `training-export-browser.mjs`、`critical-workflows-browser.mjs` |
| 训练导出/门禁/版本 | `test_training_export.py`、`test_quality_audit.py`、`test_export_audit_script.py` | `training-export-browser.mjs`（审查门槛、按视频划分、附原视频、多视频合并、响应丢失重放、改动后失效、统计导出） |
| 标注/审查工作台一致性 | 上述对应页面回归、构建 | `workbench-consistency-browser.mjs`：两种模式两尺寸浅深主题、同一画布与工具、审查隐藏追踪、清单定位与帧条、专注、760/390 窄屏与使用说明隔离 |
| 数据库查询/升级、Docker 发布 | `test_annotation_result_dedupe.py`、`test_deployment_preflight.py`、`test_docker_deployment_contract.py`、`test_model_distribution.py` | 下方容器验收；不是只运行前端构建 |
| 完整离线包、旧版识别、备份升级与回滚 | `test_offline_update.py`、`scripts/test_build_offline.ps1` | 下方离线升级验收；模拟 Docker 不代替真实导入和数据恢复 |

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

### 外部真实视频性能测量

保持上述三个 APP 路径隔离到 `work/`，素材会复制到测试媒体目录，原文件只读。`real_video_benchmark.py` 测量当前精确帧冷/热读取，并以独立顺序解码的 JPEG 校验原始帧身份。预处理模式需要已准备的本地模型配置及锁定的 Transformers/Torchvision，使用真实 processor，但不加载模型权重；每次内存测量用独立进程，RSS 包含 Python/Torch/processor，不含模型推理。

```bash
work/review-venv/bin/python backend/tests/real_video_benchmark.py --source-dir "/实际视频目录"
work/review-venv/bin/python backend/tests/real_video_benchmark.py --source-dir "/实际视频目录" --processor --count 120 --video "某个视频.mp4"
work/review-venv/bin/python backend/tests/real_video_benchmark.py --source-dir "/实际视频目录" --pipeline --count 120
```

`--phase before --processor --count 8` 为旧整窗口预处理的同样本对照；勿用长高分辨率窗口冒险占满测试机内存。`--pipeline` 验证真实原视频解码、标准预处理、seed、原始帧号、框坐标及 JSONL 发布，但推理输出模拟，不能用作模型精度/GPU 耗时证明。结果写入忽略的 `work/performance-*.json`。

真实素材浏览器用例先生成 C/UX/B 基础夹具，再在同一 `/work/e2e-confirm-ux-*` 环境运行 `backend/tests/real_4k_browser_fixture.py --source-dir "/实际视频目录"`；生成的测试标注不代表该视频真实目标。随后运行 `CONFIRMATION_ORIGIN=http://127.0.0.1:5373 node frontend/tests/performance-browser.mjs`，会消费本批 C 决定；重新运行先生成新任务。截图/指标在 `output/playwright/performance-results.json` 和 `real-4k-performance.png`。
- `annotation-delete-feedback-browser` 前在同一隔离环境运行 `backend/tests/annotation_controls_fixture.py`，只重置六个 `controls-*` 媒体，凭证留在 `work/annotation-controls-browser-fixture.json`。环境路径支持 `/work/e2e-confirm-ux*` 或单独 `/work/e2e-annotation-controls*`；前端地址用 `ANNOTATION_ORIGIN`。覆盖当前帧/全视频删除、统计取消/确认、跨帧撤销、刷新、正常/仅本次/实际修框、尺寸误报无须重画的人工参照保存与恢复、校准重置、两类写入成功但响应丢失的原键重放、帮助下载一致及小窗口首屏。其后续 GPU job 响应模拟，保存/反馈/seed/rewind与原视频帧走真实后端；不能称真实模型验收。
- `test_tracking_adaptive_feedback.py`、`test_tracking_pause_policy.py`、`test_tracking_confirmation_policy.py`、`test_tracking_pause_publication.py` 使用真实检测与反馈持久化、合成框或模拟推理输出，验证普通风险的持续观察与方向切换、30 对象在 44.2 FPS 下的轻微变化、原形状往返、已确认正常形态、重启读取、跨轮首次风险发布、回退帧界限、活动参照损坏与重置恢复、逐渐丢尾、新对象对风险和极端缩框的成对路径。它们随后端全量入口执行，不代表真实 GPU 推理。前端 `pause-diagnostics.test.mjs` 与 `tracking-calibration.test.mjs` 随 `test:annotation` 验证服务端明确指标/界限的解释、软提示与触发指标的区分、多个 partner、重置显示与历史证据缺字段，不用它们替代实际浏览器确认流程。
- `training-export-browser` 会通过审查并导出 `ux-video`，重跑前重新生成 UX 与 C 夹具。
- 测试执行时不修改前端文件，避免 HMR 改变正在运行的实例。

Vite 调试脚本若需要访问 workspace，须导入浏览器实际已加载的模块 URL（包含可能的 `?t=`），不要无条件二次 `import('/src/stores/workspace.ts')` 建立独立实例。

结束后只停止自己启动的测试服务器；业务数据不清理。截图/控制台保存在 `output/playwright/`，命令日志放 `work/`。

### Chrome 93 / HTTP 专项回归

（2026-11-01：`browser-compat-media.mjs` 仍依赖早已移除的送审按钮，需更新后才能使用。）`browser-compat-media.mjs` 覆盖素材列表仅保留删除、本地图片删除范围提示、取消与刷新不复活，并使用真实上传的短 AVI、送审接口和删除接口；拒绝非 `work/` 夹具路径。沿用以上 B/C/UX 夹具，推荐独立数据根 `work/e2e-confirm-ux-compat-*`。启动后端 3307；前端用 `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=hospital-test.local API_PROXY_TARGET=http://127.0.0.1:3307 npm run dev --prefix frontend -- --host 127.0.0.1 --port 5377 --strictPort`。

```bash
# 库路径按前述 PLAYWRIGHT_MODULE 设置；COMPAT_VIDEO 可指定生成的 AVI。
node frontend/tests/browser-compat-media.mjs
# 重新生成 UX 与 C 夹具后再执行导出测试。
LEGACY_BROWSER=1 CONFIRMATION_ORIGIN=http://127.0.0.1:5377 node frontend/tests/training-export-browser.mjs
```

兼容配置禁用 AbortSignal.timeout、crypto.randomUUID、requestVideoFrameCallback；专项脚本另用仅在测试 Chrome 内解析到 127.0.0.1 的 hospital-test.local 验证真正的非安全 HTTP 上下文。覆盖上传、真实提帧、换帧、送审响应丢失后沿用原键、取消删除、删除失败保留、成功后立即刷新、过期缓存清理、A 快照保护。其余流程脚本可用 `LEGACY_BROWSER=1` 复用缺失 API 配置。它不是 Chrome 93 整个引擎的模拟，交付后仍需医院 Windows 7 / Chrome 93 客户端验收。

## 仓库与 Docker 交付验收

`python3 scripts/check_repository.py` 检查 Git 跟踪文件，拒绝业务数据、模型、本机密钥、冲突标记和重复 Python 顶层定义。它读取索引中的文件列表；新文件提交后同样受 CI 检查，不扫描用户运行目录内容。

容器测试使用实际生产 Dockerfile、Python 3.12 和隔离存储。CPU 容器覆盖基本流程，不能代替 CUDA/真实权重推理。以下为 POSIX 示例，必须使用未占用端口和新的测试数据目录，不指向生产数据：

```bash
export COMPOSE_FILE="$PWD/compose.yaml"
export COMPOSE_PROJECT_NAME=annotation-ci
export APP_DATA_ROOT="$PWD/work/docker-smoke-data"
export WEB_PORT=18080
export WEB_BIND_ADDRESS=127.0.0.1
export SMOKE_ORIGIN=http://127.0.0.1:18080
export JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export BACKEND_IMAGE=annotation-ci-backend:cpu
export FRONTEND_IMAGE=annotation-ci-frontend:local

docker build -f backend/Dockerfile -t "$BACKEND_IMAGE" .
docker build -f frontend/Dockerfile -t "$FRONTEND_IMAGE" .
docker compose config --quiet
docker compose -f compose.yaml -f compose.gpu.yaml config --quiet
docker compose up -d --no-build --wait --wait-timeout 180
python3 scripts/docker_smoke.py --fresh
docker compose down
docker compose up -d --no-build --wait --wait-timeout 180
python3 scripts/docker_smoke.py --verify-restart
docker compose logs --tail=100 backend frontend
docker compose down
```

`--fresh` 验证空数据库起步，已有测试数据时使用新隔离目录。脚本生成真实 AVI，验证注册、人工保存和查询（防止 db.py 参数回退）、工作区、B/C 显式完成、YOLO 图像/标签及重建后令牌/数据/训练包。只允许 loopback 地址和 `work/` 下数据；恢复凭据写入已忽略的 `work/docker-smoke-state.json`，不要上传它。

后端也在同一 Python 3.12 镜像中回归，隔离变量设置方法见 `.github/workflows/ci.yml`。Bash 初始化由 pytest 的模拟 Docker 用例验证随机密钥、重复运行和模式冲突保护；Windows CI 通过 `scripts/test_deploy.ps1` 做相同的 PowerShell 配置测试，不代表 WSL2/GPU 的实际服务器验收。

`.github/workflows/ci.yml` 在 PR/main 验证；`release-images.yml` 在版本标签推送后调用验证，再发布镜像。新增工作流需推送后实际运行，不能把本地配置校验说成 GitHub Actions 已通过。镜像需要在 Packages 确认访问权限。

### 完整离线升级验收

服务端工具回归使用模拟 Docker 和临时目录，制作端回归使用 PowerShell 与模拟 Git/Docker。它们不访问医院服务器、不使用业务数据；在仓库根目录运行：

```bash
APP_DATA_DIR="$PWD/work/offline-update-test-data" \
APP_DB_FILE="$PWD/work/offline-update-test-data/app.db" \
APP_STORAGE_DIR="$PWD/work/offline-update-test-storage" \
PYTHONPATH=backend work/review-venv/bin/python -m pytest backend/tests/test_offline_update.py -q
bash -n update-offline.sh
python3 scripts/check_repository.py
```

```powershell
.\scripts\test_build_offline.ps1
```

集成验收必须另建隔离 Compose 项目，沿用上方 `scripts/docker_smoke.py` 的新测试数据目录，使用已知基线镜像创建账号、素材、工作区、B/C/F 和训练包。新包按 [离线升级流程](DOCKER_DEPLOYMENT.md#医院内网可复用的离线升级流程) 构建；不能直接以可变本地源码替代指定提交快照。先执行 `--check` 并确认旧服务仍可用，再执行真实升级和 `--verify-restart`，核对镜像、版本状态与现有登录令牌/数据保留。必须另验证重复安装、坏包/未知旧版/运行配置变化拒绝、升级失败恢复、中断后 `--recover`，以及完成升级后的 `--rollback <编号> --restore-data`。回滚后核对恢复数据及回滚前数据副本；不得只凭 HTTP 200 判定保留了业务状态。

测试记录应明确区分模拟 Docker、真实 CPU 容器和真实 GPU 服务器。Windows PowerShell 脚本通过不代表 Windows Docker 已构建出 GPU 包；CPU 镜像通过不代表 Linux amd64 / NVIDIA L20 上的推理通过。GitHub Actions 结果以实际运行记录为准。

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

## 其他专项说明

逐次验证的通过数量写在提交信息或 PR 描述里，不写入本文；旧记录见 [历史目录](archive/README.md)。

### 自动审计与统计仓库联调

新增审计、脚本与离线升级用例：

```bash
AUDIT_INTEROP_DIR="$PWD/work/audit-tests/interop" \
APP_DATA_DIR="$PWD/work/audit-tests/data" APP_DB_FILE="$PWD/work/audit-tests/data/app.db" \
APP_STORAGE_DIR="$PWD/work/audit-tests/storage" PYTHONPATH=backend \
work/review-venv/bin/python -m pytest backend/tests/test_quality_audit.py backend/tests/test_export_audit_script.py backend/tests/test_training_export.py backend/tests/test_offline_update.py -q
```

interop 夹具生成真实 YOLO/COCO/双格式、多视频及重新确认前后的完整训练包和一份运维审计包。独立统计仓库检出到 `标注统计/` 时，将 `ANNOTATION_AUDIT_INTEROP_DIR` 指向该绝对目录，在统计仓库的 PYTHONPATH 下运行它的 pytest；两个进程不共用名为 app 的模块。没有设置联调路径时用例明确跳过。统计真实浏览器入口及运行方式见该仓库当前说明。

容器 smoke 可设置 `SMOKE_STATE_FILE` 指向本次隔离 work 目录，避免覆盖其他测试的令牌文件。CPU 容器生成训练集后，用 `export-audit.sh` 实际提取，再让统计 CLI 导入；测试包含只读连接、输出拒绝覆盖、失败无半包，以及升级/恢复时运维脚本一起还原。

### 点选头部

`backend/tests/test_head_click.py` 覆盖偏差点击、歧义、空/损坏候选、精确帧、认证、越界与不写工作区；固定 annotation-ux 浏览器组覆盖点击创建、撤销重做、重复点击、失败/损坏结果和切帧/切工具/离开页面的迟到响应，以及拖出画面后仍可缩放、微调和专注模式。浏览器推理响应为模拟，不代表真实模型质量。真实模型测试仅在训练服务器独立目录运行，本地只检查日志与图像；不要在用户业务数据上测试。

### 边缘入画发现

`backend/tests/test_edge_entry.py` 覆盖连续向内运动、初始/内部目标、静止/相机平移/低置信度/已有对象/向外运动/歧义/坏输入拒绝、跨轮候选恢复、SAM3提示接续不重复帧、稳定ID避开删除墓碑、实际JSONL补帧和推理失败保留旧结果。局部模型输出为模拟；真实模型仅在训练服务器独立目录核验，不能据这些测试宣称运动能准确识别精子。固定annotation-ux组检查开关及刷新保存，tracking-recovery组验证开关随原任务/重试正文传递；仍须完整七组浏览器回归。

### 集中审查兼容路径与其他专项

（兼容）专项 `test_concentrated_review.py` / `test_concentrated_review_extra.py`：先记录旧行为失败，再验证疑点分组、检测器缺失/重叠不等于判错、失败不得默认为检查完成、连续追踪不质量暂停、内部 AI 接续不造人工 seed、完成审查直接 F、事务失败/原键恢复、重开帧后旧 F 永久失效、旧 C 不能修改新接受版本。 当前前端不再发送 concentratedReview，这些用例只保护服务端兼容路径。

真实模型仅在服务器独立实验目录执行；短片段正反向成功只能证明算子与数据链路可运行，不表示疑点检出率、假阴性或医院验收通过。

标注直接导出健康路径不得出现“导出当前标注”确认层或额外勾选；后台保存、来源读取、快照创建后直接进入设置。仅未知帧显示声明空帧输入，故障显示具体错误和重试。快照已提交但响应丢失时刷新须保留锁并原键回放；重放优先于来源锁 / 视频读取，随后恢复已有 ZIP 任务。真实 ZIP 检查包括图片、空标签、人工覆盖、annotationOnly 来源与修改后旧包失效。浏览器模型仍为模拟，不声称真实 GPU 精度验收。

首尾重框回归：test_endpoint_duplicate_output.py 覆盖紧人工框/松AI框导致低IoU、连续时序恢复、自然双头竞争不合并、仅单帧重叠拒绝匹配、尾ID撞号、明确空尾帧、失败/历史策略保护，以及结果API/JSONL/导出/删除的一致性。实际冻结观察仅放 work/endpoint-duplicate-fix，标注为真实结果回放，不称为新模型推理或标注精度验收。

出画身份专项：`test_boundary_exit_identity.py` 覆盖四边退出、部分头仍可见、连续裁切运动后借到邻居、普通画内遮挡/重叠、转向、缺图像风险、缺帧、人工重锚与已匹配尾帧保护；旧错误尾对应的纠正、读取幂等/源文件不变及 API/JSONL/导出一致性。运行时使用独立 APP_DATA_DIR/APP_DB_FILE/APP_STORAGE_DIR 与 PYTHONPATH=backend，再运行 pytest 的该文件及 endpoint_tracking、endpoint_duplicate_output、joint_tracking 三个专项。`tracking-result.test.mjs` 验证同一哈希下按原始尾编号纠错而不移动人工框；固定七组中的 endpoint-tracking-browser 新增保存过旧 canonical ID 后的真实刷新、写回、再次刷新与一框显示。服务器真实输出回放在隔离目录执行，保留原始文件SHA及第11–14帧相关对象前后框；不能称为重新执行SAM3或完整医院验收。

检测权重交付：test_bundled_head_detector.py 校验默认资源存在、尺寸 / SHA256 与固定清单一致、Git LFS 指针 / 损坏文件被拒绝、自定义路径优先、四个检测入口共享解析。校验只读字节，不在本机运行模型推理。新 Git checkout 和 Docker COPY 打包验证须核对实际权重字节，不把路径存在当作完整模型。
