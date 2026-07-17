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
_DEFAULT_MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 3))

# Redis Lua 脚本：原子性地检查并增加运行计数（全局信号量）
LUA_ACQUIRE = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local current = tonumber(redis.call('GET', key) or '0')
if current < limit then
    redis.call('INCR', key)
    return 1
end
return 0
"""

LUA_RELEASE = """
local key = KEYS[1]
local current = tonumber(redis.call('GET', key) or '0')
if current > 0 then
    redis.call('DECR', key)
end
return 1
"""

class TaskManager:
    def __init__(self):
        self.max_workers = _DEFAULT_MAX_WORKERS
        self.executor = ThreadPoolExecutor(max_workers=100)
        self.executor_instance = ToolExecutor()
        
        self._scheduler_running = False
        self._scheduler_thread = None
        
        # 注册 Lua 脚本
        if extensions.redis_client:
            self._acquire_script = extensions.redis_client.register_script(LUA_ACQUIRE)
            self._release_script = extensions.redis_client.register_script(LUA_RELEASE)
            
        # 启动后台调度器
        self._start_scheduler()

    def _start_scheduler(self):
        """启动后台调度线程"""
        if self._scheduler_running:
            return
        self._scheduler_running = True
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()
        logger.info("🔄 Task Scheduler started")

    def _scheduler_loop(self):
        """后台调度循环：从 Redis 队列拉取任务"""
        while self._scheduler_running:
            try:
                if not extensions.redis_client:
                    time.sleep(5)
                    continue

                # 动态注册 Lua 脚本（解决 Flask 启动顺序导致 __init__ 中 Redis 未初始化而缺失属性的问题）
                if not hasattr(self, '_acquire_script'):
                    self._acquire_script = extensions.redis_client.register_script(LUA_ACQUIRE)
                    self._release_script = extensions.redis_client.register_script(LUA_RELEASE)

                # 1. 检查全局并发限制
                can_run = self._acquire_script(keys=['task:global:running'], args=[self.max_workers])
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
                        # 提交到线程池
                        self.executor.submit(self._run_task, task_id)
                    else:
                        # 锁获取失败，释放计数并跳过
                        self._release_script(keys=['task:global:running'], args=[])
                else:
                    # 队列为空，释放计数
                    self._release_script(keys=['task:global:running'], args=[])
                    time.sleep(2)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                time.sleep(5)

    def submit_task(self, tool_name: str, target: str, params: dict, priority: int = 0, mcp_request_id: str = None) -> str:
        """提交任务到 Redis 队列"""
        task_id = str(uuid.uuid4())
        
        # 1. 写入数据库
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

        # 2. 写入 Redis 队列
        if extensions.redis_client:
            score = priority + time.time()
            extensions.redis_client.zadd("task:queue", {task_id: score})
            
        logger.info(f"Task {task_id} submitted for {tool_name} on {target}")
        return task_id

    def _run_task(self, task_id: str):
        """实际执行任务（在线程池中运行）"""
        from flask import current_app
        app = current_app._get_current_object()

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
                
                # 释放 Redis 锁和全局计数
                if extensions.redis_client:
                    extensions.redis_client.delete(f"task:{task_id}:lock")
                    extensions.redis_client.delete(f"task:{task_id}")
                    extensions.redis_client.delete(f"task:{task_id}:logs")
                    self._release_script(keys=['task:global:running'], args=[])

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
        from flask import current_app
        app = current_app._get_current_object()

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
        from flask import current_app
        app = current_app._get_current_object()

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
    from flask import current_app

    app = current_app._get_current_object()
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
