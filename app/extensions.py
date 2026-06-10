from flask_sqlalchemy import SQLAlchemy
from redis import Redis
import logging

db = SQLAlchemy()
redis_client = None
logger = logging.getLogger(__name__)

def init_extensions(app):
    global redis_client

    db.init_app(app)

    redis_url = app.config.get('REDIS_URL')
    redis_client = Redis.from_url(redis_url, decode_responses=True)

    try:
        redis_client.ping()
        logger.info("✅ Redis connected successfully")
    except Exception as e:
        logger.warning(f"⚠️ Redis connection failed: {e}")

    with app.app_context():
        db.create_all()
        logger.info("✅ Database tables created")
        
        # 初始化工具注册表
        from app.services.tool_registry import ToolRegistry
        ToolRegistry.init_tools()
        logger.info("✅ Tool registry initialized with default tools")
