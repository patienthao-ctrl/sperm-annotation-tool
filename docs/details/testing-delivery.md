# 交付、Docker 与离线升级验收

修改部署、镜像、离线升级或数据库升级时读。返回 [TESTING.md](../TESTING.md)。

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

`--fresh` 验证空数据库起步，已有测试数据时使用新隔离目录。脚本生成真实 AVI，验证注册、人工保存和查询（防止 db.py 参数回退）、工作区、开始审查并逐项检查后显式“审查通过”、导出快照、YOLO 图像/标签，以及重建后令牌/数据/审查通过状态/训练包。只允许 loopback 地址和 `work/` 下数据；恢复凭据写入已忽略的 `work/docker-smoke-state.json`，不要上传它。

后端也在同一 Python 3.12 镜像中回归，隔离变量设置方法见 `.github/workflows/ci.yml`。Bash 初始化由 pytest 的模拟 Docker 用例验证随机密钥、重复运行和模式冲突保护；Windows CI 通过 `scripts/test_deploy.ps1` 做相同的 PowerShell 配置测试，不代表 WSL2/GPU 的实际服务器验收。

`.github/workflows/ci.yml` 在 PR/main 验证；`release-images.yml` 在版本标签推送后调用验证，再发布镜像。新增工作流需推送后实际运行，不能把本地配置校验说成 GitHub Actions 已通过。镜像需要在 Packages 确认访问权限。

### 完整离线升级验收

服务端工具回归使用模拟 Docker 和临时目录，制作端回归使用 PowerShell 与模拟 Git/Docker。它们不访问医院服务器、不使用业务数据；在仓库根目录运行：

```bash
APP_DATA_DIR="$PWD/work/offline-update-test-data" \
APP_DB_FILE="$PWD/work/offline-update-test-data/app.db" \
APP_STORAGE_DIR="$PWD/work/offline-update-test-storage" \
PYTHONPATH=backend work/review-venv/bin/python -m pytest backend/tests/test_offline_update.py backend/tests/test_offline_release.py -q
bash -n update-offline.sh
python3 scripts/check_repository.py
```

```powershell
.\scripts\test_build_offline.ps1
```

集成验收必须另建隔离 Compose 项目，沿用上方 `scripts/docker_smoke.py` 的新测试数据目录，使用已知基线镜像创建账号、素材、工作区、B/C/F 和训练包。新包按 [离线升级流程](../DOCKER_DEPLOYMENT.md#医院内网可复用的离线升级流程) 构建；不能直接以可变本地源码替代指定提交快照。先执行 `--check` 并确认旧服务仍可用，再执行真实升级和 `--verify-restart`，核对镜像、版本状态与现有登录令牌/数据保留。必须另验证重复安装、坏包/未知旧版/运行配置变化拒绝、升级失败恢复、中断后 `--recover`，以及完成升级后的 `--rollback <编号> --restore-data`。回滚后核对恢复数据及回滚前数据副本；不得只凭 HTTP 200 判定保留了业务状态。

`test_offline_release.py` 用模拟 Git/Docker 和几十字节的分块验证 Releases 附件：分块恰好等于、超过一块边界，缺块、同大小篡改、截断、混入其他版本分块及清单不一致都被拒绝且不留下半个 `images.tar`，下载→解开→`--assemble` 后字节与原归档一致并通过 `verify_bundle`。它不运行 GitHub Actions；`offline-package` 作业的托管机器磁盘空间、真实 GPU 镜像大小和上传须由第一次真实版本标签的运行记录确认。

测试记录应明确区分模拟 Docker、真实 CPU 容器和真实 GPU 服务器。Windows PowerShell 脚本通过不代表 Windows Docker 已构建出 GPU 包；CPU 镜像通过不代表 Linux amd64 / NVIDIA L20 上的推理通过。GitHub Actions 结果以实际运行记录为准。
