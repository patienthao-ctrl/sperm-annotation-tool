#!/usr/bin/env python3
"""Download every asset of a hospital offline-update Release and verify SHA256SUMS (macOS/Linux).

Same behaviour as download-offline-release.ps1. Uses only public GitHub URLs (no login).
  python3 download-offline-release.py                 # newest vX.Y.Z offline package
  python3 download-offline-release.py --version v1.3.0
Copy the whole output folder to the hospital server and run install-offline.sh there.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

REPOSITORY = 'huangwww11222/sperm-annotation'
RELEASE_TAG = re.compile(r'^v[0-9]+\.[0-9]+\.[0-9]+$')
ASSET_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


class DownloadError(RuntimeError):
    pass


def fetch(url, accept='application/vnd.github+json'):
    request = urllib.request.Request(url, headers={'Accept': accept, 'User-Agent': 'sperm-annotation-offline-download'})
    return urllib.request.urlopen(request, timeout=60)


def offline_package(release):
    names = [a['name'] for a in release.get('assets', [])]
    return 'SHA256SUMS' in names and any(n.endswith('-files.tar.gz') for n in names)


def find_release(api, repository, version):
    if version == 'latest':
        with fetch(f'{api}/repos/{repository}/releases?per_page=100') as response:
            releases = json.load(response)
        # Other releases (e.g. the SAM3 model bundle) share the repository: pick offline packages only.
        for release in releases:
            if not release.get('draft') and not release.get('prerelease') and RELEASE_TAG.fullmatch(release.get('tag_name', '')) and offline_package(release):
                return release
        raise DownloadError('没有找到已发布的离线更新包（vX.Y.Z）')
    try:
        with fetch(f'{api}/repos/{repository}/releases/tags/{version}') as response:
            release = json.load(response)
    except urllib.error.HTTPError as error:
        raise DownloadError(f'找不到版本 {version}（HTTP {error.code}）') from None
    if not offline_package(release):
        raise DownloadError(f'{version} 不是离线更新包（缺少 SHA256SUMS 或 *-files.tar.gz）')
    return release


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def download(url, target):
    partial = target.with_name(target.name + '.partial')
    with fetch(url, 'application/octet-stream') as response, partial.open('wb') as output:
        for block in iter(lambda: response.read(8 << 20), b''):
            output.write(block)
    partial.replace(target)


def main():
    parser = argparse.ArgumentParser(description='下载医院离线更新包的全部 Release 附件并核对 SHA256SUMS')
    parser.add_argument('--version', default='latest', help='版本标签，例如 v1.3.0；默认最新')
    parser.add_argument('--output', type=Path, help='保存目录，默认 ./sperm-annotation-<版本>')
    parser.add_argument('--repository', default=REPOSITORY)
    parser.add_argument('--api-base', default='https://api.github.com')
    args = parser.parse_args()
    try:
        release = find_release(args.api_base.rstrip('/'), args.repository, args.version)
        tag = release['tag_name']
        output = (args.output or Path('sperm-annotation-' + tag)).resolve()
        output.mkdir(parents=True, exist_ok=True)
        assets = {a['name']: a['browser_download_url'] for a in release['assets']}
        for name in assets:
            if not ASSET_NAME.fullmatch(name):
                raise DownloadError('附件名无效：' + name)
        print(f'版本 {tag}，{len(assets)} 个附件 → {output}')
        sums = output / 'SHA256SUMS'
        download(assets['SHA256SUMS'], sums)
        expected = {}
        for line in sums.read_text().splitlines():
            digest, name = line.split('  ', 1)
            if not re.fullmatch('[0-9a-f]{64}', digest) or name not in assets:
                raise DownloadError('SHA256SUMS 与 Release 附件不符：' + name)
            expected[name] = digest
        missing = sorted(set(assets) - set(expected) - {'SHA256SUMS'})
        if missing:
            raise DownloadError('SHA256SUMS 未列出附件：' + '、'.join(missing))
        for name, digest in expected.items():
            target = output / name
            if target.is_file() and sha256(target) == digest:
                print('已下载并核对：' + name)
                continue
            for attempt in range(1, 4):
                print(f'下载 {name}（第 {attempt} 次）…', flush=True)
                try:
                    download(assets[name], target)
                except OSError as error:
                    print('  下载中断：' + str(error), file=sys.stderr)
                    continue
                if sha256(target) == digest:
                    break
                target.unlink()
                print('  SHA256 不符，重新下载', file=sys.stderr)
            else:
                raise DownloadError(name + ' 三次下载都未通过 SHA256 核对，请检查网络后重新运行（已核对的文件会跳过）')
        print(f'\n全部附件已下载并通过 SHA256 核对：{output}')
        print('把整个文件夹复制到医院服务器的新目录，在该目录执行：bash install-offline.sh /原部署目录')
        return 0
    except (DownloadError, OSError, ValueError, KeyError) as error:
        print('未完成：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
