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
    """获取工具列表 (从数据库读取持久化数据，状态字段使用缓存)"""
    try:
        # 从数据库读取所有工具的持久化数据
        tools = Tool.query.all()
        if not tools:
            logger.warning("⚠️ No tools found in database, initializing...")
            ToolRegistry.init_tools()
            tools = Tool.query.all()
            if not tools:
                return jsonify([])
        
        # 获取内存中的健康状态缓存
        health_cache = ToolRegistry._live_status_cache
        
        result = []
        for tool in tools:
            # 优先使用缓存的健康状态，否则使用数据库中的状态
            cached = health_cache.get(tool.name, {})
            result.append({
                "name": tool.name,
                "display_name": tool.display_name,
                "category": tool.category,
                "description": tool.description,
                "available": cached.get("available", tool.is_available),  # 状态优先用缓存
                "version": cached.get("version", tool.installed_version),  # 版本优先用缓存
                "last_check": cached.get("last_check", tool.last_health_check.isoformat() if tool.last_health_check else None),
                "health_check_cmd": tool.health_check_cmd,
                "dependencies": tool.dependencies,
                "command_template": tool.command_template
            })
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Failed to get tools status: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@bp.route('/<tool_name>/health', methods=['POST'])
def trigger_health_check(tool_name):
    result = ToolRegistry.check_health(tool_name)
    return jsonify(result)

@bp.route('/', methods=['POST'])
def create_tool():
    """创建新工具"""
    data = request.get_json()
    
    # 验证必填字段
    required_fields = ['name', 'display_name', 'category']
    for field in required_fields:
        if not data.get(field):
            return jsonify({"error": f"Missing required field: {field}"}), 400
    
    # 检查工具是否已存在
    if db.session.get(Tool, data['name']):
        return jsonify({"error": f"Tool '{data['name']}' already exists"}), 409
    
    # 创建新工具
    tool = Tool(
        name=data['name'],
        display_name=data['display_name'],
        category=data['category'],
        description=data.get('description', ''),
        command_template=data.get('command_template'),
        dependencies=data.get('dependencies', {}),
        health_check_cmd=data.get('health_check_cmd', f"{data['name']} --version"),
        is_available=False
    )
    
    db.session.add(tool)
    db.session.commit()
    
    logger.info(f"✅ New tool created: {tool.name}")
    
    return jsonify({
        "success": True,
        "tool": {
            "name": tool.name,
            "display_name": tool.display_name,
            "category": tool.category,
            "description": tool.description,
            "health_check_cmd": tool.health_check_cmd,
            "dependencies": tool.dependencies
        }
    }), 201

@bp.route('/<tool_name>', methods=['DELETE'])
def delete_tool(tool_name):
    """删除工具"""
    tool = db.session.get(Tool, tool_name)
    if not tool:
        return jsonify({"error": "Tool not found"}), 404
    
    # 检查是否有运行中的任务
    running_tasks = Task.query.filter_by(tool_name=tool_name, status=TaskStatus.RUNNING).count()
    if running_tasks > 0:
        return jsonify({"error": f"Cannot delete tool with {running_tasks} running task(s)"}), 400
    
    # 删除关联的任务日志
    from app.models.task import TaskLog
    TaskLog.query.filter_by(task_id=db.session.query(Task.id).filter_by(tool_name=tool_name).subquery()).delete(synchronize_session=False)
    
    # 删除工具
    db.session.delete(tool)
    db.session.commit()
    
    # 清理内存缓存
    if tool_name in ToolRegistry._live_status_cache:
        del ToolRegistry._live_status_cache[tool_name]
    
    logger.info(f"🗑️ Tool deleted: {tool_name}")
    
    return jsonify({
        "success": True,
        "message": f"Tool '{tool_name}' deleted successfully"
    })

@bp.route('/<tool_name>', methods=['PUT'])
def update_tool(tool_name):
    """更新工具配置（支持修改健康检测命令和依赖项）"""
    data = request.get_json()
    tool = db.session.get(Tool, tool_name)
    if not tool:
        return jsonify({"error": "Tool not found"}), 404

    if 'health_check_cmd' in data:
        tool.health_check_cmd = data['health_check_cmd']

    if 'dependencies' in data:
        tool.dependencies = data['dependencies']

    db.session.commit()
    return jsonify({
        "success": True,
        "health_check_cmd": tool.health_check_cmd,
        "dependencies": tool.dependencies
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
