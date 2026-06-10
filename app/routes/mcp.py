from flask import Blueprint, request, jsonify
from app.services.task_manager import task_manager
import uuid

bp = Blueprint('mcp', __name__)

@bp.route('/', methods=['POST'])
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
        
    elif method == 'tools/call':
        params = data.get('params', {})
        tool_name = params.get('name')
        arguments = params.get('arguments', {})
        target = arguments.pop('target', 'unknown')
        
        task_id = task_manager.submit_task(tool_name, target, arguments, mcp_request_id=req_id)
        
        return jsonify({
            "jsonrpc": "2.0", "id": req_id,
            "result": {"task_id": task_id, "status": "ACCEPTED"}
        })
        
    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}})
