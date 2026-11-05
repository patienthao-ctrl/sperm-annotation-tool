"""Offline release validation and persistence contracts; never use a Docker daemon."""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import sqlite3
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def updater():
    spec = importlib.util.spec_from_file_location("offline_update_under_test", ROOT / "scripts/offline_update.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime(tmp_path):
    """Resolved Compose plus inspect data, including a dollar-containing secret."""
    database, storage, model = (tmp_path / name for name in ("db with spaces", "storage", "model"))
    for directory in (database, storage, model):
        directory.mkdir()
    with sqlite3.connect(database / "app.db") as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
    env = {
        "APP_ENV": "production",
        "APP_DATA_DIR": "/data/database",
        "APP_DB_FILE": "/data/database/app.db",
        "APP_STORAGE_DIR": "/data/storage",
        "BACKEND_HOST": "0.0.0.0",
        "BACKEND_PORT": "3000",
        "JWT_SECRET": "fixture$literal-secret-is-not-logged-123456789",
        "SAM3_ENABLED": "true",
        "SAM3_DEVICE": "cuda",
        "SAM3_MODEL_ID": "/models/sam3",
        "SAM3_DTYPE": "bfloat16",
    }
    volumes = [
        {"type": "bind", "source": str(database), "target": "/data/database"},
        {"type": "bind", "source": str(storage), "target": "/data/storage"},
        {"type": "bind", "source": str(model), "target": "/models/sam3", "read_only": True},
    ]
    devices = [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]
    config = {"name": "hospital-fixture", "services": {
        "backend": {
            "image": "hospital/backend:old",
            "build": {"context": str(tmp_path), "dockerfile": "backend/Dockerfile"},
            "environment": env,
            "volumes": volumes,
            "deploy": {"resources": {"reservations": {"devices": devices}}},
        },
        "frontend": {
            "image": "hospital/frontend:old",
            "build": {"context": str(tmp_path), "dockerfile": "frontend/Dockerfile"},
            "ports": [{"target": 80, "published": "8080", "host_ip": "0.0.0.0", "protocol": "tcp"}],
            "depends_on": {"backend": {"condition": "service_healthy", "required": True}},
        },
        "model-setup": {"image": "python:3.12-slim", "profiles": ["model-setup"]},
    }}
    backend = {
        "Id": "backend-cid",
        "Image": "sha256:" + "a" * 64,
        "Config": {"Image": "hospital/backend:old", "Env": [f"{k}={v}" for k, v in env.items()] + ["PATH=/usr/bin"],
                   "Labels": {"com.docker.compose.project": "hospital-fixture", "com.docker.compose.service": "backend"}},
        "State": {"Running": True},
        "Mounts": [{"Type": "bind", "Source": v["source"], "Destination": v["target"], "RW": not v.get("read_only", False)} for v in volumes],
        "HostConfig": {"DeviceRequests": [{"Driver": "nvidia", "Count": 1, "Capabilities": [["gpu"]]}]},
    }
    frontend = {
        "Id": "frontend-cid",
        "Image": "sha256:" + "b" * 64,
        "Config": {"Image": "hospital/frontend:old", "Env": ["PATH=/usr/bin"],
                   "Labels": {"com.docker.compose.project": "hospital-fixture", "com.docker.compose.service": "frontend"}},
        "State": {"Running": True}, "Mounts": [],
        "HostConfig": {"PortBindings": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}},
    }
    return config, backend, frontend, {"database": str(database), "storage": str(storage), "model": str(model), "mode": "gpu"}


def test_runtime_uses_real_absolute_persistent_mounts(updater, runtime):
    config, backend, frontend, expected = runtime
    actual = updater.validate_runtime(config, backend, frontend)
    assert {key: str(actual[key]) if actual[key] is not None else None for key in expected} == expected


def test_cpu_runtime_requires_no_model_or_gpu(updater, runtime):
    config, backend, frontend, expected = runtime
    service = config["services"]["backend"]
    service["environment"]["SAM3_ENABLED"] = "false"
    service["environment"]["SAM3_DEVICE"] = "cpu"
    service["volumes"] = service["volumes"][:2]
    service.pop("deploy")
    backend["Config"]["Env"] = [f"{k}={v}" for k, v in service["environment"].items()]
    backend["Mounts"] = backend["Mounts"][:2]
    backend["HostConfig"]["DeviceRequests"] = []
    expected.update(mode="cpu", model=None)
    actual = updater.validate_runtime(config, backend, frontend)
    assert {key: str(actual[key]) if actual[key] is not None else None for key in expected} == expected


@pytest.mark.parametrize("mutation", [
    "environment", "database-path", "storage-path", "model-path", "database-readonly",
    "named-volume", "missing-database", "code-mount", "frontend-mount", "extra-service",
    "short-secret", "custom-database-file", "custom-storage-env",
])
def test_runtime_drift_or_unsupported_storage_is_rejected(updater, runtime, mutation):
    config, backend, frontend, _ = runtime
    service = config["services"]["backend"]
    if mutation == "environment":
        service["environment"]["SAM3_DTYPE"] = "float32"
    elif mutation in {"database-path", "storage-path", "model-path"}:
        index = {"database-path": 0, "storage-path": 1, "model-path": 2}[mutation]
        service["volumes"][index]["source"] += "-different"
    elif mutation == "database-readonly":
        backend["Mounts"][0]["RW"] = False
        service["volumes"][0]["read_only"] = True
    elif mutation == "named-volume":
        backend["Mounts"][0]["Type"] = "volume"
        service["volumes"][0]["type"] = "volume"
    elif mutation == "missing-database":
        backend["Mounts"].pop(0)
        service["volumes"].pop(0)
    elif mutation == "code-mount":
        backend["Mounts"].append({"Type": "bind", "Source": "/fixture", "Destination": "/app/backend", "RW": False})
    elif mutation == "frontend-mount":
        frontend["Mounts"].append({"Type": "bind", "Source": "/fixture", "Destination": "/usr/share/nginx/html", "RW": False})
    elif mutation == "extra-service":
        config["services"]["unrelated"] = {"image": "example/unrelated:1"}
    else:
        key, value = {
            "short-secret": ("JWT_SECRET", "short"),
            "custom-database-file": ("APP_DB_FILE", "/data/database/other.db"),
            "custom-storage-env": ("APP_STORAGE_DIR", "/tmp/ephemeral"),
        }[mutation]
        service["environment"][key] = value
        backend["Config"]["Env"] = [f"{k}={v}" for k, v in service["environment"].items()]
    with pytest.raises(updater.UpdateError):
        updater.validate_runtime(config, backend, frontend)


def test_freeze_compose_pins_release_without_build_or_model_download(updater, runtime):
    config, _, _, _ = runtime
    original = deepcopy(config)
    images = {"backend": {"id": "sha256:" + "d" * 64}, "frontend": {"id": "sha256:" + "e" * 64}}
    frozen = updater.freeze_compose(config, images)
    assert config == original, "preserve the pre-update configuration for rollback"
    assert set(frozen["services"]) == {"backend", "frontend"}
    for name, image in images.items():
        assert frozen["services"][name]["image"] == image["id"]
        assert "build" not in frozen["services"][name]
        assert frozen["services"][name].get("pull_policy") == "never"
    env = frozen["services"]["backend"]["environment"]
    assert env["JWT_SECRET"] == original["services"]["backend"]["environment"]["JWT_SECRET"]
    saved = updater.escaped_compose(frozen)
    assert saved["services"]["backend"]["environment"]["JWT_SECRET"] == env["JWT_SECRET"].replace("$", "$$")
    assert frozen["services"]["backend"]["volumes"] == original["services"]["backend"]["volumes"]
    assert frozen["services"]["frontend"]["ports"] == original["services"]["frontend"]["ports"]


def test_managed_env_preserves_credentials_and_custom_values(updater):
    original = '# hospital settings\nJWT_SECRET="literal$secret#value"\nAPP_DATA_ROOT=/data/hospital\nCOMPOSE_FILE=compose.yaml,compose.gpu.yaml\nCOMPOSE_PATH_SEPARATOR=,\nWEB_PORT=9090\n'
    modified = updater.update_env(original)
    assert 'JWT_SECRET="literal$secret#value"' in modified
    assert "APP_DATA_ROOT=/data/hospital" in modified
    assert "WEB_PORT=9090" in modified
    assert modified.count("COMPOSE_FILE=") == 1
    assert "COMPOSE_FILE=.offline/current-compose.json" in modified
    assert updater.update_env(modified) == modified


def test_tree_size_counts_file_bytes_and_empty_trees(updater, tmp_path):
    assert updater.tree_size(tmp_path) == 0
    (tmp_path / "one").write_bytes(b"12345")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/two").write_bytes(b"abc")
    assert updater.tree_size(tmp_path) == 8


def test_hash_reads_content_including_binary_data(updater, tmp_path):
    data = bytes(range(256)) * 65537
    path = tmp_path / "image.tar"
    path.write_bytes(data)
    assert updater.sha256_file(path) == hashlib.sha256(data).hexdigest()


def test_recorded_legacy_baselines_are_unique_and_explicit():
    legacy = json.loads((ROOT / "scripts/offline_legacy.json").read_text())
    assert legacy["contract"] == 1
    assert len(legacy["backends"]) >= 2
    for digest, baseline in legacy["backends"].items():
        assert len(digest) == 64 and int(digest, 16) >= 0
        assert len(baseline["revision"]) == 40 and int(baseline["revision"], 16) >= 0
        assert baseline["description"]


@pytest.fixture
def bundle(tmp_path):
    directory = tmp_path / "release with spaces"
    (directory / "scripts").mkdir(parents=True)
    files = {
        "images.tar": b"isolated-image-archive",
        "update-offline.sh": b"#!/bin/sh\n",
        "scripts/offline_update.py": b"# isolated fixture only\n",
        "scripts/offline_legacy.json": b'{"contract":1,"backends":{}}',
        "compose.yaml": b"services: {}\n",
        "compose.gpu.yaml": b"services: {}\n",
        ".env.docker.example": b"JWT_SECRET=\n",
    }
    for name, data in files.items():
        (directory / name).write_bytes(data)
    manifest = {
        "format": 1, "version": "v1.2.0", "revision": "c" * 40,
        "sourceHistory": ["c" * 40, "a" * 40],
        "createdAt": "2026-09-30T00:00:00Z", "platform": "linux/amd64",
        "mode": "gpu", "contract": 1, "fromContracts": [1],
        "images": {
            "backend": {"tag": "sperm-annotation-backend:v1.2.0-gpu", "id": "sha256:" + "d" * 64, "size": 12345},
            "frontend": {"tag": "sperm-annotation-frontend:v1.2.0", "id": "sha256:" + "e" * 64, "size": 1234},
        },
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory, manifest


def test_bundle_hashes_validate_complete_payload(updater, bundle):
    directory, manifest = bundle
    assert updater.verify_bundle(directory) == manifest


@pytest.mark.parametrize("name", ["images.tar", "scripts/offline_update.py", "scripts/offline_legacy.json"])
def test_corrupt_payload_is_rejected_before_docker(updater, bundle, name):
    directory, _ = bundle
    (directory / name).write_bytes(b"wrong-or-truncated-file")
    with pytest.raises(updater.UpdateError):
        updater.verify_bundle(directory)


@pytest.mark.parametrize("change", ["format", "contract", "from-contracts", "mode", "platform", "missing-image", "missing-payload", "path-traversal", "symlink", "missing-history", "empty-history", "bad-history", "history-without-target"])
def test_unsupported_or_escaping_bundle_is_rejected(updater, bundle, change):
    directory, manifest = bundle
    if change == "format":
        manifest["format"] = 99
    elif change == "contract":
        manifest["contract"] = 99
    elif change == "from-contracts":
        manifest["fromContracts"] = [99]
    elif change == "mode":
        manifest["mode"] = "automatic"
    elif change == "platform":
        manifest["platform"] = "windows/amd64"
    elif change == "missing-history":
        del manifest["sourceHistory"]
    elif change == "empty-history":
        manifest["sourceHistory"] = []
    elif change == "bad-history":
        manifest["sourceHistory"] = [manifest["revision"], "not-a-git-revision"]
    elif change == "history-without-target":
        manifest["sourceHistory"] = ["a" * 40]
    elif change == "missing-image":
        del manifest["images"]["backend"]
    elif change == "missing-payload":
        del manifest["files"]["images.tar"]
    elif change == "path-traversal":
        external = directory.parent / "outside"
        external.write_bytes(b"outside")
        manifest["files"]["../outside"] = hashlib.sha256(b"outside").hexdigest()
    elif change == "symlink":
        external = directory.parent / "outside"
        external.write_bytes(b"isolated-image-archive")
        (directory / "images.tar").unlink()
        (directory / "images.tar").symlink_to(external)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(updater.UpdateError):
        updater.verify_bundle(directory)


def test_known_legacy_version_has_fixed_source_identity(updater, runtime):
    _, backend, _, _ = runtime
    legacy = json.loads((ROOT / "scripts/offline_legacy.json").read_text())
    fingerprint, record = next(iter(legacy["backends"].items()))
    installed = updater.identify_version(backend, None, legacy, fingerprint)
    assert installed["revision"] == record["revision"]
    assert installed["contract"] == 1
    with pytest.raises(updater.UpdateError, match="无法识别"):
        updater.identify_version(backend, None, legacy, "f" * 64)


@pytest.mark.parametrize("change", [None, "image", "fingerprint", "contract"])
def test_managed_version_matches_actual_image_and_code(updater, runtime, change):
    _, backend, _, _ = runtime
    state = {"version": "v1.1.0", "revision": "a" * 40, "contract": 1,
             "images": {"backend": {"id": backend["Image"]}}, "backendFingerprint": "c" * 64}
    if change == "image":
        backend["Image"] = "sha256:" + "f" * 64
    elif change == "fingerprint":
        state["backendFingerprint"] = "b" * 64
    elif change == "contract":
        state["contract"] = 99
    if change:
        with pytest.raises(updater.UpdateError):
            updater.identify_version(backend, state, {}, "c" * 64)
    else:
        assert updater.identify_version(backend, state, {}, "c" * 64) == state


def test_tree_size_rejects_external_symlink(updater, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("do not include unrelated files")
    data = tmp_path / "data"
    data.mkdir()
    (data / "link").symlink_to(outside)
    with pytest.raises(updater.UpdateError, match="符号链接"):
        updater.tree_size(data)


@pytest.fixture
def transaction(updater, runtime, bundle, tmp_path, monkeypatch):
    """Real SQLite/backups/config writes, mocked Docker process boundaries only."""
    config, backend, frontend, paths = runtime
    release, manifest = bundle
    deploy = tmp_path / "hospital deployment"
    deploy.mkdir()
    (deploy / ".env").write_text('JWT_SECRET="fixture$secret#kept"\nCOMPOSE_FILE=compose.yaml,compose.gpu.yaml\nWEB_PORT=8080\n')
    (deploy / "deploy-offline.sh").write_text("#!/bin/sh\n# old installer\n")
    storage = Path(paths["storage"])
    (storage / "original-video").write_bytes(b"original user video")
    (Path(paths["model"]) / "weights").write_bytes(b"original model")
    database = Path(paths["database"]) / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO sample VALUES ('original annotation')")
    agent = updater.Updater(deploy, release)
    installed = {"version": "legacy-old", "revision": "a" * 40, "contract": 1}
    calls, failure, failed = [], {"at": None}, set()

    def inject(name):
        if failure["at"] == name and name not in failed:
            failed.add(name)
            raise updater.UpdateError("injected " + name)

    def docker_json(*args):
        calls.append(("json", *args))
        if args[:2] == ("image", "inspect"):
            by_tag = {image["tag"]: image["id"] for image in manifest["images"].values()}
            return [{"Id": by_tag.get(args[2], args[2]), "Os": "linux", "Architecture": "amd64", "Config": {"Labels": {
                "org.opencontainers.image.revision": manifest["revision"],
                "org.opencontainers.image.version": manifest["version"],
                "io.sperm-annotation.offline-contract": "1", "io.sperm-annotation.mode": manifest["mode"],
            }}}]
        if args[0] == "inspect":
            return [backend if args[1] == backend["Id"] else frontend]
        raise AssertionError("unexpected Docker JSON command: " + repr(args))

    def run(args, **kwargs):
        calls.append(("run", *args))
        if args[:2] == ["docker", "load"]:
            inject("load")
        if args[:2] == ["docker", "exec"]:
            if args[-1] == updater.BUSY_CODE:
                checks = sum(call[:3] == ("run", "docker", "exec") and call[-1] == updater.BUSY_CODE for call in calls)
                if checks == 3:
                    inject("busy-window")
                inject("busy")
                return "idle"
            if args[-1] == updater.FINGERPRINT_CODE:
                return "f" * 64
        return ""

    def compose(file, *args):
        calls.append(("compose", str(file), *args))
        if args[0] == "run":
            inject("preflight")
        elif args[0] == "stop":
            if "frontend" in args:
                frontend["State"]["Running"] = False
            if "backend" in args:
                backend["State"]["Running"] = False
        elif args[0] == "up":
            assert "--no-build" in args and args[args.index("--pull") + 1] == "never"
            new = str(file).endswith("current-compose.json")
            if "backend" in args:
                backend["State"]["Running"] = True
                if new:
                    assert not frontend["State"]["Running"], "frontend must remain gated during migration"
                    backup = agent.root / "backups" / agent.stamp
                    assert (backup / "backup-ready.json").exists(), "migration requires completed backup"
                    assert (backup / "storage/original-video").read_bytes() == b"original user video"
                    with sqlite3.connect(database) as connection:
                        connection.execute("CREATE TABLE IF NOT EXISTS new_schema (value TEXT)")
                        connection.execute("UPDATE sample SET value='migration changed value'")
                    (storage / "new-artifact").write_bytes(b"migration produced artifact")
                    inject("backend-start")
            if "frontend" in args:
                if new:
                    inject("frontend-start")
                frontend["State"]["Running"] = True
        elif args[0] == "ps":
            return backend["Id"] if str(file).endswith("old-compose.json") else "new-backend-cid"
        elif args[0] == "exec":
            if str(file).endswith("current-compose.json"):
                inject("health")
            if args[-1].endswith("/api/health"):
                return '{"ok":true}'
            return "<html>ready</html>"
        return ""

    original_backup = agent.backup_data

    def backup_data(*args):
        inject("backup")
        assert not backend["State"]["Running"] and not frontend["State"]["Running"]
        return original_backup(*args)

    original_write = updater.write_json

    def write_json(path, value):
        if path == agent.state_path:
            inject("commit")
        return original_write(path, value)

    monkeypatch.setattr(updater, "write_json", write_json)
    monkeypatch.setattr(agent, "discover", lambda: (deepcopy(config), paths, backend, frontend, installed))
    monkeypatch.setattr(agent, "docker_json", docker_json)
    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(agent, "compose", compose)
    monkeypatch.setattr(agent, "backup_data", backup_data)
    yield agent, calls, failure, deploy, paths, installed, manifest
    agent.log.close()
    agent.lock.close()


def test_precheck_never_loads_images_or_stops_services(transaction):
    agent, calls, _, deploy, _, _, _ = transaction
    original = (deploy / ".env").read_bytes()
    agent.update(check_only=True)
    assert not any(call[0] == "compose" or call[:3] == ("run", "docker", "load") for call in calls)
    assert not (agent.root / "backups").exists()
    assert (deploy / ".env").read_bytes() == original


@pytest.mark.parametrize("stage", ["busy", "load", "preflight"])
def test_failure_before_stopping_leaves_running_deployment_untouched(updater, transaction, stage):
    agent, calls, failure, deploy, paths, _, _ = transaction
    original = (deploy / ".env").read_bytes()
    failure["at"] = stage
    with pytest.raises(updater.UpdateError, match="injected"):
        agent.update()
    assert not any(call[0] == "compose" and call[2] in {"stop", "up"} for call in calls)
    assert (deploy / ".env").read_bytes() == original
    assert not agent.pending.exists() and not agent.state_path.exists()
    assert (Path(paths["storage"]) / "original-video").read_bytes() == b"original user video"


@pytest.mark.parametrize("stage", ["backup", "backend-start", "frontend-start", "health", "commit"])
def test_failure_restores_matching_database_storage_config_and_images(updater, transaction, stage):
    agent, calls, failure, deploy, paths, _, _ = transaction
    original = (deploy / ".env").read_bytes()
    failure["at"] = stage
    with pytest.raises(updater.UpdateError, match="injected"):
        agent.update()
    assert (deploy / ".env").read_bytes() == original
    assert (deploy / "deploy-offline.sh").read_text() == "#!/bin/sh\n# old installer\n"
    assert not agent.pending.exists() and not agent.state_path.exists()
    assert (Path(paths["storage"]) / "original-video").read_bytes() == b"original user video"
    assert not (Path(paths["storage"]) / "new-artifact").exists()
    assert (Path(paths["model"]) / "weights").read_bytes() == b"original model"
    with sqlite3.connect(Path(paths["database"]) / "app.db") as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "original annotation"
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='new_schema'").fetchall()
    restarts = [call for call in calls if call[0] == "compose" and call[2] == "up"]
    assert restarts[-1][1].endswith("old-compose.json")
    if stage != "backup":
        preserved = list(Path(paths["storage"]).parent.glob("storage.before-restore-*"))
        assert len(preserved) == 1
        assert (preserved[0] / "new-artifact").read_bytes() == b"migration produced artifact"


def test_success_records_version_reuses_paths_and_prevents_old_installer(transaction):
    agent, _, _, deploy, paths, _, manifest = transaction
    agent.update()
    state = json.loads(agent.state_path.read_text())
    assert state["version"] == manifest["version"] and state["images"] == manifest["images"]
    assert state["backendFingerprint"] == "f" * 64
    assert state["schemaFingerprint"]
    assert not agent.pending.exists()
    assert (deploy / ".env").read_text().count("COMPOSE_FILE=") == 1
    assert 'JWT_SECRET="fixture$secret#kept"' in (deploy / ".env").read_text()
    launcher = (deploy / "deploy-offline.sh").read_text()
    assert "manage-offline.sh" in launcher and "load" not in launcher and "--pull never" in launcher
    frozen = json.loads((agent.root / "current-compose.json").read_text())
    assert {v["source"] for v in frozen["services"]["backend"]["volumes"]} == {paths["database"], paths["storage"], paths["model"]}
    assert (Path(paths["storage"]) / "original-video").exists()
    assert (Path(paths["model"]) / "weights").read_bytes() == b"original model"


def test_identical_release_is_an_idempotent_noop(transaction):
    agent, calls, _, _, _, installed, manifest = transaction
    installed.update(manifest)
    agent.update()
    assert not any(call[0] == "compose" or call[:3] == ("run", "docker", "load") for call in calls)
    assert not (agent.root / "backups").exists()


@pytest.mark.parametrize("installed_revision", ["d" * 40, "e" * 40], ids=["newer-than-package", "divergent-branch"])
def test_upgrade_rejects_nonancestor_source_without_touching_running_service(updater, transaction, installed_revision):
    agent, calls, _, deploy, _, installed, _ = transaction
    original = (deploy / ".env").read_bytes()
    installed["revision"] = installed_revision
    with pytest.raises(updater.UpdateError):
        agent.update()
    assert not any(call[0] == "compose" or call[:3] == ("run", "docker", "load") for call in calls)
    assert not (agent.root / "backups").exists()
    assert not agent.pending.exists()
    assert (deploy / ".env").read_bytes() == original


def test_manual_rollback_requires_explicit_data_restore(updater, transaction):
    agent, calls, _, _, _, _, _ = transaction
    with pytest.raises(updater.UpdateError, match="restore-data"):
        agent.rollback("example-backup", False)
    assert not calls


@pytest.mark.parametrize("managed", [False, True])
def test_discovery_supports_restored_legacy_and_managed_without_original_package(updater, transaction, monkeypatch, managed):
    agent, _, _, deploy, _, _, _ = transaction
    config, _, backend, frontend, _ = agent.discover()
    fingerprint = "1" * 64
    config_file = agent.root / "backups/old-transaction/old-compose.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps(config))
    backend["Config"]["Labels"].update({
        "com.docker.compose.project.working_dir": str(deploy),
        "com.docker.compose.project.config_files": str(config_file),
    })
    legacy = agent.bundle / "scripts/offline_legacy.json"
    if managed:
        state = {"version": "v1.1.0", "revision": "a" * 40, "contract": 1,
                 "backendFingerprint": fingerprint,
                 "images": {"backend": {"id": backend["Image"]}, "frontend": {"id": frontend["Image"]}}}
        agent.state_path.write_text(json.dumps(state))
        legacy.unlink()
    else:
        legacy.write_text(json.dumps({"contract": 1, "backends": {
            fingerprint: {"revision": "a" * 40, "description": "known original deployment"},
        }}))

    def run(args, **kwargs):
        if args[:2] == ["docker", "ps"]:
            return "frontend-cid" if "label=com.docker.compose.service=frontend" in args else "backend-cid"
        if args[:2] == ["docker", "compose"]:
            return json.dumps(config)
        if args[:2] == ["docker", "exec"]:
            return fingerprint
        raise AssertionError("unexpected command: " + repr(args))

    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(agent, "compose", lambda *args: json.dumps(config))
    result = updater.Updater.discover(agent)
    assert result[-1]["revision"] == "a" * 40


def test_data_restore_can_resume_between_directory_renames(updater, transaction, monkeypatch):
    agent, _, _, _, paths, _, _ = transaction
    backup = agent.root / "backups/interrupted-restore"
    backup.mkdir(parents=True)
    # Only the data-copy methods are under test; no running application exists.
    import shutil
    import os
    for key in ("database", "storage"):
        shutil.copytree(paths[key], backup / key)
    (Path(paths["storage"]) / "new-user-write").write_bytes(b"preserve across rollback")
    original_rename = os.rename
    calls = 0

    def interrupted_rename(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected interruption after preserving original directory")
        return original_rename(source, destination)

    monkeypatch.setattr(updater.os, "rename", interrupted_rename)
    with pytest.raises(OSError, match="injected"):
        agent.restore_data(backup, paths, "initial-recovery")
    monkeypatch.setattr(updater.os, "rename", original_rename)
    agent.restore_data(backup, paths, "next-recovery")
    assert (Path(paths["database"]) / "app.db").is_file()
    assert (Path(paths["storage"]) / "original-video").read_bytes() == b"original user video"
    preserved = list(Path(paths["storage"]).parent.glob("storage.before-restore-*"))
    assert len(preserved) == 1 and (preserved[0] / "new-user-write").read_bytes() == b"preserve across rollback"


def test_late_task_activity_reopens_frontend_without_killing_backend(updater, transaction):
    agent, calls, failure, deploy, _, _, _ = transaction
    failure["at"] = "busy-window"
    original = (deploy / ".env").read_bytes()
    with pytest.raises(updater.UpdateError, match="busy-window"):
        agent.update()
    mutations = [call for call in calls if call[0] == "compose" and call[2] in {"stop", "up"}]
    assert mutations[0][2:] == ("stop", "frontend")
    assert all("backend" not in call[3:] for call in mutations)
    assert any(call[2] == "up" and call[-1] == "frontend" for call in mutations)
    assert not agent.pending.exists() and not agent.state_path.exists()
    assert (deploy / ".env").read_bytes() == original
    assert not (agent.root / "backups" / agent.stamp / "database").exists()


def test_space_check_aggregates_backup_and_restore_on_shared_disk(updater, tmp_path, monkeypatch):
    from types import SimpleNamespace
    first, second = tmp_path / "backup", tmp_path / "data"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda _: SimpleNamespace(free=1300 * 1024**2))
    with pytest.raises(updater.UpdateError, match="磁盘不足"):
        updater.check_space([(first, 600 * 1024**2), (second, 600 * 1024**2)])
    updater.check_space([(first, 200 * 1024**2), (second, 200 * 1024**2)])


def test_second_updater_is_locked_out(updater, transaction):
    agent, _, _, deploy, _, _, _ = transaction
    with pytest.raises(updater.UpdateError, match="另一个升级"):
        updater.Updater(deploy, agent.bundle)


def test_mountpoint_data_directory_is_rejected_before_upgrade(updater, runtime, monkeypatch):
    config, backend, frontend, paths = runtime
    monkeypatch.setattr(updater.os.path, "ismount", lambda p: str(p) == paths["database"])
    with pytest.raises(updater.UpdateError, match="挂载点"):
        updater.validate_runtime(config, backend, frontend)


def test_audit_operations_script_is_restored_with_config(updater, tmp_path):
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    (deployment / ".env").write_text("COMPOSE_FILE=compose.yaml\n")
    old = "#!/usr/bin/env bash\necho old\n"
    (deployment / "export-audit.sh").write_text(old)
    tool = updater.Updater(deployment, tmp_path / "bundle")
    backup = tmp_path / "backup"
    backup.mkdir()
    try:
        tool.snapshot_files(backup)
        (deployment / "export-audit.sh").write_text("new")
        tool.restore_files(backup)
        assert (deployment / "export-audit.sh").read_text() == old
    finally:
        tool.log.close()
        tool.lock.close()


def containerd_store(updater, agent, manifest, monkeypatch, *, backend_id=None):
    """A daemon using the containerd image store: image IDs are manifest digests, so
    the packaged config digests do not resolve. Real archive parsing, mocked Docker."""
    packaged = {image["tag"]: image["id"] for image in manifest["images"].values()}
    local = {tag: "sha256:" + str(i + 7) * 64 for i, tag in enumerate(packaged)}
    archive = agent.bundle / "images.tar"
    with tarfile.open(archive, "w") as tar:
        def add(name, value):
            data = json.dumps(value).encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add("manifest.json", [{"Config": "blobs/sha256/" + image.split(":")[1], "RepoTags": [tag], "Layers": []} for tag, image in packaged.items()])
        add("index.json", {"schemaVersion": 2, "manifests": [
            {"digest": local[tag], "annotations": {"io.containerd.image.name": "docker.io/library/" + tag}} for tag in packaged]})
    manifest["files"]["images.tar"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    (agent.bundle / "manifest.json").write_text(json.dumps(manifest))
    if backend_id:
        local[manifest["images"]["backend"]["tag"]] = backend_id
    mocked = agent.docker_json

    def docker_json(*args):
        if args[:2] == ("image", "inspect"):
            if args[2] in packaged.values():
                raise updater.UpdateError("No such image: " + args[2])
            result = mocked(*args)
            result[0]["Id"] = local.get(args[2], args[2])
            return result
        return mocked(*args)

    monkeypatch.setattr(agent, "docker_json", docker_json)
    return local


def test_containerd_image_store_upgrades_with_the_daemons_own_image_ids(updater, transaction, monkeypatch):
    agent, calls, _, _, _, installed, manifest = transaction
    local = containerd_store(updater, agent, manifest, monkeypatch)
    agent.update()
    state = json.loads(agent.state_path.read_text())
    for name, image in manifest["images"].items():
        assert state["images"][name]["id"] == local[image["tag"]]
        assert state["images"][name]["packageId"] == image["id"]
    current = json.loads((agent.root / "current-compose.json").read_text())
    assert current["services"]["backend"]["image"] == local[manifest["images"]["backend"]["tag"]]
    # Installing the same package again recognises it through the recorded package ID.
    installed.clear()
    installed.update(state)
    calls.clear()
    agent.update()
    assert not any(call[:3] == ("run", "docker", "load") for call in calls)


def test_tagged_image_outside_the_verified_archive_is_rejected_before_stopping(updater, transaction, monkeypatch):
    agent, calls, _, _, _, _, manifest = transaction
    containerd_store(updater, agent, manifest, monkeypatch, backend_id="sha256:" + "0" * 64)
    with pytest.raises(updater.UpdateError, match="不属于本更新包"):
        agent.update()
    assert not any(call[0] == "compose" and call[2] == "stop" for call in calls)
    assert not agent.pending.exists()
