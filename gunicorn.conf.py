"""
Gunicorn 配置文件 - 仅用于 Web API 服务

架构说明：
- Gunicorn 仅运行 Flask Web 应用
- Celery Worker 独立运行（通过 worker.py）
- 两者完全解耦，可独立扩展
"""

import multiprocessing

# 服务器绑定
bind = "0.0.0.0:8888"

# Worker 配置
workers = min(multiprocessing.cpu_count(), 4)  # API Worker 数量
worker_class = "sync"
timeout = 120

# 日志配置
accesslog = "-"
errorlog = "-"
loglevel = "info"

# PID 文件
pidfile = "/tmp/gunicorn.pid"

# 预加载应用（Web 服务可以预加载）
preload_app = True

# Worker 初始化钩子
def post_worker_init(worker):
    """Worker 初始化后的钩子"""
    worker.log.info(f"🔧 Gunicorn Worker {worker.pid} initialized")


# 应用加载钩子
def post_fork(server, worker):
    """fork 后的钩子"""
    server.log.info(f"✅ Worker spawned: {worker.pid}")
