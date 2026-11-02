# 存储、快照与迁移边界

> 要点
> - 每个视频的工作区、追踪结果和恢复日志在媒体目录下；业务库为单个 SQLite（`APP_DB_FILE`）。
> - 审查数据：`annotation_reviews` / `annotation_review_checks` / `annotation_review_exclusions`；历史 A/B/C/F 表只读。
> - 写入依赖版本、幂等回执与原子发布；删除是持久过滤规则，不物理擦除追踪原始数据。
> - 测试与夹具必须使用独立的 `APP_DATA_DIR` / `APP_DB_FILE` / `APP_STORAGE_DIR`。

`annotation_reviews` 兼容追加可空的 `evidence_policy`、`checklist_history_json`。未结束旧轮次升级审查筛选/分组策略时，在同一SQLite事务内归档旧items/检查快照并替换疑点清单；原 `annotation_review_checks` 记录不删除。已通过历史轮次不重建，原Tracking诊断和几何文件不改写。新增列对旧数据兼容，不改变离线存储 contract；策略版本（当前 actionable-review-v6）包含筛选版本和独立候选对应容差指纹，避免每次读取反复重建。

## 概览

annotation_export_snapshots 独立保存直接导出快照：账号/请求键、正文哈希、来源版本、原视频哈希和元数据、完整帧框及快照哈希。快照不写 final_versions、不创建A/B/C/F。audit requiredFeatures=annotation_direct_export_v1，包含原快照及操作者；下游不支持时须拒绝，不能将其误作经过审查的数据。

异常后的早帧重建及普通 rewind 在媒体根目录 `_tracking_restarts/<operation>/` 保存持久 journal、结果与工作区原文件备份及已同步的新文件。源锁保护当前进程，API 的 SQL 收据决定重启后保留已提交发布还是恢复两份原文件；普通 rewind 不递增人工工作区 revision。独立模型脚本没有登录账号时，同日志使用已同步的 `committed.json` 提交标记，不创建伪造用户或依赖运行中服务的数据库。读取工作区/追踪结果、送审和后续追踪前先恢复未完成日志，已提交清理不能覆盖之后的新编辑。此目录是恢复状态，不纳入 Git、不手动清理；恢复成功后服务仅清理本操作目录。缺工作区的 legacy 路径不发布空 workspace 遮蔽原人工 seed。历史人工反馈和已保存人工框不随 AI 未来分支截断而删除。兼容旧工作区无私有暂停字段，新的 `_pauseCandidates` / `_pauseContext` 仅供服务器维护暂停候选及删除撤销，不在公共响应或统计 ZIP 中。

真实模型结果发表前另写媒体目录的 `.tracking-pause-publication.json`，记录本次 publicationId、目标结果摘要和暂停依据；发表后或读/启动时，仅在结果摘要匹配时补持久暂停，并以工作区 `_pausePublicationId` 防重复应用。清理失败后的残留不能让已经确认的旧暂停复活；结果尚未发表时不借意图清除原暂停。该文件同样是恢复状态，不作为工作区/API/统计数据交付，不能手工删除来绕过异常。

全量流程统计临时包由 `statistics_export_jobs.py` 存在 `$APP_STORAGE_DIR/.statistics-exports/runtime_<id>/`，带专用所有权标记。仅清理自身标记目录和到期的统计包，不遍历/清除媒体、训练集或用户备份；24 小时/20 份限制不影响已下载文件。运行任务和回执在当前进程内，重启后旧任务失效需重新生成，未取得任务 ID 的创建重试跨重启/回执过期不保证同任务。独立 CLI `python -m app.statistics_export` 的默认输出为 storage/statistics-exports，明确路径时拒绝覆盖。源数据库只读；在线分页复制到权限隔离的临时快照，再从快照分批读表和压缩，避免长时间 CSV 写入占源库读锁。临时 SQLite 快照绝不进入 ZIP，正常和失败均清理。导出不会迁移或写入业务表。

路径来自 [config.py](../backend/app/config.py)。

| 配置 | 默认 | 内容 |
| --- | --- | --- |
| `APP_DATA_DIR` | `backend/data` | 默认 DB 目录、`logs/review.log` |
| `APP_DB_FILE` | `$APP_DATA_DIR/app.db` | SQLite 账号、人工记录、A/B/C/F 和导出任务 |
| `APP_STORAGE_DIR` | `backend/storage` | `media/`、`datasets/` |
| `SAM3_MODEL_ID` | `backend/track_modul/facebook--sam3/snapshots/master` | 模型配置与权重 |

Docker 显式设定 `APP_DATA_DIR=/data/database`，业务日志位于宿主机 `APP_DATA_ROOT/database/logs`，与数据库一同持久化。源码默认路径不变。

修改 DB 路径不自动修改日志路径；修改变量不自动搬迁已有数据。不要将测试目录与业务目录混用。


审查采用框在工作区 `manualAnnotations` 保留 `origin=review_adopted` 和 `reviewAdoptionKey`（审查轮次、项编号、动作类型），用于保存/检查失败后的原操作恢复。它不参与训练框几何，不代替工作区 revision 或写入幂等键；补框也用该标识避免检查重试重复创建。训练包 `review/decisions.jsonl` 汇总已通过各轮及归档清单的候选判断，同帧重合位置以最新结果为准，较新的 `ok` 不再输出旧 `fixed/not_target`。
浏览器本机恢复：IndexedDB `annotation-workspace-recovery/records` 缓存独立基准、待保存草稿与原键原正文请求，键包含页面标识、账号和视频；观看状态使用带 generation 的本机 sessionStorage 键。人工框的可选 creationId 用于辨别独立新建身份与恢复后的编号对应，不替代 objectId。无数据库迁移或后端文件格式变化，离线 contract 不变。兼容读取旧 sessionStorage 请求；缓存不可用时提示用户并继续服务器保存，待确认请求保留在页面内存中按原键回放。JSON 仅按需导出，不再是重新读取的固定副作用。

## 文件与工作区

```text
storage/
  media/<media-id>/
    原视频（上传时不转码）
    media.json                   # 原始宽高、FPS、帧数等
    annotations_frame_*.json     # 每次 Tracking seed
    tracker_results.json         # JSONL：逐行原始视频帧
    tracker_results.meta.json    # 已生成结果的持久标记；文件丢失不能变成未追踪
    tracker_overlay.mp4          # 按需生成的带框预览，不属于追踪完成条件
    tracker_overlay.meta.json    # 派生预览的输入签名，可重新生成
    workspace_state.json         # 可恢复人工/删除/基准/异常/当前位置与设置
    .frame_cache/                # 逐帧 JPEG 缓存，128 帧/128 MiB 上限
      source.json               # 源文件路径/大小/mtime/文件身份，失效后重建 JPEG
  media/_annotation_resets/<id>/ # 覆盖事务的临时文件及 journal.json；重启按 DB 回执恢复/清理
  datasets/
    train_<id>.zip               # 当前训练导出生成包，认证接口下载
    train_<id>.zip.partial       # 打包临时文件
    .work/                      # 临时提帧与标签
```

`workspace_state.json` 临时文件写入后原子替换。AI 逐帧结果不复制进去，加载时与 `tracker_results.json` 合并，人工修正优先，删除标记不能因重新加载复活。

追踪结果原子发布后写入 `tracker_results.meta.json`，旧结果在首次成功读取（详情或 JSONL 下载）时补记。该文件存在而 JSONL 缺失时，列表明确标为 missing，读取、下载、审查和导出均拒绝，不能当作从未追踪。JSONL 存在而标记缺失时按旧数据读取并补记；旧版本从未被新版读取且已丢失的结果，以及结果/标记同时丢失的情况，没有足够持久证据还原其历史状态。任务报告成功/暂停后的 `required=true` 读取仍要求结果必须存在。覆盖重标把标记与结果一同纳入原有文件事务，回滚时同批恢复；删除视频删除整个目录。新上传不创建结果占位文件。标记为兼容的附加文件，不修改 A/B/F 或数据库，存储运行契约仍为 1。

精确帧 JPEG 先写临时文件再原子替换；命中缓存读取为响应字节，避免返回路径后被 LRU 清理。只清理 `.frame_cache/frame_*.jpg`，源变化清理旧签名的这些 JPEG；不删除视频、标注、预览临时文件或其他运维数据。最多缓存 2 个解码器，后台长时追踪/导出另用顺序解码；不共享来源写锁。浏览器缓存服务器视频的人工框，AI 正文不再重复写入 localStorage；人工 pending 原请求、删除撤销快照及业务审计仍保存。

Tracking JSONL 同样先写临时文件再原子替换，发布失败保留旧分支。长时视频解码、模型推理和按需预览编码不占用来源锁，结果/预览发布才使用短锁；追踪运行期间该视频的工作区只允许浏览状态保存，框、人工基准、删除规则写入仍拒绝；锁只按 mediaId 生效，其他视频照常写入。预览生成在 `.frame_cache/overlay-*.mp4` 临时文件进行，成功且输入签名未变化才发布；它及 meta 文件是可重建缓存，不影响 A/B/F 或训练集原图。任务内存与前端待查询记录不代表推理跨重启恢复，启动幂等回执在当前进程内有效。

覆盖重新标注不受历史审查记录限制（历史 A 不可变，不受影响）；覆盖后已通过的审查失效。重置使用 SQL 写事务、工作区 revision 与幂等键，移动旧 workspace/seed/results/overlay 到恢复目录，写入空工作区并删除该 media_id 的活动人工查询记录；原视频、media.json、历史 A 保留。成功后清理临时旧文件，失败回滚；启动按 journal 和成功回执协调进程中断后的文件状态，不能当作普通缓存直接删除。新的 generationId 使前端丢弃旧框/撤销/校准缓存，并在重放待重试草稿前识别轮次变化；仅已确认的覆盖允许丢弃旧请求，普通冲突不能静默覆盖。旧版本或旧无版本客户端不能写回已舍弃状态。新增字段和表均为兼容增量迁移，存储运行契约保持 1。

工作区当前还保存 `revision`、稳定 ID 的 `deletedObjectIds/deletedFrameObjects`、服务端生成的 `normalMotionSamples` 和 `trackingFeedbackEvents`。内部 `_writeReceipts` 与状态同一次原子替换，GET 不暴露回执；响应丢失后原键重放不会重复递增版本或新增反馈。文件替换失败保留原状态和版本，日志记录 `annotation.workspace_save_failed`。这些状态受单进程来源锁保护，不属于 SQLite 跨资源事务。

反馈回执可用内部 `feedbackEventCount` 引用不可变事件历史前缀，避免每次确认重复嵌入全部历史；可推导的运动样本在重放时从该前缀恢复，重置后重试旧键仍返回当时的完整响应。兼容读取旧完整回执，随成功写入压缩可安全引用的旧条目，不删除 key 或缩短重放期限。回执数量本身仍随保存次数线性增长，工作区上限仍为 64 MiB；此优化不等于无限容量或长期负载验收。离线回滚继续恢复同批备份数据。

新上传 `media.json` 记录源文件 size/mtimeNs/inode 指纹及 SHA-256；仅指纹未变时复用摘要，旧媒体或指纹改变重新计算。文件写入、视频探测及查重哈希在工作线程执行；导入之间串行，长哈希不持有全局来源锁。发布前在来源锁内重新核对候选集合与指纹，删除或其他导入改变候选时重新查重，避免过期匹配。

删除是持久化过滤规则，原始 Tracking JSONL 和旧 seed 保留以便撤销；所有活动读入、续追 seed、审查和导出均应用规则。仅删除某帧不删除模型在后续帧的身份，重新追踪也不能让该帧复活。整视频删除同时排除人工记录、AI 框与人工基准。服务端 `deletedAnnotationFrames` 记录原先有人工框、后因删除变空的帧，使送审仍能区分已知空帧和未知帧。此字段与校准样本一样由服务端维护。

正常运动样本取自真实追踪行的位移测量，按视频、对象、原因保存。正常确认事件另带可选 `geometryReference`（实际像素框、帧、对象、confirmed-normal 来源）；新版单对象正常与整帧正常都保存 `checkpoint:true` 的独立几何检查点，已保存人工框优先。恢复时校验帧界限、删除及新人工修正，不改写 manualBaselines。旧事件无该字段时仍按原人工基准判断，不推断用户曾认可未知几何。原有 AI 预测不会自行成为正常样本或尺寸参照。重置清活动运动样本并使旧尺寸及身份参照失效，但保留反馈事件，已修正决定独立记录。旧客户端遗漏新字段时，服务端保留删除规则与反馈，避免旧缓存覆盖新控制状态。

主体运动校准使用可选 `features.referenceKind:"head"`，旧省略字段仍为 bbox；二者不混用。图像风险正常确认也保留实际像素参考。原图光学引用每轮从这些显式人类来源重建，当前 Mask 仅瞬时占用 ROI，不写新长期 Mask 文件。Tracking JSONL 可追加版本化 `anomaly_details.identity_evidence` 与图像观察计数/首次帧，保留有限光学点、度量、来源与状态，未知不得当正常。都是兼容诊断扩展，无 DB 迁移、模型更新或离线存储契约变更。

v2证据追加的 `observationVerified` 记录当前有效原图与非空二值Mask已经核验，不能用它替代可靠身份。光学核的实际BBox覆盖、Mask覆盖及头部到边界距离分别保存；普通矩形形变和重叠可保留warning，明确图像风险不变。旧结果缺此标志时继续保守兜底，回放脚本也不得凭BBox或Mask面积补造标志/位图。整体尺度诊断可增加 `diagonal_ratio` 及相应阈值，原BBox、Mask提取和人工参照不改写；无需迁移数据库或提升离线存储契约。

兼容诊断扩展：`mask_area_decision` 记录历史面积骤减为何仅提示或继续保守保护；`coverage.metrics.tailAttributionUncertain` 及各尺度未归属/光学连通像素和端点归属说明细线证据。它们不保存新图像或Mask、不改变人工身份/几何来源，也不增加表或调整存储契约。旧暂停type显示错误时从已有HARD原因恢复解释，不为修文案删除暂停或重写标注。

旧媒体没有工作区文件时，只从 seed 的 `source=manual` 恢复人工记录/基准；AI、warning、anomaly 不当人工基准。首次编辑/追踪可写回恢复状态。文件原子替换与 SQLite 事务不是同一跨资源事务；不要假设一个能回滚另一个。

整帧人工检查点保存在同一 `workspace_state.json` 的服务端 `trackingFeedbackEvents`，每对象一条 `scope:frame` normal 事件，共用 checkpointId 和检查时间/操作者，geometryReference 标记 checkpoint:true。它是独立人工确认来源，不伪造 manualAnnotations/manualBaselines，不重复写 seed 或 AI 文件。一个工作区原子替换同时发布全部框和暂停解除及幂等回执；重试不重复生成事件。续追/重启按原始帧号恢复不晚于起点的最近检查点，取代旧尺寸基准；同帧或更新人工修框、删除与 reset 仍优先。属于现有反馈事件的兼容字段扩展，无新增 DB 表，存储契约仍为 1。

单对象正常事件带 `scope:object`、`pauseId` 和 `pairSnapshots`，只解决本项。内部 `_pauseFeedbackId` 标识一次真实暂停；工作区读取不公开它，客户端不能注入。当前相交对象对的双框、同帧人工保存快照和媒体 generation 合成服务端摘要；双边分别确认必须共享暂停与摘要，单边事件不发布关系认可。第二方成功事件或显式整帧事件才写 `overlapReferences` 的 `approvalVersion:2`、双方 `confirmedEventIds` 等审计字段，既有事件和旧幂等回执保持准确历史。人工编辑、删除/撤销删除改变确认快照，新暂停使用新标识，不能拼合先前暂停的单边确认。

内部 `_overlapReferenceEpochs` 按稳定对象 ID 保存引用代次；人工几何、删除和撤销删除递增所涉对象，普通更名和其他对象编辑不影响它。关系快照保存双方 `objectEpochs`，身份参考保存自己的 `objectEpoch`；恢复时与工作区当前代次核对，即使撤销回到原框也不能复活旧认可。旧文件无代次按 0 兼容，既有事件和收据不删除或改写，旧客户端不能写内部代次。

内部 `_retiredTrackingRelationshipEventIds` 标记被分支截断范围 `frameIndex > cutoff` 中带身份/关系认可的事件，仅使这部分 identityReference/overlapReferences 不再恢复；cutoff 同帧及以前保留。人类事件、几何检查点和幂等回执的历史内容不改写，新分支同帧的新事件可正常生效。退休元数据与 JSONL 截断共用上述两文件发布/回滚；失败不能只删除结果或只退休判断。普通保存不公开这些内部字段，也不能由客户清空它们。

future 行截断成功后，恢复日志按操作前的文件指纹清理旧 `tracker_overlay.mp4`，不重新转码，提交失败保留旧预览。残留日志不能误删后来生成的新预览；旧缓存删除失败只记录 `tracking.preview_cleanup_pending`，不回滚已提交的源数据。预览读取仍核对当前原视频/结果签名，缓存过期时重建或返回 503，不能把残留旧文件下载为当前结果。

可选 `identityReference` 保存服务器核验的紧凑原图主体参考，与 geometryReference 独立：v2 来源、原始身份建立帧/框/点、当前可靠帧/点/框、外观、半径、sourceSignature 和 generation。当前实际追踪行须先提供可靠新版引用；反馈持全局源锁时最多读取当前原图一次并批量核验小区域，不按历史逐帧重放，也不存 Mask 或整幅图像。旧错误 v1 光学点不作为身份来源，暂不可靠时不写信任引用。双边 overlapReferences 可附双方可靠 identityReferences，只有几何认可不能授予图像身份豁免。原始 AI 输出、人工 seed 与来源保持独立；后台追踪负责旧数据的历史身份恢复。该扩展不增加数据库表、不改变离线存储契约，也不缩短原键重放期限。

seed 的临时写入或替换失败保留旧文件，临时文件清理后可重试；历史 seed 损坏返回读取错误，不能忽略坏人工来源后显示为尚未标注。日志事件为 `annotation.seed_saved/seed_save_failed/legacy_seed_read_failed`。

## SQLite 数据关系

`backend/app/db.py` 的查询接口保留可选 `user_id` / `media_id` / `source` 过滤；同名视频按 `media_id` 区分。新素材编号跳过已存在目录，也跳过 `annotation_reviews`、`annotation_review_exclusions`、`annotation_export_snapshots`、`media_revisions` 中仍有记录的编号（删除视频不删这些历史），因此删除后再上传同名视频得到 `stem_001` 等新编号。导入标注包的素材在 `media.json` 额外记录 `importedFrom`。合并分支时不要恢复同名重复函数（`scripts/check_repository.py` 会检查重复定义）。

完整 DDL 在 [review_schema.py](../backend/app/review_schema.py)、[review_workflow.py](../backend/app/review_workflow.py)、[confirmation_workflow.py](../backend/app/confirmation_workflow.py)、[training_export.py](../backend/app/training_export.py)。启动时增量建表，保留历史数据。

| 数据层 | 表/作用 |
| --- | --- |
| 媒体与 A | `media_revisions`、`annotation_baselines`、`baseline_frames` 固定来源、完整帧及对象 |
| B 编辑 | `review_sessions`、`review_frames.patch_json/state` 保存草稿状态，`submission_id` 保留上次成功引用；提交正文另存不可变表 |
| 不可变帧提交 | `frame_submissions` + `review_submission_payloads` 存成功提交及净差量 |
| 固定 B | `review_versions` + `review_version_frames` 完整逐帧对象；`review_changes` 存相对 A 的净变化 |
| C 决定 | `confirmation_sessions`、`decision_events`、`decision_heads`；事件保留、head 表示当前选择 |
| C 并发/撤销 | `confirmation_change_versions`、`confirmation_actions` |
| F | `final_versions`、`final_version_frames`、`confirmation_final_records` 存完整帧、决定快照、前一版本 |
| 历史/兼容 | `confirmation_reopen_events` 留重新确认记录；`final_frame_objects` 兼容旧对象读取方，不能取代完整帧表 |
| 撤回与重审 | `review_withdrawals` 留送审者撤回记录；`review_return_events` 留 C→B 的帧/原因/操作者及后续 B/C 关联；`confirmation_decision_carries` 关联继承决定与原事件 |
| 游标 | `review_bookmarks`、`confirmation_bookmarks`，按用户与任务隔离、版本独立 |
| 幂等 | `review_write_receipts` 与业务变化同一事务；`training_exports` 有操作者+请求键唯一约束 |
| 导出 | `training_exports` 存任务设置、进度、manifest、错误、完成状态 |
| 审查 | `annotation_reviews` 每视频每轮一行：轮次、状态 open/passed、开始人/时间、开始时逐帧内容（`start_frames_json`）、清单（`items_json`，含修正后追加的 `followup` 复查项）、是否加严（旧轮次字段，新轮次恒为 0）；通过时记录通过人/时间、内容摘要 `passed_revision`、通过时逐帧内容、列表用的工作区/追踪文件标记 `passed_stamp`、修改记录 `changes_json`。`annotation_review_checks` 每项一行：结果 ok/fixed/empty/not_target、检查人、时间。只追加轮次，不改写旧轮。`annotation_review_exclusions` 记录“不是精子”的排除位置：`id/media_id/review_id/item_id/source/frames_json/actor_id/created_at/revoked_at/revoked_by`；`frames_json` 为原图像素 `[{frameIndex,bbox:[x1,y1,x2,y2]}]`，只追加和撤销，不物理删除。静止排除写 `source=static`，其他来源只在 ±5 帧范围生效；旧静止记录从对应清单及归档恢复类型 |
| 审计身份/证据 | `audit_source_identity` 保存部署身份；`training_export_audits` 按 export_id 唯一保存规范 JSON、采集时间和 SHA；触发器禁止更新/删除 |

A/B/F 不随浏览、缩放、草稿、重新确认或退回重审而改写。F 的对象集合和类别继承 A，只选择几何来源。当前帧重审使用上述事件表；旧问题标记表不作为这一流程的状态来源。

新版 C 将一次决定的变化项或游标的空项列表写入原 `review_write_receipts`，避免每次浏览都复制整个修改项列表。旧回执不改写；决定事件、head、版本与业务事务不变。新增修改项导航复合索引为兼容增量，存储运行契约仍为 1。

## 备份、兼容和清理

- 备份至少覆盖实际 DB、storage、仍使用的 `backend/track_data` 及部署配置/模型版本。停写后备份或使用 SQLite 一致性备份，不能在写入中随意复制不完整状态。
- 当前部署是单进程，不共享一个 DB 给多后端进程；不要将 SQLite 放到 SMB/NFS 供多个实例直接读写。
- 旧 `backend/track_data/<media-id>` 保留兼容读取；新上传写入 storage/media。受支持的编辑流程仍可能对已定位到的旧媒体目录写入工作区，不能将“兼容读取”误解为整个目录被文件系统锁定只读。
- 迁移前停后端，备份，然后逐个移动旧素材子目录到 media；不要套入一层 track_data 或覆盖同名 ID。
- 缺少完整 A/B/空帧的历史任务保持只读并解释原因；不能猜测补帧或冒充可导出 F，需在当前流程中重新审查通过后导出。
- `.frame_cache` 可重建；训练任务正常完成/失败会清理临时目录。异常进程终止留下的 `.work` / `.partial` 需停服务后核对再清理。
- 原视频、seed、Tracking、工作区、DB 和历史版本不是缓存。不要因文档整理或测试删除它们。已完成 ZIP 暂无自动过期策略，清理需考虑任务记录及下载关系。

## 数据集质量审计

`quality_audit.py` 在创建导出任务的事务内固定所选 F 的相关业务历史：媒体/A 全帧、B 有效帧与全部成功 submission/payload、B 版本与净修改、C 决定/动作/撤销/重新确认、F 完整帧与决定快照。用户仅导出 id/username，不含密码、JWT、游标或写请求收据。部署身份随数据库备份/升级保留。每份导出独立快照，重新确认不修改旧证据。

发生帧重审时审计还保存退回事件及决定继承关联，补入来源 C 的决定事件与责任人，不能把继承的选择冒充为本轮新点击。

旧数据集没有快照时明确未覆盖；只读提取不会初始化数据库、补造历史或把未知观看时长写成零。A 的作者字段表示送审责任，不能代表每个框的手工作者。失败任务可保留尝试快照，但不算成功交付。

完整快照保存在 DB，训练 ZIP 仅带轻量索引和原有 provenance。新增表/触发器是增量迁移，存储运行契约仍为 1；回滚按完整离线更新器恢复同批 DB/storage。

审查增删与退回采用兼容增量迁移：review_workflow.migrate 创建 `review_object_catalog`、`review_object_allocators`、`review_annotation_returns`，已有表和 A/B/F 不删除重建。旧几何 patch 仍可读；新 patch 增加 add/delete，review_changes 的 before_bbox/after_bbox 以 JSON null 表示不存在，几何指标为 SQL NULL。新客户端用业务协议 3，旧客户端不得擦除新存在性变化。退回只关闭旧任务，不清空原人工文件；重送产生新 A/任务。存储运行契约保持 1（同一 SQLite/目录，启动兼容迁移，无模型/环境变更），降级仍必须恢复同批备份数据，不能让旧后端直接打开新版活动标注数据。

启用边缘发现时，Tracking JSONL 行可附加 `edgeDiscovery`（version=1的有界候选状态），对象可附加 `entryOrigin`（kind、firstFrame、confirmedFrame、motionVerified、humanConfirmed）。候选不是人工标注或审查结论；回退/覆盖按原Tracking分支事务处理，不另写失去事务保护的候选文件。旧行缺字段时正常从当前帧建立排除基线，不能把初始可见目标当新入画。

## 集中审查兼容存储

新首尾请求 combineResults=true 时，Tracking objects 数组只保存一套综合框。反向候选与局部检测在内存中参与连续路径计算，不另存整段自动候选文件，不向标注画面增加来源/采用理由。人工端点快照与必要未解决疑点仍按原来源契约保存；jointCheck 仅记录状态、策略及不确定对象，综合失败有明确标记。修正框清除旧 score/mask_area/anomaly 等过期诊断；人工几何、删除墓碑、稳定身份和 sourceRevision/F 导出链路仍生效。

首尾双向追踪没有新增业务数据库或独立可变模型标签文件。尾帧人工框保留在 workspace_state.json 的 manualAnnotations 中，唯一对应后统一 objectId，另存 endpointOriginalObjectId / endpointAnchorHash 以保留原局部编号并防止重复转换。本次首尾请求快照由原追踪幂等正文固定。JSONL 行 reviewEvidence.endpointCheck 保存状态、策略/端点哈希、一对一身份对应及末帧 terminalAnnotations 原始快照；反向框仅在差异疑点中保存，不替换正向对象数组。送审时将原始尾帧快照复制到集中审查 evidence_json，后续重追不抹除历史。A 来源合并兼容尚未完成客户端编号统一的尾框，已转换框不再次应用映射；未确定对应不强制合并。

首帧回归纠错保留原锚点哈希与 endpointOriginalObjectId，同一哈希下明确映射更新仍从原编号应用，不能把旧 canonical ID 当原编号。`reviewEvidence.boundaryExit` 记录已退出的AI身份及归档诊断；历史读取投影只改变返回结果，不覆写源JSONL或删除墓碑。原始人工框及审查历史保留，无数据库/离线存储契约变更。训练来源摘要包含当前投影策略，避免继续把旧错配版本当作当前来源。

训练导出目录可包含 `review/decisions.jsonl`：这是直接标注快照视频最近一次通过审查中，审查员对 `missing/static` 候选的 `fixed` / `not_target` 判断，每个 observation 一行，带 `sourceSha256/frameIndex/bbox/category/source/result/reviewId/itemId`。它用于检测模型的困难样本追溯，不是 YOLO 标签；没有判断时不生成文件，manifest 的 `reviewDecisions.count` 为 0。合并脚本按视频及实际保留的最新导出版本筛选判断后追加，保留原JSONL字段和行格式；不会将多视频包的整份判断重复追加，也不混入被新版替代的视频旧判断。声明有判断但文件缺失、行数不符或来源不属于包内视频时明确拒绝合并。

`concentrated_reviews` 保存 session/source_hash/不可变 evidence_json 和当前 final_version_id；`concentrated_reopens` 保存重开帧、原最终版本、操作者与时间。Tracking 行 `reviewEvidence` 包含 findings、检查状态、反向核验状态、expectedObjectIds；同一原子发布保护。送审固定证据不再依赖可变工作区。

为复用既有 F 外键与导出校验，新流程内部仍有 confirmation 记录，但决定 note 为 `review-acceptance:explicit-video-completion`，不是 C 人工点击。新审查完成与 F 在同一 SQL 事务；重开立即使旧导出无效，再完成只准当前最终指针。旧任务不迁移、不改原历史。此次为兼容新增表，离线 contract 仍为 1；回滚须恢复同批数据库/存储，不能只回退源码。
