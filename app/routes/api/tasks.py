from flask import Blueprint, request, jsonify
from app.services.task_manager import task_manager, cleanup_stuck_tasks
from app.services.tool_executor import ToolExecutor
from app.extensions import db
import app.extensions as extensions
from app.models.task import Task, TaskStatus
import logging

logger = logging.getLogger(__name__)

bp = Blueprint('tasks', __name__)

@bp.route('/', methods=['POST'])
def create_task():
    data = request.json
    tool = data.get('tool')
    target = data.get('target')
    params = data.get('params', {})
    priority = data.get('priority', 0)

    if not tool or not target:
        return jsonify({"error": "tool and target are required"}), 400

    task_id = task_manager.submit_task(tool, target, params, priority)
    return jsonify({"task_id": task_id, "status": "PENDING"}), 202

@bp.route('/', methods=['GET'])
@bp.route('', methods=['GET'])
def list_tasks():
    status_filter = request.args.get('status')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = Task.query.order_by(Task.created_at.desc())
    if status_filter:
        query = query.filter(Task.status == status_filter)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "total": pagination.total,
        "pages": pagination.pages,
        "tasks": [{
            "id": t.id, "tool": t.tool_name, "target": t.target,
            "status": t.status.value.lower() if t.status else "unknown",
            "created_at": t.created_at.isoformat(),
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None
        } for t in pagination.items]
    })

@bp.route('/<task_id>', methods=['GET'])
def get_task(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    return jsonify({
        "id": task.id,
        "tool": task.tool_name,
        "target": task.target,
        "status": task.status.value.lower() if task.status else "unknown",
        "params": task.params,
        "error": task.error_message,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None
    })

@bp.route('/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    """删除任务"""
    try:
        success = task_manager.delete_task(task_id)
        if success:
            return jsonify({"success": True, "message": "Task deleted or cancelled"})
        return jsonify({"success": False, "error": "Task not found or cannot be deleted"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/<task_id>', methods=['PUT'])
def update_task(task_id):
    """更新任务参数"""
    data = request.json
    params = data.get('params', {})

    success = task_manager.update_task(task_id, params)
    if success:
        return jsonify({"success": True, "message": "Task updated"})
    return jsonify({"success": False, "error": "Task not found or cannot be updated (must be PENDING)"}), 400

@bp.route('/config', methods=['GET'])
def get_config():
    """获取任务调度配置（Celery 架构）"""
    import os

    # Celery 架构中，并发数由 WORKER_CONCURRENCY 环境变量控制
    max_workers = int(os.environ.get("WORKER_CONCURRENCY", 10))
    
    # 从数据库统计全局运行中任务数（跨 Worker 准确）
    running_count = Task.query.filter(Task.status == TaskStatus.RUNNING).count()

    return jsonify({
        "max_workers": max_workers,
        "running_tasks": running_count,
        "architecture": "celery",
        "note": "Concurrency is controlled by WORKER_CONCURRENCY environment variable"
    })

@bp.route('/config', methods=['PUT'])
def update_config():
    """更新任务调度配置（Celery 架构）"""
    data = request.json
    new_limit = data.get('max_workers')

    if new_limit is None or not isinstance(new_limit, int) or new_limit < 1:
        return jsonify({"error": "Invalid max_workers value"}), 400

    # Celery 架构中，需要重启 Worker 才能生效
    return jsonify({
        "success": True,
        "max_workers": new_limit,
        "architecture": "celery",
        "note": "To apply this change, restart the Celery Worker with the new WORKER_CONCURRENCY environment variable",
        "command": f"export WORKER_CONCURRENCY={new_limit} && sudo systemctl restart hexstrike-worker"
    })

@bp.route('/cleanup', methods=['POST'])
def cleanup_stuck():
    """清理卡住的任务（状态为 RUNNING 超过 1 小时）"""
    try:
        cleaned = cleanup_stuck_tasks()
        return jsonify({
            "success": True,
            "cleaned_count": cleaned
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/<task_id>/cancel', methods=['POST'])
def cancel_task(task_id):
    """
    取消任务（支持 PENDING 和 RUNNING 状态）
    """
    try:
        task = db.session.get(Task, task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        # 支持 PENDING 和 RUNNING 状态
        if task.status not in [TaskStatus.PENDING, TaskStatus.RUNNING]:
            return jsonify({
                "error": f"Cannot cancel task in {task.status.value} state"
            }), 400

        logger.info(f" Cancelling task {task_id} (status: {task.status.value})")

        # PENDING: 从队列移除
        if task.status == TaskStatus.PENDING:
            try:
                if extensions.redis_client:
                    extensions.redis_client.zrem("task:queue", task_id)
                    logger.info(f" Removed task {task_id} from Redis queue")
            except Exception as redis_err:
                logger.warning(f"Failed to remove task from Redis queue: {redis_err}")
                # 继续执行，不影响取消流程

            task.status = TaskStatus.CANCELLED
            task.completed_at = db.func.now()
            task.error_message = "Cancelled by user (PENDING)"
            db.session.commit()

            return jsonify({
                "success": True,
                "message": "Task cancelled (was PENDING)"
            })

        # RUNNING: 多重取消
        if task.status == TaskStatus.RUNNING:
            # 1. Redis 取消标志（带重试）
            if extensions.redis_client:
                try:
                    extensions.redis_client.setex(f"task:{task_id}:cancel", 300, "1")
                    logger.info(f" Cancel signal set for {task_id}")
                except Exception as redis_err:
                    logger.warning(f"Failed to set Redis cancel flag: {redis_err}")
                    # 继续执行 Celery revoke

            # 2. Celery revoke
            try:
                from app.celery_app import celery
                celery.control.revoke(task_id, terminate=True, signal='SIGTERM')
                logger.info(f" Celery revoke sent for {task_id}")
            except Exception as celery_err:
                logger.warning(f"Celery revoke failed: {celery_err}")

            # 3. 更新状态
            task.status = TaskStatus.CANCELLED
            task.error_message = "Cancelling..."
            db.session.commit()
            
            return jsonify({
                "success": True,
                "message": "Cancel signal sent. Task will stop shortly."
            })

    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/active', methods=['GET'])
def get_active_tasks():
    """获取所有活跃任务"""
    try:
        from app.services.tool_executor import ToolExecutor
        active = ToolExecutor.get_active_tasks()
        return jsonify({"active_tasks": active})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route('/log-queue/stats', methods=['GET'])
def get_log_queue_stats():
    """获取日志队列统计信息"""
    try:
        from app.services.log_service import get_queue_stats
        stats = get_queue_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
