from flask import Flask
from app.config import get_config
from app.extensions import init_extensions
from app.routes import register_blueprints
from app.services.tool_registry import ToolRegistry
import logging

def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    init_extensions(app)
    register_blueprints(app)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('hexstrike.log')
        ]
    )

    # 启动自动健康检测
    with app.app_context():
        ToolRegistry.start_auto_health_check()

    return app
