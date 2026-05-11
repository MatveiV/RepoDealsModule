"""
Configuration management for REPO Module.
Supports demo (SQLite) and production (PostgreSQL) modes.
"""
import os
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class DatabaseConfig(BaseModel):
    type: str = "sqlite"
    path: Optional[str] = "repo_module.db"
    host: Optional[str] = "localhost"
    port: Optional[int] = 5432
    name: Optional[str] = "repo_module"
    user: Optional[str] = "repo_user"
    password: Optional[str] = None
    pool_size: int = 10
    max_overflow: int = 20


class BatchConfig(BaseModel):
    chunk_size: int = 1000
    max_retries: int = 3
    retry_delay_seconds: int = 5


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    prefix: str = "/api/v1/repo"


class Settings(BaseSettings):
    MODE: str = "demo"
    SQLITE_PATH: str = "repo_module.db"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # Production DB
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "repo_module"
    DB_USER: str = "repo_user"
    DB_PASSWORD: str = ""

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"

    model_config = {"env_file": ".env", "extra": "ignore"}


def load_config() -> dict:
    """Load configuration from config.yaml, overriding with env variables."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    cfg = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    settings = Settings()
    mode = os.environ.get("MODE", cfg.get("mode", "demo")).lower()
    cfg["mode"] = mode
    return cfg


_config = None


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_mode() -> str:
    return get_config().get("mode", "demo")


def is_demo() -> bool:
    return get_mode() == "demo"


def get_db_url() -> str:
    """Return SQLAlchemy database URL based on current mode."""
    cfg = get_config()
    settings = Settings()
    mode = cfg.get("mode", "demo")

    if mode == "demo":
        sqlite_path = os.environ.get("SQLITE_PATH", cfg.get("demo", {}).get("database", {}).get("path", "repo_module.db"))
        if sqlite_path == ":memory:":
            return "sqlite+aiosqlite:///:memory:"
        return f"sqlite+aiosqlite:///{sqlite_path}"
    else:
        password = settings.DB_PASSWORD or cfg.get("production", {}).get("database", {}).get("password", "")
        host = settings.DB_HOST
        port = settings.DB_PORT
        name = settings.DB_NAME
        user = settings.DB_USER
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


def get_incoming_dir() -> str:
    cfg = get_config()
    if is_demo():
        return cfg.get("demo", {}).get("incoming_dir", "demo_data/incoming")
    return ""


def get_output_dir() -> str:
    cfg = get_config()
    if is_demo():
        return cfg.get("demo", {}).get("output_dir", "demo_data/out")
    return ""


def get_chunk_size() -> int:
    cfg = get_config()
    return cfg.get("batch", {}).get("chunk_size", 1000)


def get_max_retries() -> int:
    cfg = get_config()
    return cfg.get("batch", {}).get("max_retries", 3)
