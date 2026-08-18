import os
from enum import Enum
from scripts.crypto import decode_b64

class Environment(Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TESTING = "testing"

def get_decoded_env(var_name: str, default: str = None) -> str:
    """
    从环境变量读取值并尝试 Base64 解码
    """
    raw_val = os.environ.get(var_name)
    if not raw_val:
        return default

    # 尝试解码，如果失败则返回原值
    return decode_b64(raw_val)

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "hexstrike-dev-secret")
    JSON_SORT_KEYS = False

    # Redis
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    REDIS_TASK_QUEUE = "hexstrike:tasks"
    REDIS_LOG_CHANNEL = "hexstrike:logs"
    REDIS_PROGRESS_CHANNEL = "hexstrike:progress"

    # MySQL / SQLite
    # 如果未配置 MySQL 环境变量，自动使用 SQLite（开发环境）
    MYSQL_HOST = get_decoded_env("MYSQL_HOST")
    MYSQL_PORT = get_decoded_env("MYSQL_PORT")
    MYSQL_USER = get_decoded_env("MYSQL_USER")
    MYSQL_PASSWORD = get_decoded_env("MYSQL_PASSWORD")
    MYSQL_DB = get_decoded_env("MYSQL_DB")

    if MYSQL_HOST and MYSQL_USER and MYSQL_DB:
        SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT or 3306}/{MYSQL_DB}"
    else:
        # 开发环境默认使用 SQLite
        SQLALCHEMY_DATABASE_URI = "sqlite:///hexstrike.db"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Task Pool
    MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 3))

    # Tool Health Check
    AUTO_HEALTH_CHECK = os.environ.get("AUTO_HEALTH_CHECK", "true").lower() == "true"
    HEALTH_CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", 300))  # 默认5分钟
    HEALTH_CHECK_TIMEOUT = int(os.environ.get("HEALTH_CHECK_TIMEOUT", 30))  # 单个工具检测超时

    # MCP
    MCP_VERSION = "2.0"
    MCP_SERVER_NAME = "HexStrike AI"
    MCP_SERVER_VERSION = "1.0.0"

class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_ECHO = False

class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_ECHO = False

class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    if env == "production":
        return ProductionConfig
    elif env == "testing":
        return TestingConfig
    return DevelopmentConfig
