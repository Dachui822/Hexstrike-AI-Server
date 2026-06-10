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
