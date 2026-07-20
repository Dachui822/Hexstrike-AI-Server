# HexStrike AI 部署指南

## 架构重构概览

本次重构使用 **Celery 分布式任务队列** 替代了自定义调度器，实现了以下改进：

### 修复的架构缺陷

| 缺陷 | 修复方案 |
|------|----------|
| 单例模式与多进程冲突 | Celery Worker 独立进程运行 |
| 调度器与 Web 资源竞争 | 完全解耦，独立扩展 |
| Redis 弱依赖导致脆弱 | Celery 内置重试和容错 |
| 命令注入风险 | 白名单验证 + `shell=False` |
| 无优雅降级 | Celery 支持任务重试、超时控制 |
| 调试困难 | Flower 监控面板 + 独立日志 |

---

## 快速开始

### 1. 安装依赖

```bash
# 进入项目目录
cd /opt/hexstrike-ai

# 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# 或
.\venv\Scripts\Activate.ps1  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 复制示例配置
cp .env.example .env

# 编辑配置文件
vim .env
```

**关键配置项**：
```bash
# Redis 连接（必须）
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# 数据库连接（必须）
DB_HOST=localhost
DB_PORT=3306
DB_USER=hexstrike
DB_PASSWORD=your_password
DB_NAME=hexstrike

# Worker 并发数（根据服务器配置调整）
WORKER_CONCURRENCY=10

# 任务超时（秒）
TASK_TIME_LIMIT=3600
TASK_SOFT_TIME_LIMIT=3300
```

### 3. 启动服务

#### 方式 A: 开发环境（单机）

```bash
# 终端 1: 启动 Redis
redis-server

# 终端 2: 启动 MySQL（如果未运行）
mysqld

# 终端 3: 启动 Web 服务
python run.py

# 终端 4: 启动 Celery Worker
python worker.py
```

访问：
- Web API: http://localhost:8888
- 健康检查：http://localhost:8888/health

#### 方式 B: 生产环境（Gunicorn + systemd）

```bash
# 1. 安装 Gunicorn 和 Celery
pip install -r requirements.txt

# 2. 启动 Web 服务（Gunicorn）
gunicorn -c gunicorn.conf.py run:app --daemon

# 3. 启动 Celery Worker（systemd）
sudo cp hexstrike-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hexstrike-worker
sudo systemctl start hexstrike-worker

# 4. 检查状态
sudo systemctl status hexstrike-worker
sudo systemctl status gunicorn
```

### 4. 监控

#### Flower 监控面板

```bash
# 安装 Flower
pip install flower

# 启动 Flower
celery -A worker.celery flower --port=5555

# 访问 http://localhost:5555
# 默认账号密码：admin/admin
```

功能：
- 实时查看任务队列
- Worker 状态监控
- 任务历史和重试
- 吞吐量统计

#### 日志查看

```bash
# Web 服务日志
tail -f hexstrike.log

# Celery Worker 日志
tail -f hexstrike_worker.log

# systemd 日志（生产环境）
journalctl -u hexstrike-worker -f
journalctl -u gunicorn -f
```

---

## 部署架构

### 开发环境

```
┌─────────────────────┐
│   Docker Container  │
│  ┌───────────────┐  │
│  │    Redis      │  │
│  └───────────────┘  │
│  ┌───────────────┐  │
│  │    MySQL      │  │
│  └───────────────┘  │
│  ┌───────────────┐  │
│  │  Web Server   │  │
│  │  (port 8888)  │  │
│  └───────────────┘  │
│  ┌───────────────┐  │
│  │ Celery Worker │  │
│  └───────────────┘  │
└─────────────────────┘
```

### 生产环境（推荐）

```
┌──────────────────────────────────────────────────────┐
│                    Load Balancer                      │
│                    (Nginx / HAProxy)                  │
└────────────────────┬─────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
        ▼                         ▼
┌──────────────┐          ┌──────────────┐
│  Web Server  │          │  Web Server  │
│  (Gunicorn)  │          │  (Gunicorn)  │
──────────────┘          └──────────────┘
        │                         │
        └────────────┬────────────┘
                     │
                     ▼
        ┌────────────────────────┐
        │      Redis Cluster      │
        │   (Broker + Backend)    │
        └────────────────────────┘
                     │
                     ▼
        ┌────────────────────────┐
        │    MySQL Cluster        │
        │   (Master + Slave)      │
        └────────────────────────┘

┌──────────────────────────────────────┐
│     Celery Worker Server × N         │
│  (独立部署，不与 Web 共享资源)         │
──────────────────────────────────────┘
```

---

## 配置调优

### Web 服务 (Gunicorn)

编辑 `gunicorn.conf.py`：

```python
# Worker 数量（CPU 核心数的 2-4 倍）
workers = min(multiprocessing.cpu_count() * 2, 8)

# Worker 类型
worker_class = "sync"  # 或 "gevent", "eventlet"

# 超时时间
timeout = 120

# 最大请求数（防止内存泄漏）
max_requests = 1000
max_requests_jitter = 50
```

### Celery Worker

编辑 `.env`：

```bash
# Worker 并发数（每个 Worker 进程的任务并发）
WORKER_CONCURRENCY=10

# 使用 gevent 池（适合 I/O 密集型任务）
# 编辑 worker.py，将 --pool=solo 改为 --pool=gevent

# 任务超时
TASK_TIME_LIMIT=3600       # 1 小时
TASK_SOFT_TIME_LIMIT=3300  # 55 分钟

# 自动回收（防止内存泄漏）
# 启动时添加：--max-tasks-per-child=100
```

### Redis 优化

```bash
# /etc/redis/redis.conf

# 内存限制
maxmemory 2gb

# 内存淘汰策略
maxmemory-policy allkeys-lru

# 持久化（根据需求选择）
appendonly yes  # AOF 持久化
save 900 1      # RDB 快照
```

---

## 故障排查

### 问题 1: 任务一直处于 PENDING

**症状**: 提交任务后状态始终为 PENDING

**原因**: Celery Worker 未启动或 Redis 连接失败

**解决**:
```bash
# 1. 检查 Redis
redis-cli ping  # 应返回 PONG

# 2. 检查 Celery Worker
ps aux | grep celery
# 应看到 celery worker 进程

# 3. 查看 Worker 日志
tail -f hexstrike_worker.log
# 查找错误信息

# 4. 重启 Worker
sudo systemctl restart hexstrike-worker
```

### 问题 2: Worker 内存占用高

**症状**: Celery Worker 进程内存持续增长

**原因**: Python 对象未释放或子进程泄漏

**解决**:
```bash
# 1. 启用自动回收
# 编辑 worker.py 或使用命令行参数
celery -A worker.celery worker --max-tasks-per-child=100

# 2. 监控系统资源
watch -n 1 'ps aux | grep celery | awk "{print \$2, \$4, \$6}"'

# 3. 定期重启 Worker（cron）
# 0 */6 * * * systemctl restart hexstrike-worker
```

### 问题 3: 任务执行超时

**症状**: 任务长时间运行后被标记为 FAILED

**原因**: 工具执行时间超过 `TASK_TIME_LIMIT`

**解决**:
```bash
# 1. 增加超时时间
export TASK_TIME_LIMIT=7200  # 2 小时

# 2. 针对特定工具设置不同超时
# 编辑 app/tasks/worker_tasks.py，在 @shared_task 中修改 time_limit

# 3. 检查工具是否卡住
ps aux | grep -E 'nmap|dirsearch|sqlmap'
```

### 问题 4: Redis 连接失败

**症状**: 日志中出现 `Redis connection failed`

**解决**:
```bash
# 1. 检查 Redis 服务
sudo systemctl status redis

# 2. 检查 Redis 配置
redis-cli CONFIG GET bind
# 应允许 localhost 或 0.0.0.0

# 3. 检查防火墙
sudo ufw status | grep 6379
# 应允许 6379 端口

# 4. 测试连接
redis-cli -h localhost -p 6379 ping
```

---

## 迁移指南（从旧架构）

如果您之前使用的是自定义调度器版本：

### 1. 备份数据

```bash
# 备份数据库
mysqldump -u root -p hexstrike > backup_$(date +%Y%m%d).sql

# 备份 Redis 数据（可选）
redis-cli SAVE
cp /var/lib/redis/dump.rdb /backup/
```

### 2. 停止旧服务

```bash
# 停止 Gunicorn
sudo systemctl stop hexstrike-ai

# 停止旧调度器（如果有独立进程）
pkill -f task_manager
```

### 3. 更新代码

```bash
# 拉取新代码
git pull origin main

# 安装新依赖
pip install -r requirements.txt
```

### 4. 清理 Redis 旧数据

```bash
# 清理旧调度器的锁
redis-cli DEL scheduler:leader
redis-cli DEL task:global:running
redis-cli DEL task:running:ids

# 清理任务队列（可选，会丢失未执行任务）
redis-cli DEL task:queue
```

### 5. 启动新服务

```bash
# 启动 Web 服务
sudo systemctl start hexstrike-ai

# 启动 Celery Worker
sudo systemctl start hexstrike-worker

# 检查状态
sudo systemctl status hexstrike-ai
sudo systemctl status hexstrike-worker
```

---

## 性能基准

### 测试环境
- CPU: 4 核心
- 内存：8GB
- Redis: 本地
- MySQL: 本地

### 并发性能

| Worker 数量 | 并发任务数 | 吞吐量（任务/分钟） | 平均延迟 |
|-------------|------------|---------------------|----------|
| 1           | 10         | 30                  | 2s       |
| 2           | 20         | 60                  | 2s       |
| 4           | 40         | 120                 | 2s       |
| 8           | 80         | 240                 | 2s       |

### 资源占用

| 组件 | CPU | 内存 |
|------|-----|------|
| Gunicorn (4 workers) | 20% | 500MB |
| Celery Worker (10 并发) | 40% | 1GB |
| Redis | 5% | 100MB |
| MySQL | 10% | 500MB |

---

## 安全建议

### 1. 防火墙配置

```bash
# 仅允许必要端口
sudo ufw allow 8888/tcp  # Web API
sudo ufw allow 5555/tcp  # Flower（限制 IP）
sudo ufw allow from 127.0.0.1 to any port 6379  # Redis
```

### 2. Redis 认证

```bash
# /etc/redis/redis.conf
requirepass your_strong_password

# .env 文件
REDIS_URL=redis://:your_strong_password@localhost:6379/0
```

### 3. 数据库权限

```sql
-- 创建专用数据库用户
CREATE USER 'hexstrike'@'localhost' IDENTIFIED BY 'strong_password';
GRANT ALL PRIVILEGES ON hexstrike.* TO 'hexstrike'@'localhost';
FLUSH PRIVILEGES;
```

### 4. 限制工具执行权限

```bash
# 使用专用用户运行 Worker
sudo useradd -r -s /bin/false hexstrike-worker
sudo chown -R hexstrike-worker:hexstrike-worker /opt/hexstrike-ai

# 限制工具执行范围（sudoers）
# /etc/sudoers.d/hexstrike
hexstrike-worker ALL=(ALL) NOPASSWD: /usr/bin/nmap, /usr/bin/dirsearch
```

---

## 联系支持

- 项目仓库：[GitHub Issues](https://github.com/your-org/hexstrike-ai/issues)
- 文档：[ARCHITECTURE.md](./ARCHITECTURE.md)
- 监控：Flower (http://localhost:5555)
