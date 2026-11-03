# 专项测试说明

按功能列出各专项测试覆盖的场景与限制。先在 [TESTING.md](../TESTING.md) 的“按修改选择测试”表找到对应修改面，再只读本文相关一节。

## 审查与导出

当前流程为标注 → 审查 → 导出。审查是标注页的审查模式；旧审查页、对比确认页及其浏览器测试已删除（A/B/C/F 数据层的后端测试仍保留，因为统计/审计导出读取这些历史表）。新主流程必须从真实画框、首尾追踪、在审查页逐帧确认并通过，到下载 ZIP。

- `test_annotation_review.py`：首轮列出全部帧、重新审查只列改动帧（帧外确认 404）；按帧确认的结果校验（有框只能 ok、未知帧只能 empty、已知空帧 empty 记为 ok、可重复确认）；全部帧确认且未知帧确认无目标才能通过；通过时的修改记录与确认人、通过后改动变为 stale、未列入重审的帧沿用上次无目标确认；旧清单进行中轮次改建为帧清单并归档旧检查、旧已通过轮次的无目标确认仍作导出门槛；未保存工作区返回 notReady、缓存按 revision 失效、HTTP 往返与错误码。
- 新训练包不含 `review/` 目录与 `manifest.reviewDecisions`。 `test_training_export.py` 的合并脚本用例验证混合多视频包与新版重复视频的两种输入顺序：只保留实际选中视频版本的判断，不重复追加其他视频的行，并拒绝判断文件缺失或行数不符。
- `test_endpoint_review_tolerance.py`：首尾端点几何容差随原图分辨率缩放一致；旧清单改建失败时事务回滚、不丢已保存项，修复后可恢复。
- `test_annotation_export.py`：导出快照先要求审查通过（REVIEW_REQUIRED），审查确认无目标的帧作为显式空帧；来源完整性、版本/账号、原键重放、真实原视频 ZIP 与编辑后旧下载撤销。
- `test_media_reimport.py` / `test_tracking_restart.py`：旧审查历史不再锁住覆盖重标和重新追踪。

多人协作专项：`workspace-writes.test.mjs` 使用实际写入器验证视图不写、三方不同帧合并、同框/删除冲突、基准唯一性、原键重放、账号隔离与 generation 拒绝。固定 `workbench-consistency` 末尾用两个独立浏览器账号在真实后端拖框，验证浏览不推进版本、不同帧合并、同框冲突、刷新草稿恢复和选择后不复发、无强制 JSON 下载；760/390 浅深主题截图。大草稿 IndexedDB 跨刷新为缓存组件测试，不等于大量对象模型验收。`endpoint-tracking-browser` 的重读不再等待下载事件。

## 追踪、暂停与身份




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


逐次验证的通过数量写在提交信息或 PR 描述里，不写入本文；旧记录见 [历史目录](../archive/README.md)。

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

（兼容）专项 `test_concentrated_review.py` / `test_concentrated_review_extra.py`：先记录旧行为失败，再验证疑点分组、连续追踪不质量暂停、内部 AI 接续不造人工 seed、完成审查直接 F、事务失败/原键恢复、重开帧后旧 F 永久失效、旧 C 不能修改新接受版本。 当前前端不再发送 concentratedReview，这些用例只保护服务端兼容路径。

真实模型仅在服务器独立实验目录执行；短片段正反向成功只能证明算子与数据链路可运行，不表示疑点检出率、假阴性或医院验收通过。

标注直接导出健康路径不得出现“导出当前标注”确认层或额外勾选；后台保存、来源读取、快照创建后直接进入设置。仅未知帧显示声明空帧输入，故障显示具体错误和重试。快照已提交但响应丢失时刷新须保留锁并原键回放；重放优先于来源锁 / 视频读取，随后恢复已有 ZIP 任务。真实 ZIP 检查包括图片、空标签、人工覆盖、annotationOnly 来源与修改后旧包失效。浏览器模型仍为模拟，不声称真实 GPU 精度验收。

首尾重框回归：test_endpoint_duplicate_output.py 覆盖紧人工框/松AI框导致低IoU、连续时序恢复、自然双头竞争不合并、仅单帧重叠拒绝匹配、尾ID撞号、明确空尾帧、失败/历史策略保护，以及结果API/JSONL/导出/删除的一致性。实际冻结观察仅放 work/endpoint-duplicate-fix，标注为真实结果回放，不称为新模型推理或标注精度验收。

出画身份专项：`test_boundary_exit_identity.py` 覆盖四边退出、部分头仍可见、连续裁切运动后借到邻居、普通画内遮挡/重叠、转向、缺图像风险、缺帧、人工重锚与已匹配尾帧保护；旧错误尾对应的纠正、读取幂等/源文件不变及 API/JSONL/导出一致性。运行时使用独立 APP_DATA_DIR/APP_DB_FILE/APP_STORAGE_DIR 与 PYTHONPATH=backend，再运行 pytest 的该文件及 endpoint_tracking、endpoint_duplicate_output、joint_tracking 三个专项。`tracking-result.test.mjs` 验证同一哈希下按原始尾编号纠错而不移动人工框；固定七组中的 endpoint-tracking-browser 新增保存过旧 canonical ID 后的真实刷新、写回、再次刷新与一框显示。服务器真实输出回放在隔离目录执行，保留原始文件SHA及第11–14帧相关对象前后框；不能称为重新执行SAM3或完整医院验收。

检测权重交付：test_bundled_head_detector.py 校验默认资源存在、尺寸 / SHA256 与固定清单一致、Git LFS 指针 / 损坏文件被拒绝、自定义路径优先、四个检测入口共享解析。校验只读字节，不在本机运行模型推理。新 Git checkout 和 Docker COPY 打包验证须核对实际权重字节，不把路径存在当作完整模型。
