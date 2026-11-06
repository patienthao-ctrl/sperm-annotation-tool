# AI 开发入口

本项目是 Vue 3 / TypeScript + 单进程 FastAPI / SQLite 的精子视频标注工具。本文是 Claude Code、Codex 等所有 AI 编程工具的共同入口（`CLAUDE.md` 只引用本文）。规则只写在仓库文件里；任何工具的私有记忆、会话记录都不是项目记录。

## 开始工作

1. 先读 `work/HANDOFF.md`（如存在）和 `git log -5`，了解上一位开发者（人或另一个 AI 工具）留下的进行中工作。检查 `git status`，保留已有未提交改动。
2. 读 [代码地图](docs/PROJECT_STRUCTURE.md) 找到要改的文件，再只读与任务相关的文档**章节**（见下文“节省上下文”）：业务规则 → [WORKFLOW.md](docs/WORKFLOW.md)；标注、追踪、画布、快捷键 → [ANNOTATION.md](docs/ANNOTATION.md)；接口、数据库 → [API.md](docs/API.md)、[STORAGE_LAYOUT.md](docs/STORAGE_LAYOUT.md)；部署 → [DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md)。
3. 用户当前明确要求优先。文档与代码不一致时，以代码和测试为事实，调查原因后修正文档；不能从 `docs/archive/`、旧原型、旧报告或旧注释推断现有规则。

## 关键边界

- 流程：标注 → 审查 → 导出。导航只有“标注”（`/annotate`）和“审查”（`/review`，即 `AnnotatePage` 的 `mode="review"`）两步，另有“标注记录与统计导出”页（`/results`）。审查员在同一画布、同一工作区直接改标注，没有 A/B 副本；审查员逐帧检查并确认（没有自动疑点），全部帧确认后显式“审查通过”，记录修改与审查人。规则见 WORKFLOW。
- 只有审查通过且内容未再改动的视频才能导出（`backend/app/annotation_review.py` 的 `require_passed`）。任何改动使通过失效，重新审查只查改动部分。浏览、勾选清单都不等于通过。
- 允许同一账号标注并审查，分别记录。
- 旧对比确认页、旧审查页、送审/撤回/领取及其接口已删除；A/B/C/F 表只读保留给统计/审计导出，不能再锁住工作区，也不能恢复这些页面。见 `docs/archive/旧审查与对比确认流程_2026-11-01前.md`。
- 真实原始帧号：API 从 0 开始，页面从 1 开始。人工标注工作区坐标为 0–100 百分比；追踪 seed、追踪结果、审查证据和历史 B/C 为原图像素 xyxy。不要混用。
- `objectId` 稳定且独立于数组顺序。未知帧不能当空帧，缺失媒体不能用占位图导出。
- 保留版本校验、幂等键、事务回滚、失败待重试状态和日志。不要把网络失败当成功或空数据。
- 内网客户端兼容 Chrome 93 / HTTP；超时和 UUID 使用 `frontend/src/utils/browserCompat.ts`，不要直接依赖新版 API。素材列表只提供经确认的删除，不提供“关闭素材”；服务器视频与本地图片的删除范围须明确，被历史审查基准引用的源视频不可删。
- 只支持一个后端进程/worker。测试设置独立 `APP_DATA_DIR`、`APP_DB_FILE`、`APP_STORAGE_DIR`，禁止向用户业务目录生成夹具。

## 节省上下文

本仓库文档和核心源文件都很大（`docs/` 主文档合计约 200 KB；`frontend/src/pages/AnnotatePage.vue` 约 98 KB），整份读取会迅速耗尽上下文和额度。

- 文档：先读开头的“要点”，再 `grep -n '^#' <文件>` 看章节，按行号只读相关章节；超过 20 KB 的文档不要整份读取。只在少数任务用到的细节在 `docs/details/`，需要时才读。各文档大小见 [docs/README.md](docs/README.md)。
- 源码：超过 500 行的文件先 `grep -n` 定位函数或关键字，再读附近几十到一两百行；不要整份读取。大文件清单见 [PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md#大文件定位)。
- 不要搜索或列出 `work/`（数十 GB 的日志、夹具和旧代码副本，内容可能是过时版本），只读 `work/HANDOFF.md` 和本次任务明确指出的文件。`docs/archive/` 只在追溯具体历史原因时读取。
- 测试和构建输出写入 `work/` 下的日志文件，对话里只看结尾摘要或失败部分（例如 `pytest -q … 2>&1 | tail -20`）；不要把完整日志、完整 diff 或大段 JSON 贴进对话。
- 一个会话只做一个任务；任务完成就更新 `work/HANDOFF.md` 并结束，下一个任务开新会话，不要在长会话里连续处理多件无关工作。

## 测试分层

命令和用例选择见 [TESTING.md](docs/TESTING.md)。

| 时机 | 必须运行 |
| --- | --- |
| 开发中和提交前 | 只跑改动相关的专项 pytest / 前端单元测试；改了前端时加 `npm run build`。**不跑全量测试和浏览器回归**，界面问题由团队使用时发现后再修 |
| 开 PR / 推送 main | CI 跑后端 pytest、前端单元测试、构建、仓库检查与 Docker 部署冒烟；不跑浏览器回归（GitHub 托管机器上计时等待会偶发超时） |
| 打版本标签发布 | main 上 CI 通过即可；`release-images.yml` 会再跑完整 CI。不要求本机跑浏览器回归 |
| 用户明确要求或排查某流程时 | `run_browser_regression.py`，可用 `--only <相关套件>` |

- 缺陷先补能在旧行为下失败的行为测试；正常状态与故障恢复成对验证。静态字符串断言、构建通过、测试数量都不能替代用户完整流程。
- `--only` / `--critical-only` 的结果不能当作完整回归通过；不能拼接分批结果。
- 汇报时分别列出实际通过、失败、未执行和模拟的范围（浏览器回归中的模型输出是模拟的，不代表真实 GPU 验收）。

## 与其他 AI 工具交接

- 同一时间只让一个工具在本工作目录写代码；需要并行时每个任务用独立 git worktree，步骤见 [TESTING.md 的并行开发](docs/TESTING.md#多个-ai-工具并行开发)。
- 每次结束一段工作前，覆盖更新 `work/HANDOFF.md`（已被 Git 忽略），格式见 [docs/HANDOFF_TEMPLATE.md](docs/HANDOFF_TEMPLATE.md)：当前任务、分支、已完成、下一步、等用户确认的问题、不要碰的文件。
- 稳定规则写进对应 `docs/*.md`；重大方案取舍写一篇 `docs/decisions/NNNN-标题.md`；验证结果写在提交信息或 PR 描述里。不新增“本次实现/最终审查/优化总结”类文档。
- 提交信息说明改了什么、为什么、跑了哪些测试。

## 文档维护

- 当前文档直接改写原文，不追加带日期的“当前入口”段落；被取代的规则直接删除，需要追溯时看 git 历史或 `docs/archive/`。
- 工作台布局遵守 [工作台布局与扩展规则](docs/WORKFLOW.md#工作台布局与扩展规则)：标注与审查共用 `frontend/src/components/WorkbenchShell.vue` 与 `frontend/src/workbench/shell.css`，不要另起一套布局或重复常驻统计。
- 用户可见流程或快捷键改变时，同步维护 [用户使用说明](frontend/src/help/user-guide.md)（前端说明窗口与 Markdown 下载的唯一来源），并验证标注、审查、记录三页在小窗口和浅深主题中的位置、可见性及原有流程。
- 入口与阅读顺序见 [docs/README.md](docs/README.md)。`docs/archive/` 只用于追溯。日志、截图、临时分析放在 `work/`、`output/playwright/`；测试 token 和业务数据不写入文档。

## 交付与部署红线

细节见 [DOCKER_DEPLOYMENT.md](docs/DOCKER_DEPLOYMENT.md)、[STORAGE_LAYOUT.md](docs/STORAGE_LAYOUT.md)。

- 运行数据、业务视频/训练集和大型 SAM3 权重不进 Git，也不进 Docker build context。唯一例外是 `backend/app/model_assets/head-detector.pt`（约 19.4 MiB，附固定清单与许可），不能扩展到其他权重或数据。
- SAM3 随仓库 Release 分发，Git 只保留 `model-distribution/` 的固定清单与许可；部署先运行独立 `model-setup` 容器。公开模型包不是用户业务数据，清理时不要混淆。
- 部署修改用隔离 Compose 项目验证；CPU 容器测试不能当成真实 GPU 验收。首次部署默认 GPU + AI，CPU 仅人工模式须显式选择；已有 `.env` 不自动切换模式。
- 医院离线升级必须用 `build-offline.ps1` 制作固定提交的完整镜像包，再用 `update-offline.sh` 升级；不能只换脚本或沿用旧 `images.tar`。`scripts/offline_legacy.json` 的 `contract` 是兼容承诺：破坏性数据库/环境/模型变更必须实现迁移并调整契约，不能把未知版本加入白名单绕过检查。回滚必须恢复同批数据，并保存回滚时的新数据副本。`.offline/` 不纳入 Git，也不自动清理。
