# 固定浏览器套件内容

七组固定回归各自覆盖的流程与断言。只有修改某套件或排查其失败时才需要读。返回 [TESTING.md](../TESTING.md)。

## 各套件覆盖

`independent-integrity` 保留独立审查中的行为断言：坏结构的成功响应、完整结果撤销后的 SPA 重读、跨素材迟到失败/成功、真实编码 4K 与 50,000 合成对象的读入/保存/刷新。另验证匿名/无效凭据不能读视频，HTTP 下真实 H.264 原生播放和 Range 正常，退出后不能读；真实保存已提交但响应丢失，再遇 `/auth/me` 500/网络失败/401，检查原请求保留、同账号重放与恢复后再次保存。首次新上传还从真实 Tracking/异常检测进入两个对象暂停，一次整帧正常确认后继续发布结果；只有模型预测被模拟。

同组调用 `tracking-policy-checks.mjs`，新上传 8 帧视频并在真实画布画框，不导入 store 或预置工作区：普通高度约 0.64 倍持续观察后返回首次风险帧，显示确切指标/界限/参照与决定帧，数值按保存的诊断核验而非假设画框像素舍入后仍恰为 0.64；整帧确认同时包含未报异常对象，确认响应丢失后刷新沿用原请求恢复，轻微变化续追至末帧、刷新仍保留检查点；人工 seed 已有稳定重叠不中断；三个对象新增重叠保留全部 partner，一次整帧确认后续追；纯重叠确认有重置入口，重置关联对象对但保留其他独立组合和历史；极端缩框仍立即暂停。刷新前等待真实帧导航和自动保存游标完成，不能把旧游标恢复当成确认失败。检查两种窗口、浅深主题和专注模式的确认按钮及逐帧导航首屏可见性。仅模型框输出使用显式 `policy-*` 测试名称驱动，原视频读取、异常检测、反馈与结果发布均走真实后端；不是实际 GPU 精度验收。

该合成负载设置宽松 CI 停滞门槛：全量结果读取 <8 s、首次恢复 <15 s、同步人工缓存 <64 KiB、最长主线程任务 <1.5 s，并保留实测耗时。这是隔离回归预算，不是医院 GPU、GB 视频或内网延迟的 SLA。慢盘测试以可释放的哈希阻塞注入证明同事件循环的健康请求与其他媒体保存仍在 2 s 内完成；不能将它称为真实医院慢盘实测。

后端 `test_independent_integrity_audit.py` 保留独立审查用例；`test_integrity_recovery.py` 补 seed 临时写/替换失败、坏历史来源、合法点与像素边界、同事件循环慢盘上传时读取/另一视频写入、查重途中删除的竞态、长期反馈回执体积及重置后的准确重放。前端 `tracking-result.test.mjs` 通过 `npm run test:annotation` 校验结构故障组合，不能只靠静态源码断言。规范 ID 校验后，路径探针直接构造不安全目标；原子 seed 修复后，磁盘故障注入同时覆盖目标和临时文件，保留所有旧数据与错误断言。

`critical-workflows-browser.mjs` 不导入前端 store，也不预先生成工作区、AI 结果或审查记录：真实登录 → 上传原视频 → 画框保存/刷新 → 保存成功但响应丢失 → 刷新沿用原键/原正文重放 → 再次画框保存/刷新 → 删除重传 → 取消/覆盖重标 → 追踪 → 导出被锁 → 去审查（注入开始失败后重试）→ 逐项检查 → 审查通过 → 快照响应丢失后刷新原键重放 → 下载真实 ZIP → 审查页内改框使通过失效并锁住导出。夹具只产生输入视频。CI 还在实际生产前端镜像上运行同一用例的 CPU 模式，并启用旧浏览器 API 兼容检查，避免只验证 Vite 开发页面。

`training-export` 固定套件同时执行 `statistics-export-checks.mjs`：从真实 A/B/C/F 数据生成全量统计 ZIP，核验文件校验和、空帧和历史/当前 F、密码与原视频排除；验证匿名/跨账号下载拒绝、创建提交后响应丢失及刷新原键重放、查询失败恢复、下载原包重试、过期重新生成，并检查 1366×768 / 1180×760 浅深主题、弹窗焦点和共用使用说明。它使用 Chrome 93 API 兼容配置，不执行模型推理。统计 ZIP 后端专项用例见 `test_statistics_export.py`。

`tracking-recovery` 同时执行 `tracking-restart-checks.mjs`，在隔离 4K 原视频的较早帧修正，验证取消保留分支、暂停帧不能绕过、回退失败原键重试、成功后 seed 使用早帧原像素、未来人工框经保存/刷新保留、旧暂停不复活，以及发布成功但响应丢失后刷新重放不重复截新模型结果。恢复期间的送审状态查询失败保留原起点，后来已送审的合法回执重放不得发 seed 或模型请求。模型输出模拟，分支/工作区/收据走真实后端。`test_tracking_restart.py` 验证逐文件发布和提交故障、重启及读取恢复、已送审/轮次/版本边界、回执准确重放与持久暂停保护；包括首次工作区尚不存在的暂停写入失败、持续故障与读取恢复、legacy 人工 seed 保留、文件和目录同步失败的成对恢复。原反馈与删除/撤销用例继续运行，不能只验证新入口能点。

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
- `training-export-browser` 会通过审查并导出 `ux-video`，重跑前重新生成 UX 与 C 夹具。通过后在记录页核对审查记录与明细，并验证读取失败不显示为空、恢复后可用。 同套件覆盖放大图的真实异步图片 load 与同元素换源、采用中切视频、连按快捷键保护、轨迹已保存但检查失败后重试不重复加框。`annotation-view-guards.test.mjs` 执行实际审查 store 和工作区动作，覆盖轮次绑定、迟到响应隔离、全视频编号冲突、待保存草稿恢复和编辑锁定的明确返回。
- 测试执行时不修改前端文件，避免 HMR 改变正在运行的实例。

Vite 调试脚本若需要访问 workspace，须导入浏览器实际已加载的模块 URL（包含可能的 `?t=`），不要无条件二次 `import('/src/stores/workspace.ts')` 建立独立实例。

结束后只停止自己启动的测试服务器；业务数据不清理。截图/控制台保存在 `output/playwright/`，命令日志放 `work/`。

### Chrome 93 / HTTP 专项回归

（2026-11-01：`browser-compat-media.mjs` 仍依赖早已移除的送审按钮，需更新后才能使用。）`browser-compat-media.mjs` 覆盖素材列表仅保留删除、本地图片删除范围提示、取消与刷新不复活，并使用真实上传的短 AVI、送审接口和删除接口；拒绝非 `work/` 夹具路径。沿用以上 B/C/UX 夹具，推荐独立数据根 `work/e2e-confirm-ux-compat-*`。启动后端 3307；前端用 `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=hospital-test.local API_PROXY_TARGET=http://127.0.0.1:3307 npm run dev --prefix frontend -- --host 127.0.0.1 --port 5377 --strictPort`。

```bash
# 库路径按 TESTING.md“真实浏览器复现”中的 PLAYWRIGHT_MODULE 设置；COMPAT_VIDEO 可指定生成的 AVI。
node frontend/tests/browser-compat-media.mjs
# 重新生成 UX 与 C 夹具后再执行导出测试。
LEGACY_BROWSER=1 CONFIRMATION_ORIGIN=http://127.0.0.1:5377 node frontend/tests/training-export-browser.mjs
```

兼容配置禁用 AbortSignal.timeout、crypto.randomUUID、requestVideoFrameCallback；专项脚本另用仅在测试 Chrome 内解析到 127.0.0.1 的 hospital-test.local 验证真正的非安全 HTTP 上下文。覆盖上传、真实提帧、换帧、送审响应丢失后沿用原键、取消删除、删除失败保留、成功后立即刷新、过期缓存清理、A 快照保护。其余流程脚本可用 `LEGACY_BROWSER=1` 复用缺失 API 配置。它不是 Chrome 93 整个引擎的模拟，交付后仍需医院 Windows 7 / Chrome 93 客户端验收。
