# Celery Flower 监控配置

# 启动 Flower 监控服务
# 访问 http://localhost:5555 查看任务队列、Worker 状态、任务历史

celery -A worker.celery flower \
  --port=5555 \
  --basic_auth=admin:your_password \
  --logging=info \
  --db=/var/log/hexstrike/flower.db

# 生产环境建议：
# 1. 使用 Nginx 反向代理
# 2. 配置 HTTPS
# 3. 限制访问 IP
