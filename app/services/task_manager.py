import json
import uuid
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from app.extensions import db, redis_client
from app.models.task import Task, TaskStatus, TaskLog
from app.services.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

class TaskManager:
    def __init__(self, max_workers=10):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.running_tasks = {}  # task_id -> Future object
        self.executor_instance = ToolExecutor()

    def submit_task(self, tool_name: str, target: str, params: dict, priority: int = 0, mcp_request_id: str = None) -> str:
        """提交任务到任务池"""
        task_id = str(uuid.uuid4())
        
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
        redis_client.zadd("task:queue", {task_id: score})
        
        # 3. 触发调度器
        self._dispatch()
        
        logger.info(f"Task {task_id} submitted for {tool_name} on {target}")
        return task_id

    def _dispatch(self):
        """调度器：从队列获取任务并提交给线程池"""
        if len(self.running_tasks) >= self.executor._max_workers:
            return

        tasks = redis_client.zrangebyscore("task:queue", "-inf", "+inf", start=0, num=1)
        if not tasks:
            return

        task_id = tasks[0]
        
        if redis_client.set(f"task:{task_id}:lock", "1", nx=True, ex=60):
            self._execute_task(task_id)

    def _execute_task(self, task_id: str):
        """实际执行任务"""
        from app import create_app
        app = create_app()
        
        with app.app_context():
            task = db.session.get(Task, task_id)
            if not task:
                return

            task.status = TaskStatus.RUNNING
            task.started_at = db.func.now()
            db.session.commit()
            
            redis_client.hset(f"task:{task_id}", mapping={"status": "RUNNING", "progress": "0.0"})
            redis_client.zrem("task:queue", task_id)

            future = self.executor.submit(
                self.executor_instance.run, 
                task_id, 
                task.tool_name, 
                task.target, 
                task.params
            )
            self.running_tasks[task_id] = future
            future.add_done_callback(lambda f: self._on_task_complete(task_id, f))

    def _on_task_complete(self, task_id: str, future):
        """任务完成回调"""
        from app import create_app
        app = create_app()
        
        with app.app_context():
            task = db.session.get(Task, task_id)
            try:
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
            
            redis_client.delete(f"task:{task_id}:lock")
            self.running_tasks.pop(task_id, None)
            self._dispatch()
            
            logger.info(f"Task {task_id} completed with status {task.status}")

task_manager = TaskManager()
