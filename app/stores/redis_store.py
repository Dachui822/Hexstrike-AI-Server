import json
import time
from typing import Dict, Any, List, Optional
from app.stores.base import TaskStore
from app.extensions import logger
import app.extensions as extensions

class RedisTaskStore(TaskStore):
    def create_task(self, task_id: str, data: Dict[str, Any]) -> bool:
        try:
            if not extensions.redis_client:
                return False
            data["created_at"] = time.time()
            data["status"] = "PENDING"
            data["progress"] = 0.0
            extensions.redis_client.hset(f"task:{task_id}", mapping={k: str(v) for k, v in data.items()})
            extensions.redis_client.zadd("task:queue", {task_id: time.time()})
            logger.info(f"Task {task_id} created in Redis")
            return True
        except Exception as e:
            logger.error(f"Failed to create task in Redis: {e}")
            return False

    def update_status(self, task_id: str, status: str, progress: float = 0.0, message: str = "") -> bool:
        try:
            if not extensions.redis_client:
                return False
            pipe = extensions.redis_client.pipeline()
            pipe.hset(f"task:{task_id}", mapping={"status": status, "progress": str(progress)})
            if message:
                pipe.lpush(f"task:{task_id}:logs", message)
                pipe.ltrim(f"task:{task_id}:logs", 0, 999)
            pipe.execute()
            extensions.redis_client.publish("hexstrike:progress", json.dumps({"task_id": task_id, "status": status, "progress": progress}))
            return True
        except Exception as e:
            logger.error(f"Failed to update task {task_id}: {e}")
            return False

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        try:
            if not extensions.redis_client:
                return None
            data = extensions.redis_client.hgetall(f"task:{task_id}")
            if not data:
                return None
            data["progress"] = float(data.get("progress", 0))
            return data
        except Exception as e:
            logger.error(f"Failed to get task {task_id}: {e}")
            return None

    def list_tasks(self, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        try:
            if not extensions.redis_client:
                return []
            task_ids = extensions.redis_client.zrevrange("task:queue", offset, offset + limit - 1)
            tasks = []
            for tid in task_ids:
                t = self.get_task(tid)
                if t and (status is None or t.get("status") == status):
                    tasks.append(t)
            return tasks
        except Exception as e:
            logger.error(f"Failed to list tasks: {e}")
            return []

    def cancel_task(self, task_id: str) -> bool:
        return self.update_status(task_id, "CANCELLED")
