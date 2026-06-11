from flask import Blueprint, jsonify, request
from app.models.task import Task
from app.extensions import db
import os
import logging

logger = logging.getLogger(__name__)

bp = Blueprint('logs', __name__)

LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'hexstrike.log')

def _read_task_output(task):
    """读取任务输出内容"""
    if task.output_path and os.path.exists(task.output_path):
        try:
            with open(task.output_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()[:2000]  # 限制长度
        except Exception:
            return "Failed to read output file."
    elif task.error_message:
        return task.error_message
    return "No output available."

@bp.route('/system', methods=['GET'])
def get_system_logs():
    """获取系统日志 (读取 hexstrike.log 文件)"""
    limit = request.args.get('limit', 500, type=int)
    
    if not os.path.exists(LOG_FILE_PATH):
        return jsonify({"logs": [], "message": "日志文件不存在"})

    try:
        with open(LOG_FILE_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            # 读取最后 N 行
            lines = f.readlines()
            recent_logs = lines[-limit:] if len(lines) > limit else lines
            
            # 清理换行符
            cleaned_logs = [line.strip() for line in recent_logs if line.strip()]
            
        return jsonify({
            "success": True,
            "logs": cleaned_logs,
            "total_lines": len(lines),
            "returned_lines": len(cleaned_logs)
        })
    except Exception as e:
        logger.error(f"Failed to read system logs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/tasks', methods=['GET'])
def get_task_logs():
    """获取任务/工具执行日志"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    tool_name = request.args.get('tool')
    status = request.args.get('status')

    query = Task.query.order_by(Task.created_at.desc())

    if tool_name:
        query = query.filter(Task.tool_name == tool_name)
    if status:
        query = query.filter(Task.status == status)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    tasks = [{
        "id": t.id,
        "task_id": t.id,
        "tool": t.tool_name,
        "target": t.target,
        "status": t.status.value if t.status else "unknown",
        "output": _read_task_output(t),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "duration": (t.completed_at - t.started_at).total_seconds() if t.completed_at and t.started_at else None
    } for t in pagination.items]

    return jsonify({
        "success": True,
        "tasks": tasks,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages
        }
    })
