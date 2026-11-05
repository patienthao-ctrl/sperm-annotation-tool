"""Offline application upgrades, contract 1. No network access or dependency installs.

The package is trusted publisher input; SHA256 detects damaged transfers, not
publisher identity. Only existing single-process Compose installations qualify.
Resolved Compose and snapshots contain secrets and remain private on the host.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tarfile
import uuid

CONTRACT = 1
REQUIRED = {'images.tar', 'update-offline.sh', 'scripts/offline_update.py', 'scripts/offline_legacy.json'}
PARTS_FILE = 'parts.json'
DIGEST = re.compile(r'^[0-9a-f]{64}$')
IMAGE_ID = re.compile(r'^sha256:[0-9a-f]{64}$')
VERSION = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$')
FINGERPRINT_CODE = """import pathlib,hashlib,json
root=pathlib.Path('/app/backend')
records={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root/'app').rglob('*.py')) if '__pycache__' not in p.parts}
print(hashlib.sha256(json.dumps(records,sort_keys=True,separators=(',',':')).encode()).hexdigest())
"""
BUSY_CODE = """import urllib.request,json,sqlite3,os
s=json.load(urllib.request.urlopen('http://127.0.0.1:3000/api/health',timeout=15))
assert s.get('ok'), 'backend unhealthy'
a=s['sam3']
assert not(a.get('running') or a.get('queued') or a.get('trackingBusy')), 'AI tracking busy'
c=sqlite3.connect('file:'+os.environ['APP_DB_FILE']+'?mode=ro',uri=True)
assert not c.execute("SELECT COUNT(*) FROM training_exports WHERE state IN ('queued','running')").fetchone()[0], 'training export busy'
print('idle')
"""


class UpdateError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise UpdateError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temp.open('x', encoding='utf-8', newline='\n') as stream:
        os.chmod(temp, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def verify_bundle(root):
    root = Path(root).resolve()
    manifest = read_json(root / 'manifest.json')
    require(manifest.get('format') == 1 and manifest.get('contract') == CONTRACT and CONTRACT in manifest.get('fromContracts', [1]), '更新包格式或兼容契约不支持')
    require(VERSION.fullmatch(manifest.get('version', '')), '更新包版本号无效')
    require(re.fullmatch('[0-9a-f]{40}', manifest.get('revision', '')), '缺少固定源码提交号')
    history = manifest.get('sourceHistory')
    require(isinstance(history, list) and history and manifest['revision'] in history
            and all(isinstance(x, str) and re.fullmatch('[0-9a-f]{40}', x) for x in history), '缺少有效的源码祖先记录')
    require(manifest.get('mode') in ('gpu', 'cpu'), '更新包模式无效')
    require(manifest.get('platform') in ('linux/amd64', 'linux/arm64'), '更新包平台无效')
    files = manifest.get('files', {})
    require(REQUIRED <= files.keys(), '更新包缺少必需文件清单')
    for name, checksum in files.items():
        path = root / name
        require(path.exists() or path.is_symlink() or not (root / PARTS_FILE).is_file(),
                '更新包是分块发布的，请先运行 bash ' + str(root / 'update-offline.sh') + ' --assemble 合并并校验')
        require(not Path(name).is_absolute() and '..' not in Path(name).parts and '\\' not in name,
                '更新包文件路径无效')
        require(not path.is_symlink() and root in path.resolve().parents and path.is_file(), '更新包文件缺失或符号链接：' + name)
        require(isinstance(checksum, str) and DIGEST.fullmatch(checksum), '更新包摘要无效：' + name)
        require(sha256_file(path) == checksum, '更新包文件校验失败：' + name)
    require(set(manifest.get('images', {})) == {'backend', 'frontend'}, '更新包镜像清单无效')
    for value in manifest['images'].values():
        require(IMAGE_ID.fullmatch(value.get('id', '')), '镜像 ID 无效')
        require(isinstance(value.get('size'), int) and value['size'] > 0, '镜像大小无效')
        require(isinstance(value.get('tag'), str) and value['tag'] and not value['tag'].startswith('-'), '镜像标签无效')
    return manifest


def assemble(root, remove_parts=False):
    """Rebuild files split for release hosting; bytes must equal manifest.json."""
    root = Path(root).resolve()
    require((root / PARTS_FILE).is_file(), '更新包没有分块清单 ' + PARTS_FILE + '，无需合并')
    expected = read_json(root / 'manifest.json').get('files', {})
    plan = read_json(root / PARTS_FILE)
    require(plan.get('format') == 1 and isinstance(plan.get('files'), list) and plan['files'], '分块清单格式不支持')
    for entry in plan['files']:
        name = entry.get('name', '')
        require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name), '分块清单文件名无效')
        size, checksum, parts = entry.get('size'), entry.get('sha256'), entry.get('parts')
        require(isinstance(size, int) and size > 0 and isinstance(checksum, str) and DIGEST.fullmatch(checksum), '分块清单整包信息无效：' + name)
        require(expected.get(name) == checksum, '分块清单与 manifest.json 不属于同一更新包：' + name)
        require(isinstance(parts, list) and parts, '分块清单缺少分块：' + name)
        pattern = re.compile(re.escape(name) + r'\.part([0-9]{2,})')
        for index, part in enumerate(parts, 1):
            match = pattern.fullmatch(str(part.get('asset', '')))
            require(match and int(match.group(1)) == index, '分块清单的分块名或顺序无效：' + name)
            require(isinstance(part.get('size'), int) and part['size'] > 0 and DIGEST.fullmatch(str(part.get('sha256', ''))), '分块信息无效：' + part['asset'])
        require(sum(part['size'] for part in parts) == size, '分块大小合计与整包不符：' + name)
        listed = {part['asset'] for part in parts}
        extra = sorted(p.name for p in root.iterdir() if pattern.fullmatch(p.name) and p.name not in listed)
        require(not extra, '目录中有清单以外的分块，可能混入了其他版本：' + '、'.join(extra))
        target = root / name
        if target.exists() or target.is_symlink():
            require(not target.is_symlink() and target.is_file() and sha256_file(target) == checksum,
                    name + ' 已存在但与清单不符；删除它后重新运行 --assemble')
            print(name + ' 已合并并校验通过。', flush=True)
        else:
            missing = [part['asset'] for part in parts if not (root / part['asset']).is_file() or (root / part['asset']).is_symlink()]
            require(not missing, '缺少分块，请重新下载：' + '、'.join(missing))
            wrong = [part['asset'] for part in parts if (root / part['asset']).stat().st_size != part['size']]
            require(not wrong, '分块大小不符（下载不完整或已损坏），请重新下载：' + '、'.join(wrong))
            require(shutil.disk_usage(root).free > size + 512 * 1024**2, '更新包目录磁盘空间不足，合并需要约 ' + str(size) + ' 字节')
            temp = root / ('.' + name + '.assembling-' + uuid.uuid4().hex)
            whole = hashlib.sha256()
            try:
                with temp.open('xb') as output:
                    for part in parts:
                        print('校验并合并 ' + part['asset'] + ' …', flush=True)
                        digest = hashlib.sha256()
                        with (root / part['asset']).open('rb') as stream:
                            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                                digest.update(block)
                                whole.update(block)
                                output.write(block)
                        require(digest.hexdigest() == part['sha256'], '分块校验失败（已损坏或被修改），请重新下载：' + part['asset'])
                    output.flush()
                    os.fsync(output.fileno())
                require(whole.hexdigest() == checksum and temp.stat().st_size == size, '合并结果与整包校验值不符：' + name)
                os.replace(temp, target)
                sync_directory(root)
            finally:
                if temp.exists():
                    temp.unlink()
            print(name + ' 合并完成，SHA256 与清单一致。', flush=True)
        if remove_parts:
            for part in parts:
                (root / part['asset']).unlink(missing_ok=True)
    print('合并完成。下一步：bash ' + str(root / 'update-offline.sh') + ' /原部署目录 --check', flush=True)


def runtime_env(container):
    return dict(item.split('=', 1) for item in container['Config'].get('Env', []) if '=' in item)


def validate_runtime(config, backend, frontend):
    services = config.get('services', {})
    require({'backend', 'frontend'} <= services.keys() and not(set(services) - {'backend', 'frontend', 'model-setup'}), '只支持独立的前后端 Compose 项目')
    env = runtime_env(backend)
    require(env.get('APP_DATA_DIR') == '/data/database' and env.get('APP_DB_FILE') == '/data/database/app.db'
            and env.get('APP_STORAGE_DIR') == '/data/storage', '数据库或存储容器路径不符合当前兼容契约')
    require(len(env.get('JWT_SECRET', '')) >= 32, '当前登录密钥配置无效')
    mode = 'gpu' if env.get('SAM3_ENABLED', '').lower() == 'true' else 'cpu'
    targets = {'/data/database', '/data/storage'} | ({'/models/sam3'} if mode == 'gpu' else set())
    mounts = backend.get('Mounts', [])
    require({v['Destination'] for v in mounts} == targets and len(mounts) == len(targets), '存在未支持的额外挂载，或业务数据未持久化')
    require(not frontend.get('Mounts'), '前端存在挂载覆盖，需先人工核对')
    paths = {}
    for mount in mounts:
        require(mount['Type'] == 'bind' and Path(mount['Source']).is_absolute(), '仅支持绝对路径的持久化 bind 挂载')
        path = Path(mount['Source'])
        require(path.is_dir() and not path.is_symlink(), '实际挂载目录缺失或是符号链接')
        key = {'/data/database': 'database', '/data/storage': 'storage', '/models/sam3': 'model'}[mount['Destination']]
        require(mount.get('RW', True) == (key != 'model'), '业务目录或模型的挂载读写方式不符合契约')
        if key != 'model':
            require(not os.path.ismount(path), '业务目录必须是磁盘挂载点内的子目录，以支持数据恢复')
        paths[key] = str(path.resolve())
    paths.setdefault('model', None)
    all_paths = [Path(p) for p in paths.values() if p]
    require(all(a != b and a not in b.parents and b not in a.parents for i, a in enumerate(all_paths) for b in all_paths[i+1:]), '数据和模型目录不得重叠')
    if mode == 'gpu':
        require(env.get('SAM3_MODEL_ID') == '/models/sam3' and env.get('SAM3_DEVICE', '').startswith('cuda'), 'AI 模型路径或设备配置不匹配')
        require(backend.get('HostConfig', {}).get('DeviceRequests'), '当前容器缺少 GPU 配置')
    for name, container in [('backend', backend), ('frontend', frontend)]:
        service = services[name]
        current = runtime_env(container)
        require(all(value is not None and current.get(key) == str(value) for key, value in service.get('environment', {}).items()), 'Compose 环境与运行容器不一致：' + name)
        expected = {(v['source'], v['target'], not v.get('read_only', False)) for v in service.get('volumes', []) if v.get('type') == 'bind'}
        actual = {(v['Source'], v['Destination'], v.get('RW', True)) for v in container.get('Mounts', [])}
        require(expected == actual, 'Compose 挂载与运行容器不一致：' + name)
        require(not service.get('privileged') and not service.get('network_mode') and not service.get('entrypoint') and not service.get('command'), '存在自定义启动或权限配置，需人工核对：' + name)
    require(not services['backend'].get('ports'), '后端直接暴露端口，不能确保升级期间停止外部写入')
    expected_ports = {(str(p['published']), int(p['target']), p.get('host_ip', '0.0.0.0')) for p in services['frontend'].get('ports', [])}
    actual_ports = {(p['HostPort'], int(port.split('/')[0]), p.get('HostIp', '0.0.0.0')) for port, bindings in frontend['HostConfig'].get('PortBindings', {}).items() for p in (bindings or [])}
    require(expected_ports == actual_ports, 'Compose 端口与运行容器不一致')
    require((Path(paths['database']) / 'app.db').is_file(), '数据库文件缺失，禁止以空库替代')
    paths['mode'] = mode
    return paths


def identify_version(backend, state, legacy, fingerprint):
    if state:
        require(state.get('contract') == CONTRACT, '已安装版本兼容契约不支持')
        require(backend['Image'] == state['images']['backend']['id'], '版本记录与正在运行的后端镜像不一致')
        require(fingerprint == state.get('backendFingerprint'), '后端代码与安装记录不一致')
        return state
    record = legacy.get('backends', {}).get(fingerprint)
    require(record is not None, '无法识别旧部署的代码指纹，未修改服务。实际指纹：' + fingerprint)
    return {'version': 'legacy-' + record['revision'][:12], 'revision': record['revision'], 'contract': CONTRACT}


def archive_image_ids(path):
    """{tag: {image IDs}} declared by a verified images.tar, for either image store.

    Docker names one image by its config digest under overlay2 and by its manifest
    digest under the containerd store, so the ID depends on the daemon that loads it.
    """
    ids = {}
    try:
        with tarfile.open(path, 'r:') as archive:
            def member(name):
                try:
                    handle = archive.extractfile(name)
                except KeyError:
                    return None
                return json.load(handle) if handle else None
            for entry in member('manifest.json') or []:
                digest = re.search('[0-9a-f]{64}', entry.get('Config', ''))
                for tag in (entry.get('RepoTags') or []) if digest else []:
                    ids.setdefault(tag, set()).add('sha256:' + digest.group())
            for entry in (member('index.json') or {}).get('manifests', []):
                name = (entry.get('annotations') or {}).get('io.containerd.image.name', '')
                if name and IMAGE_ID.fullmatch(entry.get('digest', '')):
                    ids.setdefault(re.sub('^docker[.]io/library/', '', name), set()).add(entry['digest'])
    except (tarfile.TarError, OSError, ValueError, AttributeError) as error:
        raise UpdateError('无法读取更新包镜像清单：' + str(error))
    return ids


def same_release_images(installed, images):
    recorded = installed.get('images') or {}
    return set(recorded) == set(images) and all(
        recorded[name].get('packageId', recorded[name].get('id')) == image['id'] and recorded[name].get('tag') == image['tag']
        for name, image in images.items())


def freeze_compose(config, images):
    result = copy.deepcopy(config)
    result['services'] = {key: value for key, value in result['services'].items() if key in ('backend', 'frontend')}
    for name, service in result['services'].items():
        service.pop('build', None)
        service.pop('profiles', None)
        service['image'] = images[name]['id']
        service['pull_policy'] = 'never'
    return result


def escaped_compose(value):
    # Resolved values can contain literal dollar signs (e.g. a legacy secret).
    if isinstance(value, str):
        return value.replace('$', '$$')
    if isinstance(value, list):
        return [escaped_compose(v) for v in value]
    if isinstance(value, dict):
        return {k: escaped_compose(v) for k, v in value.items()}
    return value


def update_env(text):
    lines = text.splitlines()
    output = [line for line in lines if not re.match(r'^\s*(?:export\s+)?COMPOSE_FILE\s*=', line)]
    output.append('COMPOSE_FILE=.offline/current-compose.json')
    return '\n'.join(output) + '\n'


def tree_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            p = Path(root) / name
            require(not p.is_symlink(), '业务目录含符号链接，需人工核对后再升级：' + str(p))
            if p.is_file():
                total += p.stat().st_size
            elif not p.is_dir():
                raise UpdateError('业务目录存在不支持的特殊文件：' + str(p))
    return total


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_tree(path):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            with (Path(root) / name).open('rb') as stream:
                os.fsync(stream.fileno())
        sync_directory(root)


def check_space(requirements):
    """Aggregate all allocations sharing a filesystem, including rollback staging."""
    groups = {}
    for directory, size in requirements:
        directory = Path(directory)
        dev = directory.stat().st_dev
        item = groups.setdefault(dev, [directory, 512 * 1024**2])
        item[1] += size
    for directory, size in groups.values():
        require(shutil.disk_usage(directory).free > size, '备份及失败恢复所需磁盘不足：' + str(directory))


class Updater:
    def __init__(self, deployment, bundle):
        self.deployment = Path(deployment).resolve()
        self.bundle = Path(bundle).resolve()
        require((self.deployment / '.env').is_file(), '请选择医院原部署目录（须含 .env），不要使用新包目录')
        self.root = self.deployment / '.offline'
        require(not self.root.is_symlink(), '升级状态目录不能是符号链接')
        self.root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        import fcntl
        self.lock = (self.root / 'upgrade.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError('另一个升级操作正在运行')
        self.stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        logs = self.root / 'logs'
        logs.mkdir(mode=0o700, exist_ok=True)
        self.log_path = logs / (self.stamp + '.log')
        self.log = self.log_path.open('x', encoding='utf-8')
        self.note('日志：' + str(self.log_path))
        # Ignore shell overrides. All application values come from the specified
        # .env/resolved configuration; Docker connection settings remain usable.
        self.env = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'USER', 'LANG', 'LC_ALL', 'TMPDIR'} or key.startswith('DOCKER_')}
        self.pending = self.root / 'pending.json'
        self.state_path = self.root / 'state.json'

    def note(self, message):
        print(message, flush=True)
        self.log.write(dt.datetime.now(dt.timezone.utc).isoformat() + ' ' + message + '\n')
        self.log.flush()

    def run(self, args, *, capture=True, timeout=1800, env=None):
        # Never print command payloads: config and inspection may contain JWT.
        result = subprocess.run(args, cwd=self.deployment, env=env or self.env,
                                text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            # Detailed output remains local/private; errors may include runtime paths.
            self.log.write(result.stdout + '\n' + result.stderr + '\n')
            self.log.flush()
            raise UpdateError('操作失败：' + ' '.join(args[:3]) + '；详情见本机升级日志')
        return result.stdout.strip() if capture else None

    def docker_json(self, *args):
        return json.loads(self.run(['docker', *args]))

    def compose(self, file, *args):
        project = read_json(file)['name']
        return self.run(['docker', 'compose', '-p', project, '--project-directory', str(self.deployment),
                         '--env-file', str(self.deployment / '.env'), '-f', str(file), *args])

    def discover(self):
        rows = self.run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.service=backend', '--filter', 'label=com.docker.compose.oneoff=False']).splitlines()
        matches = []
        for cid in rows:
            c = self.docker_json('inspect', cid)[0]
            labels = c['Config'].get('Labels', {})
            if Path(labels.get('com.docker.compose.project.working_dir', '/nonexistent')).resolve() == self.deployment:
                matches.append(c)
        require(len(matches) == 1, '原部署目录没有唯一后端容器，无法自动识别')
        backend = matches[0]
        project = backend['Config']['Labels']['com.docker.compose.project']
        ids = self.run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project,
                        '--filter', 'label=com.docker.compose.service=frontend', '--filter', 'label=com.docker.compose.oneoff=False']).splitlines()
        require(len(ids) == 1, '没有唯一前端容器')
        frontend = self.docker_json('inspect', ids[0])[0]
        require(backend['State']['Running'] and frontend['State']['Running'], '前后端须正常运行再执行升级；中断升级使用 --recover')
        state = read_json(self.state_path) if self.state_path.exists() else None
        if state:
            require(frontend['Image'] == state['images']['frontend']['id'], '前端镜像与版本记录不一致')
            config = json.loads(self.compose(self.root / 'current-compose.json', 'config', '--format', 'json'))
        else:
            labels = backend['Config']['Labels']
            files = labels.get('com.docker.compose.project.config_files', '').split(',')
            require(files and all(Path(p).is_file() and (Path(p).resolve().parent == self.deployment or self.root in Path(p).resolve().parents) for p in files), '旧 Compose 文件缺失或位于其他目录')
            env = dict(self.env, BACKEND_IMAGE=backend['Config']['Image'], FRONTEND_IMAGE=frontend['Config']['Image'], SAM3_AUTO_DOWNLOAD='false', COMPOSE_PROJECT_NAME=project)
            cmd = ['docker', 'compose', '--project-directory', str(self.deployment), '--env-file', str(self.deployment / '.env')]
            for file in files:
                cmd += ['-f', file]
            config = json.loads(self.run(cmd + ['config', '--format', 'json'], env=env))
        config['name'] = project
        paths = validate_runtime(config, backend, frontend)
        for key in ('database', 'storage', 'model'):
            if paths[key]:
                p = Path(paths[key])
                require(p != self.root and p not in self.root.parents and self.root not in p.parents, '业务/模型目录与升级状态目录重叠')
        fingerprint = self.run(['docker', 'exec', backend['Id'], 'python', '-c', FINGERPRINT_CODE])
        legacy = {} if state else read_json(self.bundle / 'scripts/offline_legacy.json')
        installed = identify_version(backend, state, legacy, fingerprint)
        return config, paths, backend, frontend, installed

    def save_compose(self, file, config):
        write_json(file, escaped_compose(config))

    def health(self, file):
        self.compose(file, 'exec', '-T', 'frontend', 'wget', '-qO-', 'http://127.0.0.1/')
        response = self.compose(file, 'exec', '-T', 'frontend', 'wget', '-qO-', 'http://127.0.0.1/api/health')
        require(json.loads(response).get('ok') is True, '首页或 API 健康检查失败')

    def idle(self, backend_id):
        self.run(['docker', 'exec', backend_id, 'python', '-c', BUSY_CODE])

    def resolve_images(self, manifest):
        """Loaded images under this daemon's own IDs. images.tar is already verified
        against the manifest; an ID outside that archive is not this release."""
        resolved, archive = {}, None
        for name, image in manifest['images'].items():
            obj = self.docker_json('image', 'inspect', image['tag'])[0]
            local = obj['Id']
            if local != image['id']:
                archive = archive_image_ids(self.bundle / 'images.tar') if archive is None else archive
                require(local in archive.get(image['tag'], ()), '镜像 ' + image['tag'] + ' 不属于本更新包：' + local)
            require(obj['Os'] + '/' + obj['Architecture'] == manifest['platform'], '镜像平台不匹配：' + name)
            labels = obj['Config'].get('Labels', {})
            require(labels.get('org.opencontainers.image.revision') == manifest['revision'] and labels.get('org.opencontainers.image.version') == manifest['version']
                    and labels.get('io.sperm-annotation.offline-contract') == str(CONTRACT)
                    and labels.get('io.sperm-annotation.mode') == manifest['mode'], '镜像发布信息不匹配：' + name)
            resolved[name] = dict(image) if local == image['id'] else dict(image, id=local, packageId=image['id'])
        return resolved

    def status(self):
        if self.pending.exists():
            pending = read_json(self.pending)
            self.note('存在未完成升级：' + pending['backup'] + '；请使用 --recover')
        if self.state_path.exists():
            state = read_json(self.state_path)
            self.note('记录版本：' + state['version'] + '；源码：' + state['revision'])
        else:
            self.note('尚未建立版本记录；--check 将核对旧代码指纹。')

    def snapshot_files(self, backup):
        for name in ('.env', 'deploy-offline.sh', 'manage-offline.sh', 'export-audit.sh', '.offline/current-compose.json', '.offline/state.json'):
            source = self.deployment / name
            if source.exists():
                dest = backup / 'config' / name
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(source, dest)
        # Save the updater used for this transaction, so recovery is independent
        # of a later package being moved/deleted.
        shutil.copy2(Path(__file__), backup / 'offline_update.py')

    def restore_files(self, backup):
        for name in ('.env', 'deploy-offline.sh', 'manage-offline.sh', 'export-audit.sh', '.offline/current-compose.json', '.offline/state.json'):
            source, dest = backup / 'config' / name, self.deployment / name
            if source.exists():
                atomic_write(dest, source.read_text(encoding='utf-8'))
            elif dest.exists():
                dest.unlink()

    def backup_data(self, backup, paths):
        for key in ('database', 'storage'):
            shutil.copytree(paths[key], backup / key, copy_function=shutil.copy2)
        self.check_db(backup / 'database' / 'app.db')
        sync_tree(backup)

    def check_db(self, file):
        with sqlite3.connect(file.as_uri() + '?mode=ro', uri=True) as connection:
            require(connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok', '数据库完整性检查失败')
            schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
        return hashlib.sha256(json.dumps(schema, separators=(',', ':')).encode()).hexdigest()

    def restore_data(self, backup, paths, suffix):
        # Rename current directories on their own filesystem. Never erase data
        # written after upgrade; it remains beside the restored directory.
        for key in ('database', 'storage'):
            source = backup / key
            require(source.is_dir(), '回滚数据备份缺失：' + key)
            target = Path(paths[key])
            preserved = target.with_name(target.name + '.before-restore-' + suffix)
            staged = target.with_name(target.name + '.restore-' + suffix)
            require(not preserved.exists() and not staged.exists(), '恢复目标已经存在，停止以防覆盖')
            shutil.copytree(source, staged)
            sync_tree(staged)
            if target.exists():
                os.rename(target, preserved)
            os.rename(staged, target)
            sync_directory(target.parent)
            self.note('恢复前数据已保留：' + str(preserved))

    def start(self, file, *services):
        self.compose(file, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '240', '--force-recreate', *services)

    def resume_old(self, backup, phase):
        info = read_json(backup / 'transaction.json')
        # Stop both using the saved project; this works even after new containers
        # have been created but the new state/config has not yet been committed.
        if phase == 'stopping':
            cid = self.compose(backup / 'old-compose.json', 'ps', '-q', 'backend')
            if cid and self.docker_json('inspect', cid)[0]['State']['Running']:
                self.restore_files(backup)
                self.start(backup / 'old-compose.json', 'frontend')
                self.pending.unlink(missing_ok=True)
                sync_directory(self.root)
                self.note('已恢复页面入口；原后台任务继续运行。')
                return
        self.compose(backup / 'old-compose.json', 'stop', 'frontend', 'backend')
        if phase in ('switching', 'verifying', 'committing'):
            self.restore_data(backup, info['paths'], self.stamp)
        self.restore_files(backup)
        self.start(backup / 'old-compose.json', 'backend', 'frontend')
        self.health(backup / 'old-compose.json')
        # Reset the stable file label after restoring, so subsequent discovery
        # doesn't depend on the backup directory when the old version is managed.
        if self.state_path.exists():
            self.start(self.root / 'current-compose.json', 'backend', 'frontend')
        self.pending.unlink(missing_ok=True)
        sync_directory(self.root)
        self.note('已恢复升级前版本；原始备份保留。')

    def recover(self):
        require(self.pending.exists(), '没有待恢复的升级')
        pending = read_json(self.pending)
        backup = self.root / 'backups' / pending['backup']
        self.resume_old(backup, pending['phase'])

    def rollback(self, backup_id, restore_data):
        require(restore_data, '回滚会恢复升级前数据，必须显式添加 --restore-data；当前数据会另外保留')
        require(not self.pending.exists(), '有未完成升级，请先 --recover')
        require(VERSION.fullmatch(backup_id), '备份编号无效')
        backup = self.root / 'backups' / backup_id
        info = read_json(backup / 'transaction.json')
        current = read_json(self.state_path)
        require(current.get('backup') == backup_id, '只能回滚当前版本对应的上一次升级，不能跳过后续升级')
        config, paths, backend, _, _ = self.discover()
        require(paths == info['paths'], '挂载路径已改变，停止回滚')
        self.idle(backend['Id'])
        check_space([(paths[key], tree_size(backup / key)) for key in ('database', 'storage')])
        # Reuse recovery's idempotent stop/restore path; a stopped service never
        # accepts writes during rollback.
        current_file = self.root / 'current-compose.json'
        self.compose(current_file, 'stop', 'frontend')
        try:
            self.idle(backend['Id'])
        except Exception:
            self.start(current_file, 'frontend')
            raise
        write_json(self.pending, {'backup': backup_id, 'phase': 'switching'})
        self.resume_old(backup, 'switching')

    def update(self, check_only=False):
        require(not self.pending.exists(), '上次升级未完成，请先运行 --recover')
        self.note('校验完整更新包（镜像较大时需要等待）…')
        manifest = verify_bundle(self.bundle)
        config, paths, backend, frontend, installed = self.discover()
        require(paths['mode'] == manifest['mode'], '更新包 CPU/GPU 模式与医院当前模式不同')
        arch = self.docker_json('image', 'inspect', backend['Image'])[0]
        require(arch['Os'] + '/' + arch['Architecture'] == manifest['platform'], '更新包平台与当前服务器镜像不同')
        self.note('当前 ' + installed['version'] + ' → 目标 ' + manifest['version'])
        if same_release_images(installed, manifest['images']):
            self.note('此版本已安装，无需重复更新。')
            return
        require(installed['revision'] in manifest['sourceHistory'], '目标源码不包含当前版本历史，禁止误降级或跨分支更新；降级请使用带数据恢复的回滚')
        require(installed['revision'] != manifest['revision'] and installed['version'] != manifest['version'], '版本号或源码提交相同但镜像不同，请发布独立的新版本')
        self.idle(backend['Id'])
        sizes = {k: tree_size(paths[k]) for k in ('database', 'storage')}
        size = sum(sizes.values())
        space = [(self.root, size)] + [(paths[k], sizes[k]) for k in sizes]
        check_space(space)
        if check_only:
            self.note('预检查通过；未导入镜像或停止服务。备份数据约 ' + str(size) + ' 字节。')
            return
        # Pin original image IDs with backup tags before docker load can move tags.
        backup = self.root / 'backups' / self.stamp
        backup.mkdir(parents=True, mode=0o700)
        old_images = {name: {'id': c['Image']} for name, c in [('backend', backend), ('frontend', frontend)]}
        old_config = freeze_compose(config, old_images)
        self.save_compose(backup / 'old-compose.json', old_config)
        self.snapshot_files(backup)
        for name, image in old_images.items():
            self.run(['docker', 'image', 'tag', image['id'], 'annotation-offline-backup:' + name + '-' + self.stamp.lower()])
        write_json(backup / 'transaction.json', {'paths': paths, 'from': installed, 'to': manifest['version']})
        self.note('导入镜像；所有启动操作禁止联网拉取…')
        self.run(['docker', 'load', '-i', str(self.bundle / 'images.tar')], timeout=7200)
        images = self.resolve_images(manifest)
        candidate = freeze_compose(config, images)
        self.save_compose(backup / 'candidate-compose.json', candidate)
        self.compose(backup / 'candidate-compose.json', 'config', '--quiet')
        # Real model/CUDA availability test uses the new image with the preserved
        # GPU and model mounts; no application server or migration is started.
        self.compose(backup / 'candidate-compose.json', 'run', '--rm', '--no-deps', '--pull', 'never', '--entrypoint', 'python', 'backend', '-m', 'app.deployment_check')
        self.idle(backend['Id'])
        check_space(space)
        phase = 'stopping'
        write_json(self.pending, {'backup': self.stamp, 'phase': phase})
        try:
            self.note('暂时停止页面入口，检查任务结束后备份数据…')
            self.compose(backup / 'old-compose.json', 'stop', 'frontend')
            self.idle(backend['Id'])
            self.compose(backup / 'old-compose.json', 'stop', 'backend')
            require(not self.docker_json('inspect', backend['Id'])[0]['State']['Running'], '后端未停止，不能备份')
            self.backup_data(backup, paths)
            write_json(backup / 'backup-ready.json', {'schemaFingerprint': self.check_db(backup / 'database/app.db')})
            phase = 'switching'
            write_json(self.pending, {'backup': self.stamp, 'phase': phase})
            # Stable config file is the sole normal start entry after migration.
            self.save_compose(self.root / 'current-compose.json', candidate)
            self.start(self.root / 'current-compose.json', 'backend')
            phase = 'verifying'
            write_json(self.pending, {'backup': self.stamp, 'phase': phase})
            # Keep the frontend gated while checking the new backend, then open.
            backend_id = self.compose(self.root / 'current-compose.json', 'ps', '-q', 'backend')
            fingerprint = self.run(['docker', 'exec', backend_id, 'python', '-c', FINGERPRINT_CODE])
            self.compose(self.root / 'current-compose.json', 'run', '--rm', '--no-deps', '--pull', 'never', '--entrypoint', 'sh', 'frontend', '-c', 'nginx && wget -qO- http://127.0.0.1/ >/dev/null && wget -qO- http://127.0.0.1/api/health')
            phase = 'committing'
            write_json(self.pending, {'backup': self.stamp, 'phase': phase})
            atomic_write(self.deployment / '.env', update_env((self.deployment / '.env').read_text(encoding='utf-8-sig')))
            state = {k: manifest[k] for k in ('version', 'revision', 'contract', 'mode', 'platform')}
            state['images'] = images
            state.update(backup=self.stamp, backendFingerprint=fingerprint, installedAt=dt.datetime.now(dt.timezone.utc).isoformat(), schemaFingerprint=self.check_db(Path(paths['database']) / 'app.db'))
            write_json(self.state_path, state)
            manager = ('#!/usr/bin/env bash\nset -euo pipefail\ncd -- "$(dirname -- "${BASH_SOURCE[0]}")"\n'
                       '[[ ! -f .offline/pending.json ]] || { echo "升级未完成，请先运行更新工具 --recover" >&2; exit 1; }\n'
                       'exec docker compose -p ' + shlex.quote(candidate['name']) +
                       ' --project-directory "$PWD" --env-file .env -f .offline/current-compose.json "${@:-ps}"\n')
            atomic_write(self.deployment / 'manage-offline.sh', manager)
            if 'export-audit.sh' in manifest['files']:
                atomic_write(self.deployment / 'export-audit.sh', (self.bundle / 'export-audit.sh').read_text(encoding='utf-8'))
            if (self.deployment / 'deploy-offline.sh').exists():
                atomic_write(self.deployment / 'deploy-offline.sh', '#!/usr/bin/env bash\nset -euo pipefail\ncd -- "$(dirname -- "${BASH_SOURCE[0]}")"\nexec bash manage-offline.sh up -d --no-build --pull never --wait\n')
            self.start(self.root / 'current-compose.json', 'frontend')
            self.health(self.root / 'current-compose.json')
            self.pending.unlink()
            sync_directory(self.root)
        except (Exception, KeyboardInterrupt) as error:
            self.note('升级失败，正在恢复原版本：' + str(error))
            try:
                self.resume_old(backup, phase)
            except Exception as recovery_error:
                self.note('自动恢复未完成：' + str(recovery_error) + '；请保留日志并运行 --recover')
            raise
        self.note('升级完成：' + manifest['version'] + '；备份编号：' + self.stamp)
        self.note('请刷新浏览器，检查原有数据并执行真实 GPU 追踪验收。')
        self.note('恢复命令：bash ' + str(self.bundle / 'update-offline.sh') + ' ' + str(self.deployment) + ' --rollback ' + self.stamp + ' --restore-data')


def main():
    parser = argparse.ArgumentParser(description='医院已有 Docker 部署的离线升级；保留持久数据和模型')
    parser.add_argument('deployment', type=Path, nargs='?')
    parser.add_argument('--bundle', type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true')
    group.add_argument('--status', action='store_true')
    group.add_argument('--recover', action='store_true')
    group.add_argument('--rollback', metavar='BACKUP_ID')
    group.add_argument('--assemble', action='store_true', help='合并并校验分块发布的更新包，不需要原部署目录')
    parser.add_argument('--restore-data', action='store_true')
    parser.add_argument('--remove-parts', action='store_true', help='与 --assemble 合用：合并校验成功后删除分块')
    args = parser.parse_args()
    if args.assemble == (args.deployment is not None) or (args.remove_parts and not args.assemble):
        parser.error('--assemble 不带原部署目录；其他操作必须指定原部署目录')
    os.umask(0o077)
    try:
        if args.assemble:
            assemble(args.bundle, args.remove_parts)
            return 0
        updater = Updater(args.deployment, args.bundle)
        if args.status:
            updater.status()
        elif args.recover:
            updater.recover()
        elif args.rollback:
            updater.rollback(args.rollback, args.restore_data)
        else:
            updater.update(args.check)
        return 0
    except (UpdateError, OSError, ValueError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        print('未完成：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
