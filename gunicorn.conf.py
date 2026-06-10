import multiprocessing

bind = "0.0.0.0:8888"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
timeout = 120

accesslog = "-"
errorlog = "-"
loglevel = "info"

pidfile = "/tmp/gunicorn.pid"
preload_app = True
