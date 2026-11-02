# 当前文档索引

第三方安装先读 [项目首页](../README.md) 和 [Docker 部署说明](DOCKER_DEPLOYMENT.md)。本索引主要服务开发者和 AI 编程工具；所有工具共用根目录 [AGENTS.md](../AGENTS.md) 作为入口（`CLAUDE.md` 只引用它）。

## 按任务阅读

| 文档 | 唯一职责 | 何时阅读 |
| --- | --- | --- |
| [WORKFLOW.md](WORKFLOW.md) | 标注 → 审查 → 导出；审查清单与通过规则、导出门槛、追踪流程、工作台布局 | 所有业务修改前 |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | 实际代码地图、启动、配置入口和兼容层 | 接手项目、找实现 |
| [ANNOTATION.md](ANNOTATION.md) | 标注与审查共用画布、稳定 ID、追踪回退、快捷键与性能 | 改标注体验或追踪 |
| [API.md](API.md) | 当前路由、结构、并发/幂等/错误规则及源码入口 | 改接口、数据结构 |
| [STORAGE_LAYOUT.md](STORAGE_LAYOUT.md) | 数据落点、历史 A/B/C/F 表、备份、迁移与清理边界 | 改持久化、部署、排查数据 |
| [TESTING.md](TESTING.md) | 测试分层、隔离夹具、固定回归、日志排查 | 实现与交付 |
| [DEPLOYMENT_WINDOWS.md](DEPLOYMENT_WINDOWS.md) | Windows 本机运行、生产部署与验收 | Windows 环境 |
| [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md) | 一键 CPU/GPU 部署、模型准备、离线升级与恢复 | 第三方安装与运维 |
| [details/testing-specialty.md](details/testing-specialty.md) | 各专项测试覆盖的场景与限制 | 按 TESTING 表找到修改面后 |
| [details/testing-browser-suites.md](details/testing-browser-suites.md) | 七组浏览器套件内容、4K 性能、Chrome 93 专项 | 修改或排查某套件 |
| [details/testing-delivery.md](details/testing-delivery.md) | Docker、离线升级验收 | 部署、镜像、数据库升级 |
| [details/hospital-server.md](details/hospital-server.md) | 医院离线服务器的系统、GPU、驱动、Docker 版本与网络条件 | 制作医院更新包、判断兼容或估算空间 |
| [details/tracking-pause-policy.md](details/tracking-pause-policy.md) | 旧暂停兼容路径：暂停核对、异常判定、原图/Mask 证据 | 改异常判定或暂停核对界面 |
| [decisions/](decisions/README.md) | 重大方案取舍的原因 | 想改回某个已定方案前 |
| [HANDOFF_TEMPLATE.md](HANDOFF_TEMPLATE.md) | `work/HANDOFF.md` 交接记录格式 | 每段工作开始和结束 |

前后端目录的 README 只提供入口链接。

## 怎样读大文档

每份文档开头有几行“要点”，先读要点；再用 `grep -n '^#' docs/<文件>` 看章节，只读与任务相关的章节。当前大小（KB）：WORKFLOW 34、API 36、STORAGE_LAYOUT 29、ANNOTATION 26、DOCKER_DEPLOYMENT 30、TESTING 19、PROJECT_STRUCTURE 12。新增内容放进已有章节；一节超过约 5 KB、且只在少数任务中用到时，移到 `details/`。

## 给工具使用者的说明

[用户使用说明](../frontend/src/help/user-guide.md) 面向标注员和审查员，覆盖完整流程、保存与续做、快捷键、训练集导出及常见问题。前端顶部“使用说明”按当前页面打开章节、支持搜索并可下载同一份 Markdown。它随前端一起打包，是唯一来源；不替代上表中的开发契约。

## 信息放在哪里

| 内容 | 位置 |
| --- | --- |
| 现行规则 | 上表对应文档，直接改写原文，不加日期段落 |
| 为什么这样定 | `docs/decisions/NNNN-标题.md` |
| 进行中的工作、等用户确认的问题 | `work/HANDOFF.md`（不进 Git，每次覆盖） |
| 一次验证的结果 | 提交信息或 PR 描述 |
| 日志、截图、临时分析 | `work/`、`output/playwright/` |
| 历史需求、旧原型、旧报告 | [archive/](archive/README.md)，只用于追溯 |

## 以后怎样维护

- 用户确认的新要求更新到对应当前文档，必要时补测试；不追加新的交接或总结文档。
- 规则被取代时删除旧段落，不保留“当前入口（某日期）”式的叠加；需要追溯时看 git 历史或 archive。
- 记录验证必须注明范围与限制，不能把曾经通过的数量当作以后每次提交的通过结果。
- 文档移动后检查相对链接；命令引用必须指向仍存在的脚本。
