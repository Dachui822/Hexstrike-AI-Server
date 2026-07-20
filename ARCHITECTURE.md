# HexStrike AI 架构文档

## 架构概览

HexStrike AI 采用 **Celery 分布式任务队列** 架构，实现了 Web 服务与任务执行的完全解耦。

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                              │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ Web Browser │  │ MCP Client   │  │ Mobile App / API        │ │
│  └──────┬──────┘  └──────┬───────┘  └───────────┬─────────────┘ │
│         │                 │                      │                │
│         └─────────────────┴──────────────────────┘                │
│                           │                                       │
│                      HTTP / JSON-RPC                              │
───────────────────────────┼───────────────────────────────────────┘
                            │
        ┌───────────────────┴───────────────────┐
        │                                       │
        ▼                                       ▼
┌───────────────────┐                  ┌─────────────────────┐
│  Gunicorn (Web)   │                  │  Celery Worker      │
│  Port: 8888       │                  │  (Independent)      │
│                   │                  │                     │
│  - REST API       │                  │  - Task Execution   │
│  - MCP Endpoint   │                  │  - Tool Running     │
│  - Task Submit    │───── Redis ─────▶│  - Concurrency Ctrl │
│                   │   (Broker)       │                     │
└───────────────────┘                  └─────────────────────┘
        │                                       │
        │                                       │
        ▼                                       ▼
───────────────────┐                  ┌─────────────────────┐
│   MySQL           │                  │  Security Tools     │
│   (Persistence)   │                  │  (nmap, dirsearch,  │
│                   │                  │   nuclei, etc.)     │
│  - Task Records   │                  │                     │
│  - Task Logs      │                  │                     │
│  - Tool Registry  │                  │                     │
└───────────────────┘                  └─────────────────────┘
```

## 核心组件

### 1. Web 服务 (Gunicorn + Flask)

**文件**: `run.py`, `gunicorn.conf.py`, `app/__init__.py`

**职责**:
- 处理 HTTP 请求（REST API + MCP 协议）
- 任务提交（写入 MySQL + 推送 Celery 队列）
- 任务状态查询
- 日志实时推送

**启动命令**:
```bash
# 开发环境
python run.py

# 生产环境
gunicorn -c gunicorn.conf.py run:app
```

### 2. 任务队列 (Celery)

**文件**: `app/celery_app.py`, `app/tasks/worker_tasks.py`, `worker.py`

**职责**:
- 任务调度与分发
- 并发控制
- 任务重试
- 超时控制
- 结果存储

**启动命令**:
```bash
# 开发环境（单 Worker）
python worker.py

# 生产环境（多 Worker）
celery -A worker.celery worker \
  --loglevel=info \
  --concurrency=10 \
  --pool=gevent \
  -Q hexstrike_default,hexstrike_high_priority,hexstrike_low_priority
```

### 3. 消息代理 (Redis)

**作用**:
- 任务队列（Celery Broker）
- 结果后端（Celery Backend）
- 实时日志推送
- 分布式锁（Leader 选举）

**配置**:
```bash
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
```

### 4. 数据持久化 (MySQL)

**作用**:
- 任务记录存储
- 任务日志存储
- 工具注册表
- 配置信息

**配置**:
```bash
DB_HOST=localhost
DB_PORT=3306
DB_USER=hexstrike
DB_PASSWORD=your_password
DB_NAME=hexstrike
```

## 任务生命周期

```
1. 提交阶段
   Client ──HTTP──▶ Web API ──┬── MySQL (Task PENDING)
                              ── Redis (Celery Queue)

2. 调度阶段
   Celery Worker ◀── Redis (BLPOP)
   Task Status: PENDING → RUNNING

3. 执行阶段
   Celery Worker ──▶ subprocess (Security Tool)
                    ├─ stdout ──▶ Redis Pub/Sub (Real-time Logs)
                    └─ output ──▶ /tmp/{task_id}.log

4. 完成阶段
   Task Status: RUNNING ─┬── SUCCESS
                          ├── FAILED
                          └── CANCELLED
   MySQL: updated_at, completed_at, output_path

5. 清理阶段
   Redis: task:{id}:lock, task:{id}:logs (TTL 自动过期)
```

## 队列优先级

| 队列名称 | 优先级 | 用途 |
|----------|--------|------|
| `hexstrike_high_priority` | 8-10 | 紧急任务、VIP 用户任务 |
| `hexstrike_default` | 3-7 | 普通任务 |
| `hexstrike_low_priority` | 0-2 | 批量任务、低优先级任务 |

## 并发控制

### Web 服务并发
```bash
# Gunicorn Worker 数量
workers = min(multiprocessing.cpu_count(), 4)
```

### 任务执行并发
```bash
# Celery Worker 并发数
WORKER_CONCURRENCY=10

# 每个 Worker 可处理的最大任务数
worker_prefetch_multiplier=1  # 公平调度
```

### 任务超时控制
```bash
TASK_TIME_LIMIT=3600          # 绝对超时：1 小时（强制终止）
TASK_SOFT_TIME_LIMIT=3300     # 软超时：55 分钟（触发回调）
IDLE_TIMEOUT=300              # 空闲超时：5 分钟无输出则终止
```

## 安全机制

### 1. 命令注入防护
- 工具白名单验证
- 目标参数格式验证（IP/域名/URL）
- 危险字符过滤（`;|&$\`(){}[]<>`）
- 使用 `shell=False` + 参数列表执行

### 2. 资源隔离
- Celery Worker 独立进程运行
- 子进程进程组管理（`os.setsid`）
- 超时自动终止

### 3. 任务取消
- Redis 取消标志（`task:{id}:cancel`）
- 数据库状态同步
- 优雅终止（捕获 `SIGTERM`）

## 部署架构

### 开发环境
```
单机部署：
├─ Redis (docker)
├─ MySQL (docker)
├─ Web Server (python run.py)
└─ Celery Worker (python worker.py)
```

### 生产环境
```
多机部署：
─ Redis Cluster (3 节点)
├─ MySQL Cluster (主从复制)
├─ Web Server × N (Gunicorn + Nginx)
└─ Celery Worker × M (独立服务器)
```

## 监控与运维

### 1. Celery 监控 (Flower)
```bash
# 启动 Flower
celery -A worker.celery flower --port=5555

# 访问 http://localhost:5555
# 查看任务队列、Worker 状态、任务历史
```

### 2. 日志查看
```bash
# Web 服务日志
tail -f hexstrike.log

# Celery Worker 日志
tail -f hexstrike_worker.log

# 实时日志（Redis Pub/Sub）
redis-cli SUBSCRIBE hexstrike:logs
```

### 3. 任务清理
```bash
# 清理卡住的任务（RUNNING > 1 小时）
curl -X POST http://localhost:8888/api/tasks/cleanup
```

## 故障排查

### 问题 1: 任务一直处于 PENDING 状态

**原因**: Celery Worker 未启动或 Redis 连接失败

**解决**:
```bash
# 检查 Redis
redis-cli ping

# 检查 Celery Worker 状态
ps aux | grep celery

# 重启 Worker
pkill -f celery
python worker.py
```

### 问题 2: 任务执行超时

**原因**: 工具执行时间超过 `TASK_TIME_LIMIT`

**解决**:
```bash
# 增加超时时间
export TASK_TIME_LIMIT=7200  # 2 小时

# 或针对特定工具设置不同的超时
# (需要在 worker_tasks.py 中自定义)
```

### 问题 3: Worker 内存泄漏

**原因**: 子进程未正确清理或 Python 对象未释放

**解决**:
```bash
# 启用 Worker 自动回收
celery worker --max-tasks-per-child=100

# 监控系统资源
watch -n 1 'ps aux | grep celery | awk '{print $2, $4, $6}''
```

## 性能优化

### 1. 队列优化
- 使用优先级队列分离紧急任务和普通任务
- 配置 `worker_prefetch_multiplier=1` 避免任务积压

### 2. 数据库优化
- 任务日志分批写入（批量插入）
- 定期清理历史任务（归档或 TTL）

### 3. Redis 优化
- 使用 Redis Cluster 提高可用性
- 配置 `maxmemory-policy allkeys-lru` 避免内存溢出

## 扩展阅读

- [Celery 官方文档](https://docs.celeryq.dev/)
- [Flower 监控工具](https://github.com/mher/flower)
- [Redis 最佳实践](https://redis.io/topics/best-practices)
