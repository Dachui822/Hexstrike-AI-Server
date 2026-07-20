from flask import Blueprint
from app.routes.api import tools as api_tools
from app.routes.api import tasks as api_tasks
from app.routes.api import monitor as api_monitor
from app.routes.api import logs as api_logs
from app.routes.api import worker_logs as api_worker_logs
from app.routes import mcp

def register_blueprints(app):
    app.register_blueprint(mcp.bp, url_prefix="/mcp")
    app.register_blueprint(api_tools.bp, url_prefix="/api/tools")
    app.register_blueprint(api_tasks.bp, url_prefix="/api/tasks")
    app.register_blueprint(api_monitor.bp, url_prefix="/api/monitor")
    app.register_blueprint(api_logs.bp, url_prefix="/api/logs")
    app.register_blueprint(api_worker_logs.bp, url_prefix="/api/logs")
