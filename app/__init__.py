from flask import Flask, jsonify
from app.config import get_config
from app.extensions import init_extensions
from app.routes import register_blueprints
from app.services.tool_registry import ToolRegistry
from app.services.log_service import start_consumer as start_log_consumer
from app.services.task_manager import task_manager
import logging

def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    init_extensions(app)
    
    # 注入 Flask 实例到 TaskManager，解决后台线程上下文缺失问题
    task_manager.init_app(app)
    
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

    # 启动日志消费者线程（在应用上下文外）
    start_log_consumer()

    # 注册根路径路由 (MCP 客户端连接测试等)
    @app.route('/health', methods=['GET'])
    def health_check():
        """MCP 客户端健康检查端点"""
        from datetime import datetime
        import app.extensions as extensions
        return jsonify({
            "status": "healthy",
            "service": "HexStrike AI Backend",
            "timestamp": datetime.now().isoformat(),
            "redis_connected": extensions.redis_client is not None
        })

    return app
