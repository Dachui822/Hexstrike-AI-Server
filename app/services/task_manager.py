import json
import uuid
import time
import os
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from app.extensions import db
import app.extensions as extensions
from app.models.task import Task, TaskStatus, TaskLog
from app.services.tool_executor import ToolExecutor
from app.config import Config

logger = logging.getLogger(__name__)

# 从环境变量读取默认并发数，与 config.py 保持一致
_DEFAULT_MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 3))

class TaskManager:
    def __init__(self, max_workers=_DEFAULT_MAX_WORKERS):
        # 尝试从 Redis 恢复配置，确保多 Worker 环境下保持一致
        if extensions.redis_client:
            try:
                val = extensions.redis_client.get('app:config:max_workers')
                if val:
                    max_workers = int(val)
            except Exception:
                pass
        self.max_workers = max_workers  # 动态并发限制
        # 线程池保持足够大，实际并发由 max_workers 控制
        self.executor = ThreadPoolExecutor(max_workers=100)
        self.running_tasks = {}  # task_id -> Future object
        self.executor_instance = ToolExecutor()

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
        # 触发调度，如果新限制允许，可能启动等待中的任务
        self._dispatch()

    def submit_task(self, tool_name: str, target: str, params: dict, priority: int = 0, mcp_request_id: str = None) -> str:
        """提交任务到任务池"""
        task_id = str(uuid.uuid4())

        # 优化 3 & 4: 检查队列长度 (快速失败 & 监控)
        if extensions.redis_client:
            queue_length = extensions.redis_client.zcard("task:queue")
            if queue_length >= 1000:
                raise Exception("Task queue is full (1000 tasks), please try again later")
            elif queue_length >= 50:
                logger.warning(f"⚠️ Task queue length is high: {queue_length}, consider increasing MAX_WORKERS")

        # 调试日志
        logger.info(f"[DEBUG] submit_task: tool_name={tool_name}, target={repr(target)}, params={params}")

        # 1. 写入 MySQL
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
        
        # 2. 写入 Redis 队列 (ZSET 支持优先级)
        score = priority + time.time()
        if extensions.redis_client:
            extensions.redis_client.zadd("task:queue", {task_id: score})
        
        # 3. 触发调度器
        self._dispatch()
        
        logger.info(f"Task {task_id} submitted for {tool_name} on {target}")
        return task_id

    def _dispatch(self):
        """调度器：从队列获取任务并提交给线程池"""
        # 检查当前运行任务数是否达到限制
        if len(self.running_tasks) >= self.max_workers:
            return

        if not extensions.redis_client:
            return

        tasks = extensions.redis_client.zrangebyscore("task:queue", "-inf", "+inf", start=0, num=1)
        if not tasks:
            return

        task_id = tasks[0]

        if extensions.redis_client.set(f"task:{task_id}:lock", "1", nx=True, ex=60):
            self._execute_task(task_id)

    def _execute_task(self, task_id: str):
        """实际执行任务"""
        from app import create_app
        app = create_app()

        with app.app_context():
            task = db.session.get(Task, task_id)
            if not task:
                return
            
            # 调试日志
            logger.info(f"[DEBUG] _execute_task: task_id={task_id}, tool_name={task.tool_name}, target={repr(task.target)}, params={task.params}")

            task.status = TaskStatus.RUNNING
            task.started_at = db.func.now()
            db.session.commit()

            if extensions.redis_client:
                extensions.redis_client.hset(f"task:{task_id}", mapping={"status": "RUNNING", "progress": "0.0"})
                extensions.redis_client.zrem("task:queue", task_id)

            future = self.executor.submit(
                self.executor_instance.run,
                task_id,
                task.tool_name,
                task.target,
                task.params
            )
            self.running_tasks[task_id] = future
            future.add_done_callback(lambda f: self._on_task_complete(task_id, f))

            # 设置超时保护 (如果任务超过 TASK_TIMEOUT 秒未完成，强制标记为超时)
            timeout = Config.TASK_TIMEOUT
            threading.Thread(target=self._watchdog, args=(task_id, future, timeout), daemon=True).start()

    def _watchdog(self, task_id: str, future, timeout: int):
        """看门狗：监控任务超时"""
        try:
            future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.error(f"⏰ Task {task_id} timed out after {timeout} seconds")
            # 超时后强制更新状态
            with self._app.app_context():
                task = db.session.get(Task, task_id)
                if task and task.status == TaskStatus.RUNNING:
                    task.status = TaskStatus.TIMEOUT
                    task.error_message = f"Task timed out after {timeout} seconds"
                    task.completed_at = db.func.now()
                    db.session.commit()
                    
                    if extensions.redis_client:
                        extensions.redis_client.delete(f"task:{task_id}:lock")
                        extensions.redis_client.delete(f"task:{task_id}")
                        extensions.redis_client.delete(f"task:{task_id}:logs")
                    
                    self.running_tasks.pop(task_id, None)
                    logger.info(f"⏰ Task {task_id} marked as TIMEOUT")
        except Exception as e:
            logger.warning(f"Watchdog error for {task_id}: {e}")

    def _app_context(self):
        """创建应用上下文"""
        from app import create_app
        app = create_app()
        return app.app_context()

    def _on_task_complete(self, task_id: str, future):
        """任务完成回调"""
        from app import create_app
        app = create_app()

        with app.app_context():
            task = db.session.get(Task, task_id)
            if not task:
                return

            try:
                # 检查是否被取消
                if task.status == TaskStatus.CANCELLED:
                    logger.info(f"Task {task_id} was cancelled.")
                    return

                result = future.result()
                if result.get("success"):
                    task.status = TaskStatus.SUCCESS
                    task.progress = 100.0
                    task.output_path = result.get("output_path")
                else:
                    task.status = TaskStatus.FAILED
                    task.error_message = result.get("error")
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error_message = str(e)

            task.completed_at = db.func.now()
            db.session.commit()

            if extensions.redis_client:
                extensions.redis_client.delete(f"task:{task_id}:lock")
                extensions.redis_client.delete(f"task:{task_id}")          # 清理状态 Hash
                extensions.redis_client.delete(f"task:{task_id}:logs")     # 清理日志 List
            self.running_tasks.pop(task_id, None)
            self._dispatch()

            logger.info(f"Task {task_id} completed with status {task.status}")

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        from app import create_app
        app = create_app()

        with app.app_context():
            task = db.session.get(Task, task_id)
            if not task:
                return False

            # 移除队列和锁 (兼容 Redis 未启动的情况)
            if extensions.redis_client:
                try:
                    extensions.redis_client.zrem("task:queue", task_id)
                    extensions.redis_client.delete(f"task:{task_id}:lock")
                except Exception as e:
                    logger.warning(f"Redis cleanup failed for {task_id}: {e}")

            if task.status == TaskStatus.RUNNING:
                # 运行中的任务标记为取消，不物理删除
                task.status = TaskStatus.CANCELLED
                task.completed_at = db.func.now()
                logger.info(f"Task {task_id} marked as cancelled.")
            else:
                # 先删除关联的日志
                TaskLog.query.filter_by(task_id=task_id).delete()
                # 物理删除非运行中任务
                db.session.delete(task)
                logger.info(f"Task {task_id} deleted.")

            db.session.commit()
            return True

    def update_task(self, task_id: str, params: dict) -> bool:
        """更新任务参数（仅支持 PENDING 状态）"""
        from app import create_app
        app = create_app()
        
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

task_manager = TaskManager()

def cleanup_stuck_tasks():
    """清理卡住的任务（用于手动执行）"""
    from datetime import datetime, timedelta
    
    with task_manager._app_context():
        # 查找超过 1 小时仍然是 RUNNING 的任务
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
                extensions.redis_client.delete(f"task:{task.id}:lock")
                extensions.redis_client.delete(f"task:{task.id}")
                extensions.redis_client.delete(f"task:{task.id}:logs")
            
            cleaned += 1
        
        db.session.commit()
        
        if cleaned > 0:
            logger.info(f"✅ Cleaned up {cleaned} stuck tasks")
        else:
            logger.info("ℹ️ No stuck tasks found")
        
        return cleaned
