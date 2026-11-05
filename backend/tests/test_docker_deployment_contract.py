from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_compose_persists_database_storage_and_uses_one_worker() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    config = (ROOT / "backend/app/config.py").read_text(encoding="utf-8")

    assert "APP_STORAGE_DIR: /data/storage" in compose
    assert "APP_DB_FILE: /data/database/app.db" in compose
    assert "target: /data/storage" in compose
    assert "target: /data/database" in compose
    assert "target: /models/sam3" in (ROOT / "compose.gpu.yaml").read_text(encoding="utf-8")
    assert "APP_DATA_DIR: /data/database" in compose
    assert '"--workers", "1"' in (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert 'os.getenv("APP_DB_FILE")' in config


def test_nginx_serves_spa_and_proxies_api_without_exposing_backend() -> None:
    nginx = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "try_files $uri $uri/ /index.html" in nginx
    assert "client_max_body_size 8g" in nginx
    assert "set $backend_upstream backend:3000;" in nginx
    assert "resolver 127.0.0.11" in nginx
    assert "proxy_pass http://$backend_upstream;" in nginx
    backend_block = compose[compose.index("  backend:"):compose.index("  frontend:")]
    assert "ports:" not in backend_block


def test_docker_document_covers_build_pull_migration_and_backup() -> None:
    document = (ROOT / "docs/DOCKER_DEPLOYMENT.md").read_text(encoding="utf-8")

    for required in (
        "docker compose build --pull",
        "docker compose pull",
        "迁移现有数据库和 Storage",
        "备份与恢复",
        "APP_DATA_ROOT",
        "SAM3_MODEL_HOST_PATH",
    ):
        assert required in document
