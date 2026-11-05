# 当前接口与数据契约

> 要点
> - 当前路由见“路由概览”；旧 `/api/review/*`、`/api/confirmation/*`、`/api/final-versions/*` 已删除。
> - 业务写请求：Bearer + `Idempotency-Key`（审查/导出另需 `X-Review-Contract`）；未知结果用原键原正文重试，409 版本冲突须重新读取。
> - 帧号从 0 开始；工作区坐标 0–100 百分比，追踪/证据为原图像素 xyxy。
> - 旧暂停兼容路径的接口细节在 [details/tracking-pause-policy.md](details/tracking-pause-policy.md)。

## 路由概览

标注 → 审查 → 导出。审查与旧 A/B/C/F 无关；旧 `/api/review/*`、`/api/confirmation/*`、`/api/final-versions/*` 已删除（说明见 [archive](archive/旧审查与对比确认流程_2026-11-01前.md)），对应数据表只读保留供统计/审计导出。实现：[annotation_review_routes.py](../backend/app/annotation_review_routes.py)、[annotationReviewApi.ts](../frontend/src/api/annotationReviewApi.ts)。均需登录；审查接口无需幂等头，重复调用天然幂等。

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/annotation-review/media/{id}` | `status: none/open/passed/stale`、`currentRevision`（逐帧内容摘要）、`unknownFrames`、`stage`（`status=none` 时为 `unstarted/annotating/ready`）、当前或最近一轮 `review{reviewId,round,state,lastActorId,lastActorName,lastActivityAt,changedFrames:[[start,end]],frames:[[start,end]],checkedFrames:[[start,end,'ok'|'empty']],checked,total,passedBy,passedAt,changes}`、`lastPassed`。`frames` 是本轮要检查的帧（首轮全部帧，重新审查只含改动帧，闭区间、原始帧号从 0 开始）；旧版清单的已通过轮次返回空 `frames`。`lastActorId` 取最近确认人，无确认时为开始人；`lastActivityAt` 同理。从未保存工作区时返回 `none` 与 `notReady` 说明，不报错。Tracking 运行中也可读取 |
| `POST /api/annotation-review/media/{id}/start` | 已有进行中的轮次则原样返回；已通过且内容未变返回 409 `REVIEW_ALREADY_PASSED`；Tracking 运行中 409 `SOURCE_BUSY` |
| `PUT /api/annotation-review/media/{id}/frames/{frameIndex}` | `{reviewId, result:'ok'|'empty'}`，确认一帧，可重复确认（以最后一次为准）。服务端按当前工作区校验：有框的帧只能 `ok`；未知帧（无任何标注行）只能 `empty`，提交 `ok` 返回 422；已知空帧提交 `empty` 记为 `ok`。帧不在本轮或超出视频 404 `ITEM_NOT_FOUND`，非当前轮 409 `REVIEW_NOT_CURRENT`，已结束 409 `REVIEW_CLOSED`。旧 `/items/{itemId}` 已删除 |
| `POST /api/annotation-review/media/{id}/pass` | `{reviewId}`；有帧在确认后被改动 409 `REVIEW_FRAME_CHANGED`，有未确认帧 409 `REVIEW_INCOMPLETE`，仍有未确认无目标的未知帧 409 `UNKNOWN_FRAMES`；成功固定 `passed_revision`、确认无目标的帧和修改记录 |
| `GET /api/annotation-review/summary` | `{items:{mediaId: status}, stages:{mediaId: unstarted/annotating/ready}}`，列表用，不读追踪文件；`stages` 只含 `status=none` 的视频 |
| `POST /api/annotation-review/media/{id}/reset-annotations` | 原 `/api/review/media/{id}/reset-annotations` 迁移至此，正文与幂等语义不变；不再因历史送审拒绝 |
| `GET /api/datasets/annotations/{id}/preview` | 导出弹窗预览（取代 `completion-preview`）：`sourceRevision`、有框/空/未知帧，审查确认无目标的帧计为空 |
| `POST /api/datasets/annotations/{id}/snapshot` | 同前；另要求最近一次审查通过且内容未变，否则 409 `REVIEW_REQUIRED` |

本页是 API 地图与不变量，不复制全部类型定义。精确请求验证以已注册路由的 Pydantic 模型为准；开发时可读本地 FastAPI `/openapi.json`。旧原型中的示例接口不是活动接口。

记录帧确认的请求显式绑定开始时的 mediaId/reviewId 与帧号，切换视频或轮次后不发送旧操作的确认。旧审查采用的工作区人工框仍保留 `origin=review_adopted` / `reviewAdoptionKey`，只作历史来源，不再产生。

客户端多人协作仍使用现有工作区 GET/PUT 写入，不增加绕过版本检查的入口。`GET /api/track/workspace/{mediaId}/stamp` 是只读轮询：`{revision,generationId,updatedBy,updatedByName,trackingStamp:[mtimeNs,size]|null,tracking:{userId,userName,status}|null}`；不持全局来源锁、不执行恢复日志，按文件 mtime/大小缓存已解析版本，文件正在替换时返回 503。权威内容仍以完整 GET 为准。新版普通保存仅提交人工框、人工基准、删除规则和 generation；观看状态在本机独立保存。工作区版本 409 的自动合并只基于独立读取基准及最新 GET；每次重算正文后使用新幂等键。反馈/回退等专用接口仍原键恢复，不自动重写。详见 WORKFLOW 的多人协作保存。

## 追踪与审查证据协议

- 审查状态、开始审查与帧确认可复用进程内解析缓存：键为 `(mediaId, 工作区revision/追踪文件标记)`，最多 8 个，按最近使用淘汰；通过审查与导出门槛不读缓存，始终重新加载当前内容。
- 审查不读取追踪结果里的 `reviewEvidence`（逐帧人工检查，见 WORKFLOW 审查小节）；下列证据只服务追踪自身（端点对应、出画保护、综合标注）与排错。

- `POST /api/track` 新增 `combineResults:boolean`（默认 false 兼容旧请求）。新页面的首尾追踪固定 true，并携带人工 terminalAnnotations、reviewMode=true、startFrame=0。缺少端点时422；该字段进入幂等正文，不在重试时补写或切换。
- true 时正向、人工尾帧反向及头部检测器共同生成实际输出；任务成功需同时有 `endpointVerification` 与 `jointTracking.state=checked`。jointTracking 只含状态、policy/version、checkedFrames、changedObjects、recoveredObjects、reviewCount等摘要，不增加画面上的采用理由。前端收到旧后端的仅核验结果必须报错，不能冒充综合完成。
- 检测器依赖/权重、原图读取或数据结构失败：task.status=failed，jointTracking.state=failed，保留正向已发布结果及失败疑点；局部无检测候选是明确空观测，区别于检测失败。`head_click.detect_regions` 仅关联已知追踪身份，不自动新增内部检测。
- 最终 `objects[].bbox` 是唯一综合几何，source 内部可为 joint_tracking、score/mask_area 在修正后清空过期值；读取 API 仍统一映射 source=ai。人工覆盖、删除过滤、stable objectId 和训练集准入不变。reviewEvidence.jointCheck 记录检查状态与不确定对象，审查 evidence 接口新增简短 jointTracking 状态；已修正且有支持的旧位置疑点不继续当作当前错误。
- 全量结果读取 envelope 同样返回 jointTracking 状态：所有帧均有 checked 才表示整段综合完成，部分缺失或失败返回 failed 和实际 checkedFrames。客户端刷新后仍展示未完成提示并保留几何可查看，不把失败成果当成空数据，也不冒充综合成功。成功重试后替换失败标记。

- `POST /api/track` 可选 `terminalAnnotations: object[] | null`：省略/null 保留当前帧单向追踪；数组（含明确空数组）启动人工首尾核验，要求 `startFrame=0`、`reviewMode=true`。对象使用原图像素 bbox、正整数 object_id、source=manual，frameIndex 若提供必须为真实尾帧。首尾快照必须与已保存 manualAnnotations 完整一致；来源/几何/帧/重复身份错误返回422。尾帧ID是独立端点编号，不必等于首帧ID；不得依据相同编号推断对应。参数包含在原请求哈希和幂等重放中。
- 任务状态的 `checking` 覆盖反向/综合阶段；`message` 显示处理进度。完成时可附 `endpointVerification`：state=checked/failed/insufficient、anchorKind、anchorFrame、anchorHash、matches（尾帧局部ID字符串→正向ID）、unmatchedTailIds、checkedFrames、策略与阈值。checked表示执行完比较，不能作为自动验收。旧仅核验请求可保留正向成功与反向失败证据；新综合请求不能把反向执行失败报告成综合成功，审查完成规则不变。
- JSONL `reviewEvidence.endpointCheck` 保存证据，末帧另保存 terminalAnnotations 原始人工快照。审查 evidence 接口可返回 `endpointVerification`；疑点新增 endpoint_disagreement / endpoint_identity_uncertain，供原有片段队列展示。结果显示接口抑制已被人工尾框替代的匹配 AI 框；旧仅核验请求保留正向几何，新综合请求保存唯一综合几何，A 合并时按唯一对应采用人工几何。
- 全量结果 envelope 可带 `terminalIdentity={frameIndex,anchorHash,matches}`。前端严格校验其帧号/哈希/一对一正整数映射后，只对对应尾帧人工框统一身份，保留 endpointOriginalObjectId / endpointAnchorHash；同一哈希按原始尾编号应用当前对应，结果相同不重复转换，明确错配纠正后可更新 canonical ID。该人工工作区修改沿用原保存队列/版本校验，不改原始几何。冻结审查证据保留原始 terminalAnnotations，不依赖后续可变工作区。
- 综合输出可附 `reviewEvidence.boundaryExit={policy:'boundary-exit-v1',exitedObjectIds,findings}`：已确认边界退出的AI身份停止出框，原相关诊断归档于 findings，不继续作为当前活动风险；不写工作区删除或正常确认。历史读取、JSONL下载、训练来源共用该只读投影，缺少有效原图尺寸不推测边界。身份重关联策略记录为 associationPolicy=first-anchor-v2，无新增客户端写入权限或数据库迁移。

- `POST /api/track` 新增 `reviewMode`（默认 false 兼容旧客户端，新页面发送 true）。true 时质量风险不暂停，按原单段上限自动续跑；状态新增 `checking`。模型/持久化错误仍 failed。

## 通用协议

活动业务接口使用 `Authorization: Bearer <token>`。审查与训练导出的业务写请求还要求 `X-Review-Contract`（服务端接受 `2` / `3`，当前前端发送 `2`，见 `backend/app/api_common.py`）与 `Idempotency-Key`。同一次未知结果/网络失败重试使用**相同 key、相同请求体**；用户新的业务意图才生成新 key。

原视频也必须登录。为支持原生 `<video>` 与 Range 请求，登录/注册/`GET /api/auth/me` 同时签发仅用于 `/api/track/video/` 的 HttpOnly、SameSite=Strict 播放 Cookie；HTTPS 使用 Secure，医院 HTTP 环境仍可播放。它不能作为工作区或其他业务接口凭据，也不放入 URL。显式无效 Bearer 不被旧 Cookie 掩盖。`POST /api/auth/logout` 清除播放 Cookie；启动先校验身份再挂载工作区。401 才清除失效身份，超时/500 保留账号及待重试请求并允许重新验证。

服务端以 `BEGIN IMMEDIATE`、期望版本及成功回执保护写入。回执重放先于版本校验；业务数据与回执同时提交/回滚。帧、视频、决定和游标各自有版本；游标更新不改变业务完成状态。

错误包含 `message`、`code`、`requestId`（亦可从 `X-Request-ID` 获取）。401 处理登录；409 处理冲突并保留意图；422 不合法参数；428 契约需要更新。不要统一吞掉错误返回空数据。具体错误以路由为准。

`GET /api/health` 提供进程健康和 `sam3.enabled/modelLoaded`。Docker 默认安装启用 GPU + AI；显式 CPU 基础模式设置 `SAM3_ENABLED=false`，`POST /api/track` 和 `/api/track/rewind` 返回明确 503（不会先删除已有追踪分支）；本机开发默认仍启用。健康正常不等于 GPU/权重推理已验证。

## 媒体、标注与追踪

实现：[main.py](../backend/app/main.py)、[trackApi.ts](../frontend/src/api/trackApi.ts)、[httpAnnotationApi.ts](../frontend/src/api/httpAnnotationApi.ts)。

| 路径 | 用途 |
| --- | --- |
| `POST /api/auth/register`、`/api/auth/login`；`GET /api/auth/me` | 真实账号与 JWT |
| `POST /api/track/upload`；`GET /api/track/media` | 原视频上传、可用媒体 |
| `POST /api/track/import-package` | multipart `file`：含原视频的训练数据集 ZIP。201 `{imported:[{mediaId,videoName,…,objects,emptyFrames}],skipped:[{videoName,reason,mediaId?}]}`；非 ZIP 或非本工具 manifest v2 返回 422，超过 `MAX_PACKAGE_BYTES`（默认 8 GiB）返回 413。规则见 WORKFLOW“导入标注包” |
| `DELETE /api/track/media/{mediaId}` | 把视频目录移入回收目录 `_trash/`；需要登录。成功返回 `deleted: true, mediaId`；AI 运行、基准引用、新旧存储同名目录返回 409；文件系统失败返回 500 并记录日志，不能伪报成功 |
| `GET /api/track/video/{mediaId}`、`/api/track/frame/{mediaId}/{fi}` | 原视频、真实指定帧 |
| `GET / PUT /api/track/workspace/{mediaId}` | 可恢复工作区 |
| `GET /api/track/deletion-preview/{mediaId}/{objectId}` | 整视频删除预览，按稳定 ID 合并人工和 AI 同帧框，返回真实有效数量 |
| `POST /api/track/feedback/{mediaId}` | 显式正常/已修正/重置判断；版本校验和原键重试 |
| `POST /api/annotation/annotations/manual` | 保存人工对象记录 |
| `GET /api/annotation/projects/{projectId}/results` | 人工标注记录查询 |
| `POST /api/track/annotations`、`/api/track/rewind`、`/api/track` | seed、回退、发起追踪 |
| `POST /api/track/restart-branch/{mediaId}` | 异常暂停后，从更早帧修正并显式重建分支；版本与原键恢复，成功后才续追 |
| `GET /api/track/status/{taskId}`、`/api/track/result/{mediaId}` | 追踪状态与对象 |
| `GET /api/track/result-file/{mediaId}`、`/api/track/overlay/{mediaId}` | JSONL 与按需生成的完整带框视频；预览缓存以当前视频/追踪/删除规则校验，生成时输入变化返回 409，失败 503 不影响已保存 JSONL |

`AnnotationObject` 定义在 [annotation.ts](../frontend/src/types/annotation.ts)：界面 `id` 与稳定 `objectId` 不等价，`frameIndex` 为实际 0 起帧号；工作区 bbox 为百分比 `{x,y,width,height}`。seed / Tracking / B/C 使用原图像素 xyxy，转换不能重复进行。追踪结果文件虽后缀 `.json`，实际逐行 JSONL。

`GET /api/track/result/{mediaId}` 区分结果生命周期：源视频存在但尚未生成结果时返回 200 `{format:"sam3-tracking-results-jsonl",state:"not_generated",frames:[],count:0}`，不生成文件、不声明空帧；全量结果存在时为 `state:"available"`，有效零对象帧仍在 frames 中。可选 `required=true` 用于追踪任务成功/暂停后的读取，此时缺失返回 409 `TRACKING_RESULTS_MISSING`。服务端记录已发布/成功读取过的结果，之后文件丢失同样返回 409；送审预览及送审也拒绝这个状态，不能用显式空帧确认绕过。媒体缺失返回 404 `MEDIA_NOT_FOUND`。格式/对象几何损坏返回 500 并记录日志，不忽略坏框后伪造空结果。客户端不能将所有 404 或所有异常统一忽略。

素材列表增加 `trackingResultState:'not_generated'|'present'|'missing'`；`hasTrackingResult` 分别为 false / true / null。null 明确表示已知结果丢失，不能当作尚未追踪。列表只查询文件/标记状态，不为列出素材读取全部 JSONL；`present` 表示文件存在，不代表内容已验证。详情和原始下载再校验格式、对象身份及几何。

`GET /api/track/result-file/{mediaId}` 在健康未追踪时返回 404 `TRACKING_RESULTS_NOT_GENERATED`（没有可下载文件），媒体缺失为 404 `MEDIA_NOT_FOUND`，已知结果丢失为 409 `TRACKING_RESULTS_MISSING`；可选 `required=true` 时尚未生成也为 409。损坏结果返回 500，不能下载坏框充当正常 JSONL；有效零对象结果仍可下载。列表、详情和下载遵守同一结果生命周期，404 不是可以统一忽略的成功响应。

工作区 GET 返回 `revision`（旧工作区首次为 0）；新版 PUT 携带 `expectedRevision` 和 `Idempotency-Key`，返回新的 `revision`。未知保存结果必须沿用原键和原正文，成功回执重放先于版本校验。无版本的旧客户端仍可保存普通编辑，但不能变更新删除规则，也不能覆盖服务端反馈、样本和回执。新前端始终使用版本和幂等键。

工作区写入在提交回执前校验人工对象：正整数稳定 ID、同帧身份唯一、原始帧号在视频范围内、百分比框及点为有限数值并在 0–100 内；人工基准使用原图像素 xyxy。合法点标注仍允许。非法输入返回 422，不改变文件、版本或回执。seed 同样校验规范 mediaId、仍存在的原视频及帧/像素框，以临时文件原子替换；删除后的迟到 seed 返回 404，不重建孤立目录，写入/替换失败保留旧来源并记录日志。

历史 Tracking 的 `object_id` / `objectId` / `sam3_object_id` 统一转换为稳定 `object_id`；存在真实 `source_frame_index` 时它优先于模型内部 `frame_index`。详情、下载、删除、审查及导出采用同一转换，重复或矛盾的稳定身份不能静默接受。客户端也校验成功响应的数组、计数、帧、身份与几何结构，HTTP 200 不等于数据有效。完整重读替换 AI 集合，保留人工修改，不保留已撤销分支的旧 AI 框。

旧无版本请求携带的 `deletedTrackingIds` 只能是当前已有历史删除标记的子集（也可省略）；服务端保留完整当前集合，不删除既有标记。尝试新增历史删除标记同样返回 428，不能利用旧字段绕过版本校验。带版本及幂等键的当前客户端可正常删除和撤销。

`POST /api/track` 新前端另携带 `Idempotency-Key`，同账号原键/输入在当前后端进程内返回同一个 taskId，原键用于不同输入返回 409；覆盖轮次仍校验，不能借回执绕过 generationId。旧无键客户端保持兼容。状态 GET 含 `stage`（queued/decoding/preparing_model/tracking/checking/saving_results/paused/completed）、处理帧数与原始 `lastProcessedFrame`，不暴露请求 hash/key。首尾双向追踪的 `checking` 阶段另带结构化位置：反向核验 `phase=backward`、`phaseFrom`（尾帧）、`phaseTo`（首帧）、`phaseCurrent`（正往回处理的帧）；综合标注 `phase=combine`、`phaseFrom=0`、`phaseTo`（末帧）、`phaseCurrent`（当前帧），均为从 0 开始的原始帧号。前端进度条只用这些字段和 `lastProcessedFrame`，不解析 `message` 文本。模型失败在任务状态返回 failed 与具体 message，短 HTTP 超时不等于后台任务已失败。追踪只锁定所属视频：该视频已有排队或运行任务时再次 `POST /api/track` 返回 409，其他视频可以提交并排队（GPU 单 worker 依次执行，状态为 `queued`）。该视频的工作区浏览状态可在追踪中保存；改人工框、基准或删除规则返回 409，其他视频不受影响。rewind 成功的 `overlayRegenerated` 为 false，不重编码源视频；结果发布原子完成。普通 rewind 将结果截断与被丢弃未来的身份/关系认可退休一起纳入两文件恢复日志，API 使用真实账号的内部 SQL 回执判断提交；不递增人工工作区 revision，不导致随后正常保存/续追的版本冲突。

删除规则是 `deletedObjectIds:number[]`（整视频）和 `deletedFrameObjects:[{objectId,frameIndex}]`（单帧）；`deletedTrackingIds` 仅保留历史兼容。规则同时作用于人工/AI、seed、追踪读取、人工记录查询、审查与导出。单帧删除不关闭后续该对象的追踪；整视频删除不允许该对象进入后续 seed。原追踪文件不物理抹除，撤销通过恢复工作区及移除对应删除标记实现。A/B/F 不随删除或撤销改变。

删除预览前必须保存当前工作区。响应为 `{mediaId,objectId,name,revision,totalCount,frameCount,manualCount,aiCount,firstFrame,lastFrame}`；同对象同帧人工覆盖 AI 只算一框；帧号 0 起，无框时首末帧为 null。确认删除时仍用当前工作区版本，不能把预览数量当无需校验的删除许可。

反馈请求为 `{expectedRevision,objectId,frameIndex,decision:'normal'|'corrected'|'reset',calibrate:boolean,scope?:'object'|'frame'}`，使用 `Idempotency-Key`，与工作区共享版本。`reset` 不要求帧号，清该对象活动运动样本并使已确认尺寸及身份参照失效，保留审计。`normal` 只在明确勾选校准且持久化追踪结果包含位移依据时生成 `normalMotionSamples`；每条为 `{objectId,frameIndex,reason:'motion',decision:'normal',calibrate:true,features:{motionNormalized}}`。数值取自服务端追踪数据，不接受客户提供阈值。已保存人工修框不学习旧 AI 位移。`corrected` 要求当前帧已保存的人工框实际不同于异常原框，且不生成正常样本。

默认/`scope:"object"` 的“本对象正常”只清除该对象当前暂停，其他项继续待处理；正常事件从当前实际有效框保存 `geometryReference:{objectId,frameIndex,bbox,source:"confirmed-normal",checkpoint:true}`，已保存人工框优先，无须重画且不依赖运动校准。它更新该对象独立的几何检查点，不修改人工标注来源，也不重新选择框内最强光学核来当可信身份。消失或缺少有效框时拒绝正常确认。`pausedAnomalies` 返回尚待处理项，全部成功保存后才可续追；失败或原键重放不清除另一项。

图像风险的逐对象 normal 同样保存上述实际参考；它可用于后续原图光学建档，不能把正常确认改成在线模型训练。主体位移样本在 features 追加 `referenceKind:"head"`，旧样本省略即 bbox；两类独立使用，避免旧框中心阈值放宽主体跳跃。

追踪行的 v2 `identity_evidence` 可追加 `observationVerified`：仅由生产图像核验器在原图、BBox、非空二值Mask及框内占用有效时设置，不接受客户端宣称。该标志与 `status/head/identityReference` 独立，身份缺参考仍为unknown。当前源签名及帧/对象/框/Mask面积验证通过后，普通矩形形变和两框高重叠只作提示；独立图像风险、整体尺度极端与对象消失仍保护。历史Mask面积骤减仅在当前证据严格有效、主体可靠且无明确丢失时降提示；未知、失效或旧证据保留坍缩兜底。新增 `diagonal_ratio`、`geometry_thresholds.manual_diagonal_ratio` 及 `manual_diagonal_ratio=... HARD` 诊断解释实际整体尺度触发，不把同条记录的SOFT宽高指标冒充硬暂停原因。旧行缺字段不自动升级为已核验，没有新增确认权限或API写入字段。

诊断可追加 `mask_area_decision`（可靠当前主体提示或保守坍缩兜底），及coverage.metrics中的尾部光学归属标志；不新增客户端写入权限。`pausedObjects.type` 的尺寸分类包含对角线HARD，发布/持久化/前端说明按真实HARD原因统一；兼容历史错误type但保有raw reasons的记录，不根据SOFT尺寸把运动HARD改写成尺寸。原始框、掩膜面积、参考和暂停帧不因展示修正而重写。

可选 `singleCoreConflict` 仅记录当前唯一可见光核与可信伙伴、真实Mask交叠的核验摘要；对象身份仍可为unknown。持续未恢复时以 `image_shared_core_unresolved` 提示身份未辨明，按同一partner连续计数，不给未知对象新身份或修改单对象正常确认范围。已提供但非字典的head/coverage metrics被明确拒绝并记录，不让损坏历史摘要触发属性访问错误。

反馈响应包含 `{ok,mediaId,revision,normalMotionSamples,trackingFeedbackEvents,pausedAnomalies,lastPausedContext}`。正常/已修正请求必须匹配当前持久化暂停帧及未解决对象，并核验真实追踪行；新键不能再次确认已解决的历史异常。每次只移除选定对象及已被删除的暂停项；仍有待处理对象时保留暂停上下文。运动样本按媒体、对象和位移原因隔离；尺寸参照单独按确认事件恢复，不豁免重叠或丢失检测。普通工作区 PUT 不能注入校准样本。Tracking 状态另返回 `warningSummary`，用于合并轻提示，不把每次轻微抖动升级为阻塞弹窗。

### 正常参照与短时风险观察

整帧/单对象正常确认、几何与身份参照、风险持续观察和诊断字段属于旧暂停兼容路径，见 [details/tracking-pause-policy.md](details/tracking-pause-policy.md#接口正常参照与短时风险观察)。

### 异常暂停后从较早帧重新追踪

`POST /api/track/restart-branch/{mediaId}` 使用 Bearer 和 `Idempotency-Key`，正文为 `{expectedRevision,expectedPausedFrame,startFrame,confirmDiscardFuture:true,generationId?}`，帧号全部为原始 0 起编号。首次执行必须 `startFrame < expectedPausedFrame`、服务器暂停仍未解决、起点有有效框、原视频存在、轮次/工作区版本一致，且未送审、无运行中的 Tracking。暂停帧及之后只能使用逐项异常确认；普通 rewind/track 不得绕过未解决暂停。

成功返回 `{ok,mediaId,revision,pausedAnomalies:[],lastPausedContext:null,cutoffFrame,removedRows,keptRows,deletedFutureSeedFiles:0,overlayRegenerated:false}`。只截断起点之后的旧 AI 行，清除旧分支的当前暂停及未来派生提示；所有人工框、seed/基准、删除规则、历史反馈和正常样本保留，不生成 normal/corrected 事件。被丢弃未来里的身份/关系认可通过内部事件 ID 退休，不删除人工决定、几何历史或旧回执；起点同帧及更早认可仍适用，避免正常续追立即丢掉刚保存的判断。以后新分支走过旧确认帧再启动，也不能恢复旧分支的未来关系认可。操作不自动启动模型；客户端成功核对后保存该早帧 seed，再启动追踪，不重复普通 rewind。

跨文件发布通过持久日志与 SQL `review_write_receipts` 协调，收据重放早于业务版本/暂停校验，但轮次重置后不重放旧轮次操作；同账号原键原正文恢复已完成的重建，不再截断之后新模型的结果。相同 key 用不同意图返回 409。合法收据只证明过去已完成的分支操作；发布失败和重启按提交证据恢复两份文件，读取不能混用中间状态；临时故障保留原请求、暂停和数据，冲突需明确重读。当前暂停由真实追踪任务持久化，普通工作区 PUT 可以补展示信息和删除/撤销对象，但不能清空或伪造已确认暂停；显式反馈或重建才解除。运行时暂停更新不改变人工工作区 revision。

## 训练导出

实现：[training_export_routes.py](../backend/app/training_export_routes.py)、[trainingExportApi.ts](../frontend/src/api/trainingExportApi.ts)。

| 方法与路径 | 作用 |
| --- | --- |
| `POST /api/datasets/exports/preview` | `finalVersionIds`，可选划分字段同下；只读校验/统计，另返回 `splitMode`、`splitFrames{train,val,test}`、`videos[{finalVersionId,frameCount,objectCount,split}]` |
| `POST /api/datasets/exports` | `finalVersionIds`、`format:'yolo'|'coco'|'both'`、`splitRatio`（训练比例，默认 0.8）、`splitMode:'frame'|'video'`（省略时为旧的按帧划分）、`testRatio`（仅 video，默认 0，`splitRatio+testRatio<1`）、`videoSplits{finalVersionId:'train'|'val'|'test'}`（仅 video，须为所选视频）、`includeSourceVideos`（默认 false） |
| `GET /api/datasets/annotations/snapshots` | 本人每个视频最新的标注导出快照：`finalVersionId,mediaId,name,frameCount,createdAt,valid,reason`；标注变化或视频不可用时 `valid:false` |
| `GET /api/datasets/exports/{id}` | 本人任务阶段、帧数、错误和下载资格 |
| `GET /api/datasets/exports/{id}/download` | 校验当前资格后返回真实 ZIP，支持 Range。除 Bearer 令牌外接受登录时设置的 `annotation-export-session` Cookie（HttpOnly、SameSite=Strict，路径限于 `/api/datasets/exports/`，只能用于下载），所以页面用普通链接下载，令牌不进网址 |
| `POST /api/export/dataset` | 旧入口拒绝，410 `FINAL_CONFIRMATION_REQUIRED` |

创建请求幂等；任务生成状态与资格共同决定是否可下载，不得仅按 ZIP 文件存在就返回。资格与产物规则见 [WORKFLOW.md](WORKFLOW.md)。

## 全量标注流程统计导出

入口在标注记录页；实现为 `statistics_export.py` / `statistics_export_jobs.py` / `statistics_export_routes.py`，前端 `StatisticsDataExport.vue` 与 `statisticsExportApi.ts`。按外部脚本的 annotation-confirmation-statistics-v1 格式导出全实例，包含未完成和历史 A/B/C/F、人员与流程记录，不受页面筛选影响。现有登录账号可申请全量包；当前没有额外管理员角色。生成的任务和下载仅对创建账号开放，匿名拒绝、其他账号不能读取该任务。

| 方法与路径 | 作用 |
| --- | --- |
| `POST /api/statistics/exports` | 空 JSON `{}`，必需 `Idempotency-Key`（1–128 字符）；202 返回后台任务，当前进程的有效回执内同账号原键返回同任务，不要求 X-Review-Contract |
| `GET /api/statistics/exports/{id}` | 本人任务状态 `queued/running/ready/failed`、阶段、表数/行数进度、文件名、大小、错误、到期时间 |
| `GET /api/statistics/exports/{id}/download` | ready 时鉴权下载 ZIP，不返回数据库文件；未就绪 409，生成失败 409，输出丢失 410 |

全实例同时最多生成一个包，新请求遇到已有运行任务返回 409 `STATISTICS_EXPORT_BUSY`；有效回执的原键恢复优先。临时输出保留 24 小时、最多 20 份，任务/回执有上限。已取得任务 ID 的查询在服务重启或任务过期后返回 410 `STATISTICS_EXPORT_EXPIRED`，用户可明确新建。输出文件丢失为 `STATISTICS_EXPORT_FILE_MISSING`，需重新生成。瞬时创建/查询/下载错误保留可重试状态；前端按账号保存创建键和任务 ID，未知创建结果沿用原键。回执是进程内临时状态：若创建响应未返回 ID 且服务随后重启，或回执 24 小时到期，旧键的手动重试可能生成新只读包，不承诺跨重启/过期仍是同任务；这不会重复修改业务数据。请求日志不记录 token。

ZIP 包含 15 个必需 CSV/JSONL、2 个可选历史表（存在时）和 manifest，逐文件校验和与行数用于传输校验。users 仅 id/username/created_at；密码、JWT、原始视频、原 SQLite 文件均排除。`sourceSystemId` 优先读取已有 `audit_source_identity` 部署身份，旧库无身份时 CLI 须显式提供或配置 STATISTICS_SOURCE_SYSTEM_ID，不能默认把多个医院当同一个来源。`final_versions.is_current` 表示读取时已确认会话的最新 F，不代表历史 F 可训练导出；空帧和旧版本保留。

该包与下节 annotation-quality-audit 包分别维护。消费者必须支持此全量格式的导入和展示；本地统计项目和远程仓库可能使用不同前端入口，联调必须核对实际部署版本。相同来源的新全量包会包含旧记录，消费者需要按稳定身份处理重复或更新，不能把主键冲突当正常增量行为，也不能将首包导入成功当成反复导入验收。本功能不改变训练集准入、快照或流程状态。

## 审计传输契约

现有创建 API 和按钮不增加请求字段；任务与不可变审计同事务落库。训练 `manifest.json` 的 `schemaVersion` 为 **2**：顶层 `sourceSystemId`、`exportId`、`auditReference {auditId,sha256,schemaVersion:1,capturedAtUtc}`；`sources` 固定 mediaRevisionId/mediaId/sourceSha256/宽高/帧数/A/B/C/F/snapshotHash；`samples` 增加 sampleId、mediaRevisionId、imageSha256、objects[]。objects 项将稳定 objectId 对应到 1 基 yoloLine 或 cocoAnnotationId，空帧保留空列表。文件名包含 exportId；标签仍是标准格式。

完整证据通过服务端 `python -m app.audit_export` 只读提取，或使用根 `export-audit.sh`。运维 ZIP 顶层 `format:'annotation-quality-audit',schemaVersion:1,extractedAtUtc,datasetCount,files[{name,sha256,size}]`；`datasets.jsonl` 含生成状态、创建时间、提取时 currentFinalAtRead、auditReference、snapshotFile、manifestFile。snapshots schemaVersion:1 保存 sourceSystemId/exportId/auditId/capturedAtUtc/sourceAppRevision、dataset 设置、sources、tables、completeness、坐标/责任约定。该 ZIP 的版本与训练 manifest 版本分别维护。

提取记录“ready”只表示生成成功，不表示已下载或训练。currentFinalAtRead 只表示提取时的当前状态；导出的历史 F 选择取 F 的 decisionEventId 和冻结决定，不能用最新 head 替代。SHA 用于传输校验，发布方身份仍依赖可信交付渠道。读取/提取故障返回非零且不发布半个包。

含补标/删除的统计包、训练审计与审计传输包声明 `requiredFeatures:["review_presence_v1"]`。消费端必须理解 nullable bbox、对象存在性和完整决定记录；旧统计系统需适配后再分析，不能将 null 几何当 0 或遗漏删除决定。

### 点选头部

`POST /api/track/head-box/{media_id}`（需登录）：`{frameIndex,x,y}` 为原始帧号和原图像素点击坐标；返回 `{frameIndex,width,height,bbox:[x1,y1,x2,y2],confidence}`。只提供候选，不写工作区或建立对象 ID。422 表示坐标/候选无效或歧义，404 表示素材/帧不存在，503 表示模型不可用，或排队约 20 秒仍未轮到。日志 `annotation.head_click` 记录素材、帧、框、耗时与失败原因。


检测器与固定权重随仓库及后端镜像提供；`HEAD_DETECTOR_MODEL` 可指定自定义权重。`HEAD_DETECTOR_DEVICE` 默认 `cuda:0`（`compose.gpu.yaml` 同值，CPU 部署 `compose.yaml` 显式设为 `cpu`）；GPU 不可用时请求失败，不静默切换 CPU。惰性加载、单请求推理，不自动下载模型。

`POST /api/track` 可选 `edgeDiscovery:boolean`（兼容接口默认 false，新前端固定 true），包含在启动幂等正文中。开启使用与点选头部共用的检测器和设备配置。任务推理阶段发现边缘新对象后仍以既有 Tracking objects 返回，不改变frame/objectId坐标契约；检测器不可用时任务failed，不静默降级为识别成功。头部检测权重随应用镜像提供，与单独保留的 SAM3 模型不同。

首尾结果读取采用 head-centre-v1 统一端点投影，返回 terminalIdentity.matches 合并已验证对应和必要的未匹配尾ID消歧（内部仍分开保存）。后者只定义端点身份，不证明跨视频时序匹配。JSONL 与导出来源应用同一规则；原结果文件不改写。旧快照不变，新预览的 sourceRevision 包含投影策略。
