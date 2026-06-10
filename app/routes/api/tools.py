from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models.tool import Tool
from app.services.tool_registry import ToolRegistry

bp = Blueprint('tools', __name__)

@bp.route('/', methods=['GET'])
def list_tools():
    category = request.args.get('category')
    query = Tool.query
    if category:
        query = query.filter(Tool.category == category)
    tools = query.all()
    return jsonify([{
        "name": t.name, "display_name": t.display_name,
        "category": t.category, "available": t.is_available,
        "version": t.installed_version
    } for t in tools])

@bp.route('/<tool_name>/health', methods=['POST'])
def trigger_health_check(tool_name):
    result = ToolRegistry.check_health(tool_name)
    return jsonify(result)

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
