#!/usr/bin/env python3
"""
HexStrike AI Celery Worker 启动脚本

使用方法：
    # 开发环境（单 Worker）
    python worker.py
    
    # 生产环境（多 Worker 并发）
    celery -A worker.celery worker --loglevel=info --concurrency=10 --pool=gevent
    
    # 使用 systemd 管理
    sudo systemctl start hexstrike-worker

环境变量：
    CELERY_BROKER_URL: Redis  broker URL（默认：redis://localhost:6379/0）
    CELERY_RESULT_BACKEND: Redis backend URL（默认：redis://localhost:6379/1）
    WORKER_CONCURRENCY: Worker 并发数（默认：10）
    TASK_TIME_LIMIT: 任务绝对超时（秒，默认：3600）
    TASK_SOFT_TIME_LIMIT: 任务软超时（秒，默认：3300）
"""

import os
import sys
import logging
from pathlib import Path

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

# ============================================================================
# 日志配置（延迟初始化，避免导入时创建文件）
# ============================================================================

def setup_logging():
    """配置日志系统"""
    # 优先使用 /var/log/hexstrike_ai/
    log_dir = Path('/var/log/hexstrike_ai')
    log_file = log_dir / 'hexstrike_worker.log'

    # 检查是否可写，否则使用 /tmp
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        test_file = log_dir / '.write_test'
        test_file.touch()
        test_file.unlink()
    except (OSError, PermissionError):
        log_dir = Path('/tmp')
        log_file = log_dir / 'hexstrike_worker.log'

    logging.basicConfig(
        level=logging.INFO,
        format='[ HexStrike Worker] %(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding='utf-8')
        ]
    )

    return logging.getLogger(__name__)

logger = setup_logging()
logger.info("🚀 HexStrike AI Celery Worker starting...")

# ============================================================================
# Celery 应用导入
# ============================================================================

from app.celery_app import celery

# ============================================================================
# 启动检查
# ============================================================================

def check_dependencies():
    """检查依赖服务"""
    logger.info("🔍 Checking dependencies...")
    
    # 检查 Redis
    try:
        from app.extensions import redis_client
        if redis_client:
            redis_client.ping()
            logger.info("✅ Redis connected")
        else:
            logger.error("❌ Redis client not initialized")
            return False
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")
        return False
    
    # 检查数据库
    try:
        from app.extensions import db
        from app import create_app
        app = create_app()
        with app.app_context():
            db.engine.connect()
            logger.info("✅ Database connected")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return False
    
    return True


def print_startup_info():
    """打印启动信息"""
    logger.info("=" * 60)
    logger.info("🔥 HexStrike AI Celery Worker")
    logger.info("=" * 60)
    logger.info(f" Broker: {os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')}")
    logger.info(f"💾 Backend: {os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/1')}")
    logger.info(f" Concurrency: {os.environ.get('WORKER_CONCURRENCY', '10')}")
    logger.info(f"⏱️  Time Limit: {os.environ.get('TASK_TIME_LIMIT', '3600')}s")
    logger.info(f"⏱️  Soft Limit: {os.environ.get('TASK_SOFT_TIME_LIMIT', '3300')}s")
    logger.info("=" * 60)


# ============================================================================
# 主入口
# ============================================================================

if __name__ == '__main__':
    # 依赖检查
    if not check_dependencies():
        logger.error("❌ Dependency check failed, exiting...")
        sys.exit(1)
    
    # 打印启动信息
    print_startup_info()
    
    # 启动 Worker
    logger.info("🎯 Starting Celery Worker...")
    logger.info("ℹ️  Use Ctrl+C to stop")
    
    # 执行 Celery Worker
    # 命令行方式启动（推荐）
    argv = [
        'worker',
        '--loglevel=info',
        '--concurrency=' + os.environ.get('WORKER_CONCURRENCY', '10'),
        '--pool=prefork',  # 使用 prefork 池（支持 terminate）
        '-Q', 'hexstrike_default,hexstrike_high_priority,hexstrike_low_priority',
    ]

    # 生产环境可使用 gevent 池（但不支持 terminate_job）
    # 如果需要使用 gevent，设置 USE_GEVENT=true
    if os.environ.get('USE_GEVENT'):
        argv[-2] = '--pool=gevent'
        logger.warning("️  Using gevent pool - terminate_job will NOT work (use Redis cancel flag instead)")
    
    try:
        celery.worker_main(argv)
    except KeyboardInterrupt:
        logger.info("\n Worker stopped by user")
    except Exception as e:
        logger.error(f"❌ Worker error: {e}", exc_info=True)
        sys.exit(1)
