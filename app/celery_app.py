"""
Celery 应用配置与初始化
架构说明：
- Celery 作为独立 Worker 进程运行，与 Web 服务完全解耦
- Redis 作为消息代理和结果后端
- 支持任务重试、超时控制、并发限制
"""

import os
import logging
from celery import Celery
from celery.signals import setup_logging, worker_process_init, worker_process_shutdown
from kombu import Exchange, Queue

# ============================================================================
# 配置常量
# ============================================================================

DEFAULT_CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
DEFAULT_CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
DEFAULT_TASK_TIME_LIMIT = int(os.environ.get("TASK_TIME_LIMIT", 3600))  # 任务绝对超时：1 小时
DEFAULT_TASK_SOFT_TIME_LIMIT = int(os.environ.get("TASK_SOFT_TIME_LIMIT", 3300))  # 软超时：55 分钟
DEFAULT_WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", 10))  # 每个 Worker 并发数
DEFAULT_TASK_ACKS_LATE = os.environ.get("TASK_ACKS_LATE", "true").lower() == "true"  # 延迟确认（支持重试）
DEFAULT_TASK_REJECT_ON_WORKER_LOST = os.environ.get("TASK_REJECT_ON_WORKER_LOST", "true").lower() == "true"  # Worker 丢失时拒绝任务

# ============================================================================
# Celery 应用创建
# ============================================================================

def make_celery(app_name: str = "hexstrike") -> Celery:
    """创建 Celery 应用实例"""
    
    celery_app = Celery(app_name)
    
    # 基础配置
    celery_app.conf.update(
        # 消息代理和结果后端
        broker_url=DEFAULT_CELERY_BROKER_URL,
        result_backend=DEFAULT_CELERY_RESULT_BACKEND,
        
        # 任务序列化
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Shanghai",
        enable_utc=True,
        
        # 任务路由
        task_default_queue="hexstrike_default",
        task_queues=(
            Queue("hexstrike_default", Exchange("hexstrike_default"), routing_key="default"),
            Queue("hexstrike_high_priority", Exchange("hexstrike_high_priority"), routing_key="high"),
            Queue("hexstrike_low_priority", Exchange("hexstrike_low_priority"), routing_key="low"),
        ),
        task_default_routing_key="default",
        
        # 任务执行限制
        task_time_limit=DEFAULT_TASK_TIME_LIMIT,
        task_soft_time_limit=DEFAULT_TASK_SOFT_TIME_LIMIT,
        worker_prefetch_multiplier=1,  # 每次只取 1 个任务（公平调度）
        worker_concurrency=DEFAULT_WORKER_CONCURRENCY,
        
        # 可靠性配置
        task_acks_late=DEFAULT_TASK_ACKS_LATE,
        task_reject_on_worker_lost=DEFAULT_TASK_REJECT_ON_WORKER_LOST,
        task_track_started=True,
        
        # 结果过期时间（24 小时）
        result_expires=86400,
        
        # 重试配置
        task_default_retry_delay=60,  # 默认重试间隔 60 秒
        task_max_retries=3,  # 最大重试 3 次
        task_default_rate_limit=None,  # 不限速
        
        # 导入任务模块
        imports=(
            "app.tasks.worker_tasks",
        ),
    )
    
    # 自动发现任务
    celery_app.autodiscover_tasks(["app.tasks"])
    
    return celery_app


# ============================================================================
# 信号处理
# ============================================================================

@setup_logging.connect
def config_loggers(*args, **kwargs):
    """配置 Celery Worker 日志"""
    logger = logging.getLogger("celery")
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "[📦 Celery] %(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)


@worker_process_init.connect
def on_worker_init(*args, **kwargs):
    """Worker 进程初始化钩子"""
    logger = logging.getLogger("celery")
    logger.info(" Celery Worker process initializing...")


@worker_process_shutdown.connect
def on_worker_shutdown(*args, **kwargs):
    """Worker 进程关闭钩子"""
    logger = logging.getLogger("celery")
    logger.info("🛑 Celery Worker process shutting down...")


# ============================================================================
# 全局实例
# ============================================================================

celery = make_celery()
