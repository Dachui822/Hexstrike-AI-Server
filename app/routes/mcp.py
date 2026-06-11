from flask import Blueprint, request, jsonify
from app.services.task_manager import task_manager
from app.services.tool_registry import ToolRegistry
from app.extensions import db
from app.models.task import Task
import uuid
import logging

logger = logging.getLogger(__name__)
bp = Blueprint('mcp', __name__)

@bp.route('', methods=['POST'], strict_slashes=False)
def mcp_handler():
    data = request.json
    method = data.get('method')
    req_id = data.get('id')

    if method == 'initialize':
        return jsonify({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2.0",
                "serverInfo": {"name": "HexStrike AI", "version": "1.0.0"},
                "capabilities": {"tools": {}}
            }
        })

    elif method == 'tools/list':
        try:
            tools = ToolRegistry.get_live_tools_status()
            mcp_tools = []
            for t in tools:
                mcp_tools.append({
                    "name": t.get('name'),
                    "description": t.get('description', f"Execute {t.get('display_name')}"),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string", "description": "Target IP, URL or domain"},
                            "options": {"type": "string", "description": "Additional options/arguments"}
                        },
                        "required": ["target"]
                    }
                })
            # 添加任务状态查询工具
            mcp_tools.append({
                "name": "get_task_status",
                "description": "Get the status, progress, and result of a submitted task.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "The task ID returned by tools/call"}
                    },
                    "required": ["task_id"]
                }
            })
            return jsonify({
                "jsonrpc": "2.0", "id": req_id,
                "result": {"tools": mcp_tools}
            })
        except Exception as e:
            logger.error(f"Error listing tools for MCP: {e}")
            return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}})

    elif method == 'tools/call':
        params = data.get('params', {})
        tool_name = params.get('name')
        arguments = params.get('arguments', {})

        try:
            if tool_name == 'get_task_status':
                task_id = arguments.get('task_id')
                if not task_id:
                    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "task_id is required"}})
                
                task = db.session.get(Task, task_id)
                if not task:
                    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Task not found"}})
                
                return jsonify({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "task_id": task.id,
                        "status": task.status.value if task.status else "unknown",
                        "progress": task.progress,
                        "error": task.error_message,
                        "output_path": task.output_path
                    }
                })
            else:
                # 提交安全扫描任务
                target = arguments.pop('target', 'unknown')
                task_id = task_manager.submit_task(tool_name, target, arguments, mcp_request_id=req_id)
                return jsonify({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"task_id": task_id, "status": "ACCEPTED"}
                })
        except Exception as e:
            logger.error(f"Error processing tools/call [{tool_name}]: {e}")
            return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}})

    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}})
