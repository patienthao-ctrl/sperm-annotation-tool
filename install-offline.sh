#!/usr/bin/env bash
# One-command hospital upgrade from a downloaded Release folder:
# verify downloads -> unpack tools -> --assemble -> --check -> confirm -> upgrade -> --status.
# Only chains update-offline.sh; every version, contract, backup and recovery check stays there.
set -euo pipefail

usage() { echo "用法：bash install-offline.sh /原部署目录   （在放有本版本全部 Release 附件的目录中执行）" >&2; exit 2; }
[[ $# -eq 1 && $1 != -* ]] || usage
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
deployment=$1
step=0
total=6
fail() { echo "第 ${step}/${total} 步失败：$1" >&2; echo "已停止。按上面的原因处理后，重新运行本脚本即可（已完成的合并会复核后跳过）。" >&2; exit 1; }
begin() { step=$((step + 1)); echo; echo "[$step/$total] $1"; }

[[ -d $deployment && -f $deployment/.env ]] || { echo "原部署目录不存在或缺少 .env：$deployment" >&2; exit 2; }
deployment=$(cd -- "$deployment" && pwd)
[[ $deployment != "$package" ]] || { echo "原部署目录不能是更新包目录；请把附件放在独立的新目录中。" >&2; exit 2; }
command -v python3 >/dev/null || { echo '需要 Python 3（Ubuntu 24.04 已提供）。' >&2; exit 1; }
cd -- "$package"

begin '核对下载的附件'
if [[ -f SHA256SUMS ]]; then
  python3 - <<'PY' || fail '附件 SHA256 不符或缺少附件，请重新下载出错的文件（不要改动 SHA256SUMS）。'
import hashlib, pathlib, sys
bad = []
for line in pathlib.Path('SHA256SUMS').read_text().splitlines():
    digest, name = line.split('  ', 1)
    path = pathlib.Path(name)
    if '/' in name or not path.is_file():
        bad.append(name + '（缺失）'); continue
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''): h.update(block)
    if h.hexdigest() != digest: bad.append(name + '（损坏）')
if bad: print('附件有问题：' + '、'.join(bad), file=sys.stderr); sys.exit(1)
print('全部附件 SHA256 一致。')
PY
else
  echo '没有 SHA256SUMS（手工制作的完整包），跳过；manifest.json 的校验在第 3、4 步进行。'
fi

begin '解开升级工具'
shopt -s nullglob
tools=(*-files.tar.gz)
shopt -u nullglob
if (( ${#tools[@]} > 1 )); then fail "目录中有多个 *-files.tar.gz：${tools[*]}；每个版本使用独立目录。"
elif (( ${#tools[@]} == 1 )); then
  tar -xzf "${tools[0]}" --no-same-owner || fail "无法解开 ${tools[0]}"
  echo "已解开 ${tools[0]}"
elif [[ -f manifest.json && -f update-offline.sh ]]; then
  echo '手工完整包，已包含升级工具。'
else
  fail '找不到 *-files.tar.gz 或 manifest.json；请确认已下载本版本的全部附件并放在本脚本所在目录。'
fi
[[ -f update-offline.sh && -f manifest.json ]] || fail '升级工具不完整（缺少 update-offline.sh 或 manifest.json）。'

begin '合并并校验镜像分块'
if [[ -f parts.json ]]; then
  bash update-offline.sh --assemble || fail '分块合并或校验未通过，请按提示重新下载对应分块。'
else
  echo '没有分块（手工完整包），跳过。'
fi

begin '升级前检查（不停服务、不导入镜像）'
bash update-offline.sh "$deployment" --check || fail '升级前检查未通过；原服务仍在运行，未做任何改动。'

begin '确认停机'
echo '升级会短暂停止服务，并先完整备份数据库和素材。'
echo '请确认：所有使用者已保存并退出，当前没有 AI 追踪或导出任务。'
read -r -p '确认后输入 yes 开始升级，其他输入取消：' answer || answer=''
[[ $answer == yes ]] || fail '未确认，已取消；原服务未停止。'

begin '安装更新'
bash update-offline.sh "$deployment" || fail '升级未完成；请查看上方日志。升级器已按记录恢复或保留恢复信息，可运行 bash update-offline.sh 原部署目录 --recover。'

echo
echo '当前状态：'
bash update-offline.sh "$deployment" --status || true
cat <<'EOF'

升级完成。请继续人工验收：
  1. 浏览器按 Ctrl+F5 刷新，用原账号登录，核对原有素材、标注和审查结果；
  2. 用测试视频检查上传抽帧、审查通过、删除后刷新和训练数据集导出；
  3. 执行一次真实 AI Tracking（GPU 推理以实际追踪结果为准）。
需要回退时使用上方升级日志给出的 --rollback 命令。
EOF
