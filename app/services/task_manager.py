"""
任务管理器 - 基于 Celery 的异步任务提交
架构说明：
- 不再包含调度器（由 Celery Worker 负责）
- 仅负责任务提交和状态管理
- 与 Celery Worker 完全解耦
"""

import uuid
import time
import logging
from typing import Dict, Any, Optional
from app.extensions import db
import app.extensions as extensions
from app.models.task import Task, TaskStatus, TaskLog
from app.tasks.worker_tasks import execute_tool_task

logger = logging.getLogger(__name__)

# ============================================================================
# Task Manager - 仅负责任务提交
# ============================================================================

class TaskManager:
    """
    任务管理器（Celery 架构）
    
    职责：
    - 提交任务到 Celery 队列
    - 任务状态查询
    - 任务取消
    
    不再负责：
    - 任务调度（由 Celery Worker 负责）
    - 任务执行（由 Celery Worker 负责）
    - 并发控制（由 Celery Worker 负责）
    """
    
    def __init__(self):
        # 不再包含线程池和调度器
        pass
    
    def submit_task(
        self,
        tool_name: str,
        target: str,
        params: dict,
        priority: int = 0,
        mcp_request_id: str = None
    ) -> str:
        """
        提交任务到 Celery 队列
        
        Args:
            tool_name: 工具名称
            target: 目标地址
            params: 工具参数
            priority: 优先级（0-10，越高越优先）
            mcp_request_id: MCP 请求 ID
        
        Returns:
            task_id: 任务 ID
        """
        task_id = str(uuid.uuid4())
        
        # 1. 写入数据库（持久化）
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
        
        logger.info(f"📝 Task {task_id} created in database: {tool_name} on {target}")
        
        # 2. 提交到 Celery 队列
        try:
            # 根据优先级选择队列
            if priority >= 8:
                queue = 'hexstrike_high_priority'
            elif priority <= 2:
                queue = 'hexstrike_low_priority'
            else:
                queue = 'hexstrike_default'
            
            # 异步提交任务
            execute_tool_task.apply_async(
                args=[task_id, tool_name, target, params],
                queue=queue,
                priority=priority,
                task_id=task_id
            )
            
            logger.info(f"✅ Task {task_id} submitted to Celery queue '{queue}'")
            
        except Exception as e:
            logger.error(f"❌ Failed to submit task {task_id} to Celery: {e}")
            # Celery 提交失败，回滚任务状态
            task.status = TaskStatus.FAILED
            task.error_message = f"Failed to submit to queue: {e}"
            db.session.commit()
            raise
        
        return task_id
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        task = Task.query.get(task_id)
        if not task:
            return None
        
        return {
            "id": task.id,
            "tool_name": task.tool_name,
            "target": task.target,
            "status": task.status.value,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "error_message": task.error_message,
            "output_path": task.output_path
        }
    
    def cancel_task(self, task_id: str) -> bool:
        """
        取消任务
        
        注意：Celery 任务的取消通过设置数据库标志实现，
        Worker 会定期检查该标志并终止执行。
        """
        task = Task.query.get(task_id)
        if not task:
            return False
        
        # 仅运行中的任务可取消
        if task.status != TaskStatus.RUNNING:
            return False
        
        # 设置取消标志
        task.status = TaskStatus.CANCELLED
        task.completed_at = db.func.now()
        db.session.commit()
        
        # 设置 Redis 取消标志（供 Worker 检查）
        if extensions.redis_client:
            try:
                extensions.redis_client.set(f"task:{task_id}:cancel", "1", ex=300)
                logger.info(f"🛑 Cancel signal set for task {task_id}")
            except Exception as e:
                logger.warning(f"Failed to set cancel signal: {e}")
        
        logger.info(f"✅ Task {task_id} cancelled")
        return True
    
    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        task = Task.query.get(task_id)
        if not task:
            return False
        
        # 运行中的任务先取消
        if task.status == TaskStatus.RUNNING:
            self.cancel_task(task_id)
        
        # 删除日志
        TaskLog.query.filter_by(task_id=task_id).delete()
        
        # 删除任务
        db.session.delete(task)
        db.session.commit()
        
        logger.info(f"✅ Task {task_id} deleted")
        return True
    
    def update_task(self, task_id: str, params: dict) -> bool:
        """更新任务参数（仅支持 PENDING 状态）"""
        task = Task.query.get(task_id)
        if not task or task.status != TaskStatus.PENDING:
            return False
        
        if task.params:
            task.params.update(params)
        else:
            task.params = params
        
        db.session.commit()
        logger.info(f"✅ Task {task_id} params updated")
        return True
    
    def update_max_workers(self, new_limit: int):
        """
        更新最大并发数（Celery 配置）
        注意：此方法仅更新配置，实际并发数由 Celery Worker 控制
        """
        logger.warning(
            "update_max_workers is deprecated in Celery architecture. "
            "Use WORKER_CONCURRENCY environment variable instead."
        )


# ============================================================================
# 全局单例
# ============================================================================

task_manager = TaskManager()


# ============================================================================
# 辅助函数
# ============================================================================

def cleanup_stuck_tasks():
    """清理卡住的任务（状态为 RUNNING 超过 1 小时）"""
    from datetime import datetime, timedelta
    
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
        cleaned += 1
    
    db.session.commit()
    
    if cleaned > 0:
        logger.info(f"✅ Cleaned up {cleaned} stuck tasks")
    else:
        logger.info("ℹ️ No stuck tasks found")
    
    return cleaned
