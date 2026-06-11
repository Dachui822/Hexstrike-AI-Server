from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.tool import Tool
from app.models.task import Task, TaskStatus
from app.services.tool_registry import ToolRegistry
from app.services.task_manager import task_manager
import logging

logger = logging.getLogger(__name__)
bp = Blueprint('tools', __name__)

@bp.route('/', methods=['GET'])
def list_tools():
    """获取工具列表 (从内存缓存读取，支持动态更新)"""
    try:
        tools = ToolRegistry.get_live_tools_status()
        return jsonify(tools)
    except Exception as e:
        logger.error(f"Failed to get tools status: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/<tool_name>/health', methods=['POST'])
def trigger_health_check(tool_name):
    result = ToolRegistry.check_health(tool_name)
    return jsonify(result)

@bp.route('/<tool_name>', methods=['PUT'])
def update_tool(tool_name):
    """更新工具配置（支持修改健康检测命令）"""
    data = request.get_json()
    tool = db.session.get(Tool, tool_name)
    if not tool:
        return jsonify({"error": "Tool not found"}), 404
    
    if 'health_check_cmd' in data:
        tool.health_check_cmd = data['health_check_cmd']
    
    db.session.commit()
    return jsonify({
        "success": True,
        "health_check_cmd": tool.health_check_cmd
    })

@bp.route('/<tool_name>', methods=['POST'])
def execute_tool(tool_name):
    """执行工具扫描 (通过任务池)"""
    data = request.get_json() or {}
    target = data.get('target') or data.get('url')
    if not target:
        return jsonify({"error": "target or url is required"}), 400

    # 使用任务管理器提交任务
    try:
        task_id = task_manager.submit_task(
            tool_name=tool_name,
            target=target,
            params=data,
            priority=data.get('priority', 0)
        )
        return jsonify({
            "task_id": task_id,
            "status": "queued",
            "tool": tool_name,
            "target": target
        }), 202
    except Exception as e:
        logger.error(f"Failed to submit task: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/health/check-all', methods=['POST'])
def check_all_tools():
    """批量检查所有工具健康状态"""
    result = ToolRegistry.check_all_health()
    return jsonify(result)

@bp.route('/health/auto-status', methods=['GET'])
def get_auto_check_status():
    """获取自动健康检测状态"""
    status = ToolRegistry.get_auto_check_status()
    return jsonify(status)

@bp.route('/health/auto-start', methods=['POST'])
def start_auto_check():
    """启动自动健康检测"""
    success = ToolRegistry.start_auto_health_check()
    return jsonify({
        "success": success,
        "message": "Auto health check started" if success else "Failed to start auto health check"
    })

@bp.route('/health/auto-stop', methods=['POST'])
def stop_auto_check():
    """停止自动健康检测"""
    success = ToolRegistry.stop_auto_health_check()
    return jsonify({
        "success": success,
        "message": "Auto health check stopped" if success else "Failed to stop auto health check"
    })
