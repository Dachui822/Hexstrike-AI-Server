from flask import Blueprint, request, jsonify
from app.services.task_manager import task_manager, cleanup_stuck_tasks
from app.services.tool_executor import ToolExecutor
from app.extensions import db
import app.extensions as extensions
from app.models.task import Task, TaskStatus

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
    """获取任务调度配置"""
    import app.extensions as extensions
    
    max_workers = task_manager.max_workers
    if extensions.redis_client:
        try:
            val = extensions.redis_client.get('app:config:max_workers')
            if val:
                max_workers = int(val)
                task_manager.max_workers = max_workers
        except Exception:
            pass
            
    # 从数据库统计全局运行中任务数（跨 Worker 准确）
    running_count = Task.query.filter(Task.status == TaskStatus.RUNNING).count()
            
    return jsonify({
        "max_workers": max_workers,
        "running_tasks": running_count
    })

@bp.route('/config', methods=['PUT'])
def update_config():
    """更新任务调度配置"""
    data = request.json
    new_limit = data.get('max_workers')

    if new_limit is None or not isinstance(new_limit, int) or new_limit < 1:
        return jsonify({"error": "Invalid max_workers value"}), 400

    task_manager.update_max_workers(new_limit)
    return jsonify({
        "success": True,
        "max_workers": task_manager.max_workers
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
    """取消运行中的任务（通过 Redis 设置取消标志）"""
    try:
        # 检查任务是否存在
        task = db.session.get(Task, task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        if task.status != TaskStatus.RUNNING:
            return jsonify({"error": f"Task is not running (status: {task.status.value})"}), 400

        # 设置 Redis 取消标志（任务执行时会检查）
        if extensions.redis_client:
            extensions.redis_client.set(f"task:{task_id}:cancel", "1")
            logger.info(f"🛑 Cancel signal set for task {task_id} in Redis")
        else:
            logger.warning("Redis not available, cancel signal not set")

        # 立即更新数据库状态（可选：也可以等任务自己退出后再更新）
        task.status = TaskStatus.CANCELLED
        task.completed_at = db.func.now()
        db.session.commit()

        return jsonify({
            "success": True, 
            "message": "Cancel signal sent. Task will stop shortly."
        })
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {e}")
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
