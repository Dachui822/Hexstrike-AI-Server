from flask import Blueprint, request, jsonify
from app.services.task_manager import task_manager
from app.extensions import db
from app.models.task import Task

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
            "status": t.status.value, "progress": t.progress,
            "created_at": t.created_at.isoformat()
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
        "status": task.status.value,
        "progress": task.progress,
        "params": task.params,
        "error": task.error_message,
        "created_at": task.created_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None
    })
