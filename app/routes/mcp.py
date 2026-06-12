from flask import Blueprint, request, jsonify
from app.services.task_manager import task_manager
from app.services.tool_registry import ToolRegistry
from app.extensions import db
from app.models.task import Task, TaskLog
from app.models.tool import Tool
import uuid
import logging
import os

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

        logger.info(f"[DEBUG] tools/call received: tool_name={tool_name}, arguments={arguments}")

        try:
            if tool_name == 'get_task_status':
                task_id = arguments.get('task_id')
                if not task_id:
                    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "task_id is required"}})
                
                task = db.session.get(Task, task_id)
                if not task:
                    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Task not found"}})
                
                # 查询任务日志 (按时间正序)
                logs = TaskLog.query.filter_by(task_id=task_id).order_by(TaskLog.timestamp.asc()).all()
                
                # 安全获取枚举值 (兼容字符串)
                def get_enum_val(v, default):
                    return v.value if hasattr(v, 'value') else str(v) if v else default

                log_messages = [
                    {
                        "level": get_enum_val(log.level, "INFO"),
                        "source": get_enum_val(log.source, "system"),
                        "message": log.message,
                        "timestamp": log.timestamp.isoformat() if log.timestamp else None
                    }
                    for log in logs
                ]

                # 读取输出文件内容 (如果任务完成且有输出)
                output_content = None
                status_val = get_enum_val(task.status, 'unknown')
                
                if status_val in ('SUCCESS', 'FAILED') and task.output_path:
                    try:
                        if os.path.exists(task.output_path):
                            with open(task.output_path, 'r', encoding='utf-8', errors='ignore') as f:
                                output_content = f.read()
                    except Exception as e:
                        logger.warning(f"Failed to read output file {task.output_path}: {e}")
                        output_content = f"[Error reading output file: {str(e)}]"

                # 构建返回结果
                tool_record = db.session.get(Tool, task.tool_name)
                tool_version = tool_record.installed_version if tool_record else None

                result = {
                    "task_id": task.id,
                    "tool": task.tool_name,
                    "tool_version": tool_version,
                    "target": task.target,
                    "status": status_val,
                    "progress": task.progress,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                    "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                }

                if status_val == 'SUCCESS':
                    # 任务成功：返回工具扫描的结果内容 (output)
                    result["result"] = output_content if output_content else "[No output generated]"
                    result["message"] = "Task completed successfully. Here is the scan result:"
                elif status_val == 'FAILED':
                    # 任务失败：返回工具执行日志以便排查 (logs)
                    result["logs"] = log_messages
                    result["error"] = task.error_message
                    result["message"] = f"Task failed. Here are the execution logs for debugging: {task.error_message or ''}"
                else:
                    # 任务运行中
                    result["message"] = f"Task is {status_val}. Progress: {task.progress}%"

                return jsonify({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": result
                })
            else:
                # 提交安全扫描任务
                target = arguments.pop('target', 'unknown')
                logger.info(f"[DEBUG] Extracted target={repr(target)} for tool={tool_name}")
                tool_record = db.session.get(Tool, tool_name)
                tool_version = tool_record.installed_version if tool_record else None

                task_id = task_manager.submit_task(tool_name, target, arguments, mcp_request_id=req_id)
                return jsonify({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"task_id": task_id, "status": "ACCEPTED", "tool": tool_name, "tool_version": tool_version}
                })
        except Exception as e:
            logger.error(f"Error processing tools/call [{tool_name}]: {e}")
            return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}})

    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}})
