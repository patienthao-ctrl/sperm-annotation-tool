"""Build release assets for the hospital offline update in CI (Linux).

Produces the same manifest.json as build-offline.ps1 from images already built
with the offline labels, but streams `docker save` into parts that fit GitHub
Release assets (each file < 2 GiB). The server rebuilds images.tar with
`update-offline.sh --assemble`, which checks every part and the whole archive.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

PACKAGE_FILES = ['update-offline.sh', 'export-audit.sh', 'scripts/offline_update.py', 'scripts/offline_legacy.json',
                 'compose.yaml', 'compose.gpu.yaml', '.env.docker.example']
# Run on the server before the tools archive is unpacked, so it is a separate Release asset.
STANDALONE_ASSETS = ['install-offline.sh']
GITHUB_ASSET_LIMIT = 2 * 1024**3
DEFAULT_PART_SIZE = 2_000_000_000
VERSION = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


class ReleaseError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def run(args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    require(result.returncode == 0, ' '.join(args) + ' failed: ' + result.stderr.strip())
    return result.stdout.strip()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_parts(stream, directory, name, part_size):
    """Split a byte stream into name.partNN files; returns the parts.json entry."""
    require(0 < part_size < GITHUB_ASSET_LIMIT, 'part size must be between 1 byte and 2 GiB')
    directory = Path(directory)
    whole = hashlib.sha256()
    parts, size, output = [], 0, None
    try:
        while True:
            if output is None:
                block = stream.read(min(8 * 1024 * 1024, part_size))
                if not block:
                    break
                path = directory / ('%s.part%02d' % (name, len(parts) + 1))
                output, digest, written = path.open('xb'), hashlib.sha256(), 0
            else:
                block = stream.read(min(8 * 1024 * 1024, part_size - written))
            if block:
                output.write(block)
                digest.update(block)
                whole.update(block)
                written += len(block)
                size += len(block)
            if not block or written == part_size:
                output.close()
                parts.append({'asset': path.name, 'size': written, 'sha256': digest.hexdigest()})
                output = None
                if not block:
                    break
    finally:
        if output is not None:
            output.close()
    require(size > 0, name + ' is empty')
    return {'name': name, 'size': size, 'sha256': whole.hexdigest(), 'parts': parts}


def split_file(path, part_size):
    """Split an existing file in place (removes the original); used for tests and manual packages."""
    path = Path(path)
    with path.open('rb') as stream:
        entry = write_parts(stream, path.parent, path.name, part_size)
    path.unlink()
    return entry


def image_identity(tag, revision, version, mode, cwd):
    items = json.loads(run(['docker', 'image', 'inspect', tag], cwd))
    require(len(items) == 1, 'Expected one image for ' + tag)
    item = items[0]
    require(item.get('Os') == 'linux' and item.get('Architecture') == 'amd64', 'Image must be linux/amd64: ' + tag)
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', item.get('Id', '')) and isinstance(item.get('Size'), int) and item['Size'] > 0,
            'Invalid image identity: ' + tag)
    labels = (item.get('Config') or {}).get('Labels') or {}
    require(labels.get('org.opencontainers.image.revision') == revision and labels.get('org.opencontainers.image.version') == version
            and labels.get('io.sperm-annotation.offline-contract') == '1' and labels.get('io.sperm-annotation.mode') == mode,
            'Image version labels do not match the selected source: ' + tag)
    return {'tag': tag, 'id': item['Id'], 'size': item['Size']}


def package(source, version, revision, mode, output, part_size):
    source = Path(source).resolve()
    output = Path(output).resolve()
    require(VERSION.fullmatch(version), 'Version must be 1-64 letters, digits, dots, underscores or hyphens')
    require(re.fullmatch('[0-9a-f]{40}', revision), 'Revision must be a full commit hash')
    require(mode in ('gpu', 'cpu'), 'Mode must be gpu or cpu')
    require(not output.exists(), 'Output already exists: ' + str(output))
    require(run(['git', 'status', '--porcelain', '--untracked-files=normal'], source) == '', 'Source checkout has uncommitted files')
    require(run(['git', 'rev-parse', 'HEAD'], source) == revision, 'Source checkout is not the selected commit')
    require(run(['git', 'rev-parse', '--is-shallow-repository'], source) == 'false', 'A complete Git history is required (fetch-depth: 0)')
    history = run(['git', 'rev-list', revision], source).splitlines()
    require(history and history[0] == revision and all(re.fullmatch('[0-9a-f]{40}', x) for x in history)
            and len(set(history)) == len(history), 'Git history must contain the selected commit and valid ancestors')
    for relative in PACKAGE_FILES + STANDALONE_ASSETS:
        require((source / relative).is_file(), 'Selected revision does not contain ' + relative)
    tags = {'backend': 'sperm-annotation-backend:%s-%s' % (version, mode), 'frontend': 'sperm-annotation-frontend:' + version}
    images = {name: image_identity(tag, revision, version, mode, source) for name, tag in tags.items()}

    stem = 'sperm-annotation-%s-%s-linux-amd64' % (version, mode)
    output.mkdir(parents=True)
    try:
        return _write_assets(source, version, revision, mode, output, part_size, history, tags, images, stem)
    except BaseException:
        # Never leave a partial asset set that could be uploaded by mistake.
        shutil.rmtree(output, ignore_errors=True)
        raise


def _write_assets(source, version, revision, mode, output, part_size, history, tags, images, stem):
    process = subprocess.Popen(['docker', 'save', tags['backend'], tags['frontend']], cwd=source, stdout=subprocess.PIPE)
    try:
        entry = write_parts(process.stdout, output, 'images.tar', part_size)
    finally:
        process.stdout.close()
        code = process.wait()
    require(code == 0, 'docker save failed (exit %d)' % code)
    # A concurrent retag during save would make the archive disagree with the manifest.
    for name, tag in tags.items():
        require(image_identity(tag, revision, version, mode, source)['id'] == images[name]['id'], 'An image tag changed during export: ' + tag)

    files = {'images.tar': entry['sha256']}
    files.update({relative: sha256_file(source / relative) for relative in PACKAGE_FILES})
    manifest = {
        'format': 1, 'version': version, 'revision': revision, 'sourceHistory': history,
        'createdAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'platform': 'linux/amd64', 'mode': mode,
        'contract': 1, 'fromContracts': [1], 'images': images, 'files': files,
    }
    parts = {'format': 1, 'partSize': part_size, 'files': [entry]}
    tools = output / (stem + '-files.tar.gz')
    with tarfile.open(tools, 'x:gz') as archive:
        for name, value in (('manifest.json', manifest), ('parts.json', parts)):
            data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, int(dt.datetime.now().timestamp())
            archive.addfile(info, io.BytesIO(data))
        for relative in PACKAGE_FILES:
            archive.add(source / relative, arcname=relative, recursive=False,
                        filter=lambda info: _plain(info, 0o755 if info.name.endswith('.sh') else 0o644))
    for name in STANDALONE_ASSETS:
        shutil.copyfile(source / name, output / name)
    assets = sorted(p.name for p in output.iterdir())
    for name in assets:
        require((output / name).stat().st_size < GITHUB_ASSET_LIMIT, 'Release asset is too large: ' + name)
    (output / 'SHA256SUMS').write_text(''.join('%s  %s\n' % (sha256_file(output / name), name) for name in assets))
    return manifest, parts, tools


def _plain(info, mode):
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    info.mode = mode
    return info


def release_notes(manifest, tools_name):
    return '\n'.join([
        '医院离线更新包：%s（GPU，linux/amd64），源码提交 `%s`。' % (manifest['version'], manifest['revision']),
        '',
        '1. 联网电脑一条命令下载本页全部附件并核对 SHA256（Windows PowerShell；macOS/Linux 用同名 .py）：',
        '```powershell',
        '.\\download-offline-release.ps1 -Version %s' % manifest['version'],
        '```',
        '   脚本在仓库根目录；也可手工下载本页全部附件（%s、`images.tar.partNN`、install-offline.sh、SHA256SUMS）放进同一文件夹。' % tools_name,
        '2. 把整个文件夹传入医院服务器的新目录（例如 `/opt/sperm-updates/%s/`），在该目录执行：' % manifest['version'],
        '```bash',
        'bash install-offline.sh /原部署目录',
        '```',
        '   脚本依次核对附件、解开工具、合并校验分块、升级前检查，经确认后升级并显示状态；任一步失败即停止。',
        '',
        '分步命令、回滚和故障恢复见仓库 docs/DOCKER_DEPLOYMENT.md 第 6、8 节。SHA256 只用于发现传输损坏，请只从本仓库 Releases 页面下载。',
        '',
    ])


def main():
    parser = argparse.ArgumentParser(description='Build GitHub Release assets for the hospital offline update')
    parser.add_argument('--version', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--mode', default='gpu', choices=['gpu', 'cpu'])
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument('--part-size', default=DEFAULT_PART_SIZE, type=int)
    parser.add_argument('--notes', type=Path, help='write Release notes to this file (outside --output)')
    args = parser.parse_args()
    try:
        manifest, _, tools = package(args.source, args.version, args.revision, args.mode, args.output, args.part_size)
        if args.notes:
            args.notes.write_text(release_notes(manifest, tools.name), encoding='utf-8')
        print('Release assets: ' + str(args.output))
        return 0
    except (ReleaseError, OSError, ValueError) as error:
        print('Packaging failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
