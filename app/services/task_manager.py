import json
import uuid
import time
import os
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from app.extensions import db
import app.extensions as extensions
from app.models.task import Task, TaskStatus, TaskLog
from app.services.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

# 调度器 Leader 选举锁
SCHEDULER_LEADER_KEY = "scheduler:leader"
SCHEDULER_LEADER_TTL = 30  # 锁过期时间（秒）

# 默认并发数：限制为 CPU 核心数或固定值，防止资源耗尽
_DEFAULT_MAX_WORKERS = min(int(os.environ.get("MAX_WORKERS", 5)), 20)

# Redis Lua 脚本：使用 Set 管理并发，天然防泄漏
LUA_ACQUIRE = """
local running_set = KEYS[1]
local limit = tonumber(ARGV[1])
local current = redis.call('SCARD', running_set)
if current < limit then
    redis.call('SADD', running_set, ARGV[2])
    return 1
end
return 0
"""

LUA_RELEASE = """
local running_set = KEYS[1]
redis.call('SREM', running_set, ARGV[1])
return 1
"""

class TaskManager:
    def __init__(self):
        self.max_workers = _DEFAULT_MAX_WORKERS
        # 线程池大小与 max_workers 对齐，避免过度创建线程
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers + 5)
        self.executor_instance = ToolExecutor()

        self._scheduler_running = False
        self._scheduler_thread = None
        self._leader_watchdog_thread = None

        # 注册 Lua 脚本
        if extensions.redis_client:
            self._acquire_script = extensions.redis_client.register_script(LUA_ACQUIRE)
            self._release_script = extensions.redis_client.register_script(LUA_RELEASE)

        # 启动后台调度器（带 Leader 选举）
        self._start_scheduler()

    def _start_scheduler(self):
        """启动后台调度线程（带 Leader 选举）"""
        if self._scheduler_running:
            return

        # 尝试获取 Leader 锁
        if extensions.redis_client:
            acquired = extensions.redis_client.set(
                SCHEDULER_LEADER_KEY, "1", nx=True, ex=SCHEDULER_LEADER_TTL
            )
            if not acquired:
                logger.info("🔒 Scheduler leader already elected, skipping startup")
                return

        self._scheduler_running = True
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()

        # 启动 Leader 续期看门狗
        self._leader_watchdog_thread = threading.Thread(target=self._leader_watchdog, daemon=True)
        self._leader_watchdog_thread.start()

        logger.info("🔄 Task Scheduler started (Leader elected)")

    def _leader_watchdog(self):
        """Leader 锁续期看门狗：每 10 秒续期一次"""
        while self._scheduler_running:
            try:
                if extensions.redis_client:
                    extensions.redis_client.setex(SCHEDULER_LEADER_KEY, SCHEDULER_LEADER_TTL, "1")
                time.sleep(10)
            except Exception as e:
                logger.error(f"Leader watchdog error: {e}")
                time.sleep(5)

    def _get_app(self):
        """安全获取 Flask 应用实例（兼容后台线程与扩展上下文）"""
        from flask import current_app
        try:
            return current_app._get_current_object()
        except RuntimeError:
            try:
                return db.get_app()
            except RuntimeError:
                return None

    def _scheduler_loop(self):
        """后台调度循环：从 Redis 队列拉取任务"""
        last_reconcile = 0
        reconcile_interval = 30  # 每 30 秒执行一次对账补偿
        running_set_key = "task:running:ids"

        while self._scheduler_running:
            try:
                if not extensions.redis_client:
                    time.sleep(5)
                    continue

                # 动态注册 Lua 脚本（防御性编程：处理 __init__ 中 Redis 未就绪的情况）
                if not hasattr(self, '_acquire_script') or not hasattr(self, '_release_script'):
                    try:
                        self._acquire_script = extensions.redis_client.register_script(LUA_ACQUIRE)
                        self._release_script = extensions.redis_client.register_script(LUA_RELEASE)
                        logger.info("✅ Lua scripts registered dynamically in scheduler")
                    except Exception as e:
                        logger.error(f"Failed to register Lua scripts: {e}")
                        time.sleep(5)
                        continue

                # 🔍 定期补偿：修复 MySQL PENDING 但 Redis 缺失的任务
                now = time.time()
                if now - last_reconcile > reconcile_interval:
                    self._reconcile_pending_tasks()
                    last_reconcile = now

                # 1. 检查全局并发限制（使用 Set 防泄漏）
                task_id_check = f"check_{int(time.time())}"
                can_run = self._acquire_script(
                    keys=[running_set_key],
                    args=[self.max_workers, task_id_check]
                )
                if not can_run:
                    time.sleep(1)
                    continue

                # 2. 从队列获取任务 (ZSET 按优先级排序)
                tasks = extensions.redis_client.zrangebyscore("task:queue", "-inf", "+inf", start=0, num=1)
                if tasks:
                    task_id = tasks[0]
                    # 尝试加锁，防止多进程重复执行
                    if extensions.redis_client.set(f"task:{task_id}:lock", "1", nx=True, ex=300):
                        extensions.redis_client.zrem("task:queue", task_id)
                        # 更新 Set 中的真实任务 ID
                        self._release_script(keys=[running_set_key], args=[task_id_check])
                        self._acquire_script(keys=[running_set_key], args=[self.max_workers, task_id])
                        # 提交到线程池
                        self.executor.submit(self._run_task, task_id)
                    else:
                        # 锁获取失败，释放检查占位
                        self._release_script(keys=[running_set_key], args=[task_id_check])
                else:
                    # 队列为空，释放检查占位
                    self._release_script(keys=[running_set_key], args=[task_id_check])
                    time.sleep(2)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                time.sleep(5)

    def submit_task(self, tool_name: str, target: str, params: dict, priority: int = 0, mcp_request_id: str = None) -> str:
        """提交任务到 Redis 队列（强一致性：Redis 为生命周期管控源）"""
        if not extensions.redis_client:
            raise RuntimeError("Redis unavailable. Task submission requires Redis queue.")

        task_id = str(uuid.uuid4())
        score = priority + time.time()

        try:
            # 1. 生命周期起点：必须写入 Redis 队列
            extensions.redis_client.zadd("task:queue", {task_id: score})

            # 2. 异步持久化到 MySQL
            task = Task(
                id=task_id,
                tool_name=tool_name,
                target=target,
                params=params,
                priority=priority,
                mcp_request_id=mcp_request_id,
                status=TaskStatus.PENDING
            )
            db.session.add(task)
            db.session.commit()

            logger.info(f"✅ Task {task_id} submitted & queued")
            return task_id

        except Exception as e:
            db.session.rollback()
            # 回滚：MySQL 写入失败，立即从 Redis 队列移除
            extensions.redis_client.zrem("task:queue", task_id)
            logger.error(f"❌ Task {task_id} submission failed, rolled back from Redis: {e}")
            raise

    def _reconcile_pending_tasks(self):
        """补偿机制：分批将 MySQL 中 PENDING 但 Redis 队列缺失的任务重新入队"""
        if not extensions.redis_client:
            return
        
        app = self._get_app()
        if not app:
            return

        try:
            batch_size = 50  # 每次处理 50 条，避免内存/DB 压力
            offset = 0

            with app.app_context():
                while True:
                    # 分批查询 PENDING 任务
                    pending_tasks = Task.query.filter_by(status=TaskStatus.PENDING).limit(batch_size).offset(offset).all()
                    if not pending_tasks:
                        break

                    # 获取 Redis 队列中的 task_id
                    redis_ids = set(extensions.redis_client.zrange("task:queue", 0, -1))
                    pending_ids = {t.id for t in pending_tasks}

                    # 找出孤儿任务（MySQL 有但 Redis 无）
                    missing_ids = pending_ids - redis_ids
                    if missing_ids:
                        pipeline = extensions.redis_client.pipeline()
                        for tid in missing_ids:
                            t = next((x for x in pending_tasks if x.id == tid), None)
                            score = (t.priority if t else 0) + time.time()
                            pipeline.zadd("task:queue", {tid: score})
                        pipeline.execute()
                        logger.warning(f"🔄 Reconciled {len(missing_ids)} orphan PENDING tasks to Redis queue")

                    offset += batch_size
                    # 释放会话，避免长事务
                    db.session.remove()

        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")

    def _run_task(self, task_id: str):
        """实际执行任务（在线程池中运行）"""
        app = self._get_app()
        if not app:
            logger.error(f"Cannot run task {task_id}: Flask app instance not available")
            return
            
        running_set_key = "task:running:ids"

        with app.app_context():
            try:
                task = db.session.get(Task, task_id)
                if not task:
                    return

                # 更新状态为 RUNNING
                task.status = TaskStatus.RUNNING
                task.started_at = db.func.now()
                db.session.commit()

                if extensions.redis_client:
                    extensions.redis_client.hset(f"task:{task_id}", mapping={"status": "RUNNING", "progress": "0.0"})

                # 执行工具
                result = self.executor_instance.run(task_id, task.tool_name, task.target, task.params)

                # 更新结果
                task = db.session.get(Task, task_id)
                if result.get("success"):
                    task.status = TaskStatus.SUCCESS
                    task.output_path = result.get("output_path")
                else:
                    task.status = TaskStatus.FAILED
                    task.error_message = result.get("error")

                task.completed_at = db.func.now()
                db.session.commit()

            except Exception as e:
                logger.error(f"Task {task_id} failed: {e}")
                try:
                    task = db.session.get(Task, task_id)
                    if task:
                        task.status = TaskStatus.FAILED
                        task.error_message = str(e)
                        task.completed_at = db.func.now()
                        db.session.commit()
                except: pass
            finally:
                # 清理日志队列（多任务并发支持）
                from app.services.log_service import cleanup_task
                cleanup_task(task_id)

                # 释放 Redis 锁、状态和运行集
                if extensions.redis_client:
                    extensions.redis_client.delete(f"task:{task_id}:lock")
                    extensions.redis_client.delete(f"task:{task_id}")
                    extensions.redis_client.delete(f"task:{task_id}:logs")
                    self._release_script(keys=[running_set_key], args=[task_id])

                # 释放 DB 会话，防止连接池泄漏
                db.session.remove()

    def update_max_workers(self, new_limit: int):
        """动态更新最大并发数"""
        if new_limit < 1:
            new_limit = 1
        self.max_workers = new_limit
        # 同步到 Redis，确保多 Worker 环境下配置一致
        if extensions.redis_client:
            try:
                extensions.redis_client.set('app:config:max_workers', new_limit)
            except Exception as e:
                logger.warning(f"Failed to sync max_workers to Redis: {e}")
        logger.info(f"🔄 Max concurrent tasks updated to {new_limit}")

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        app = self._get_app()
        if not app:
            return False

        with app.app_context():
            task = db.session.get(Task, task_id)
            if not task:
                return False

            # 移除队列和锁
            if extensions.redis_client:
                try:
                    extensions.redis_client.zrem("task:queue", task_id)
                    extensions.redis_client.delete(f"task:{task_id}:lock")
                except Exception as e:
                    logger.warning(f"Redis cleanup failed for {task_id}: {e}")

            if task.status == TaskStatus.RUNNING:
                # 运行中的任务标记为取消
                task.status = TaskStatus.CANCELLED
                task.completed_at = db.func.now()
                logger.info(f"Task {task_id} marked as cancelled.")
            else:
                # 物理删除非运行中任务
                TaskLog.query.filter_by(task_id=task_id).delete()
                db.session.delete(task)
                logger.info(f"Task {task_id} deleted.")

            db.session.commit()
            return True

    def update_task(self, task_id: str, params: dict) -> bool:
        """更新任务参数（仅支持 PENDING 状态）"""
        app = self._get_app()
        if not app:
            return False

        with app.app_context():
            task = db.session.get(Task, task_id)
            if not task:
                return False

            if task.status != TaskStatus.PENDING:
                return False

            # 合并参数
            if task.params:
                task.params.update(params)
            else:
                task.params = params

            db.session.commit()
            return True

# 全局单例
task_manager = TaskManager()

def cleanup_stuck_tasks():
    """清理卡住的任务（用于手动执行）"""
    from datetime import datetime, timedelta
    try:
        app = db.get_app()
    except RuntimeError:
        logger.error("Cannot cleanup stuck tasks: Flask app instance not available")
        return 0

    with app.app_context():
        one_hour_ago = datetime.now() - timedelta(hours=1)
        stuck_tasks = Task.query.filter(
            Task.status == TaskStatus.RUNNING,
            Task.started_at < one_hour_ago
        ).all()

        cleaned = 0
        for task in stuck_tasks:
            logger.warning(f"🧹 Cleaning up stuck task {task.id}")
            task.status = TaskStatus.TIMEOUT
            task.error_message = "Task cleaned up by cleanup_stuck_tasks()"
            task.completed_at = db.func.now()

            if extensions.redis_client:
                try:
                    extensions.redis_client.delete(f"task:{task.id}:lock")
                    extensions.redis_client.delete(f"task:{task.id}")
                    extensions.redis_client.delete(f"task:{task.id}:logs")
                except: pass

            cleaned += 1

        db.session.commit()

        if cleaned > 0:
            logger.info(f"✅ Cleaned up {cleaned} stuck tasks")
        else:
            logger.info("ℹ️ No stuck tasks found")

        return cleaned
