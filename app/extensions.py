from flask_sqlalchemy import SQLAlchemy
from redis import Redis, ConnectionPool
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
import logging
import time

db = SQLAlchemy()
redis_client = None
redis_pool = None
logger = logging.getLogger(__name__)


def create_redis_pool(redis_url, max_retries=3, retry_delay=2.0):
    """创建 Redis 连接池，带重试机制"""
    for attempt in range(max_retries):
        try:
            pool = ConnectionPool.from_url(
                redis_url,
                decode_responses=True,
                max_connections=100,  # 增加最大连接数，支持更高并发
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
                retry_on_timeout=True,
                health_check_interval=30,
                # 连接回收配置
                max_idle_time=300,  # 连接空闲 5 分钟后回收
                retry_on_error=[RedisConnectionError, RedisTimeoutError],
            )

            # 测试连接
            test_client = Redis(connection_pool=pool)
            test_client.ping()

            logger.info(f"✅ Redis connection pool created (attempt {attempt + 1}/{max_retries})")
            return pool

        except Exception as e:
            logger.warning(f"️ Redis connection attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)

    logger.error("❌ Redis connection pool creation failed after all retries")
    return None


def init_extensions(app):
    global redis_client, redis_pool

    db.init_app(app)

    redis_url = app.config.get('REDIS_URL')
    
    # 创建连接池
    redis_pool = create_redis_pool(redis_url)
    
    if redis_pool:
        redis_client = Redis(connection_pool=redis_pool)
    else:
        redis_client = None
        logger.error("🚫 Redis not available - continuing without Redis")

    with app.app_context():
        db.create_all()
        logger.info("✅ Database tables created")
        
        # 初始化工具注册表
        from app.services.tool_registry import ToolRegistry
        ToolRegistry.init_tools()
        logger.info("✅ Tool registry initialized with default tools")


def get_redis_client():
    """获取 Redis 客户端，带自动重连"""
    global redis_client, redis_pool
    
    if redis_client:
        try:
            redis_client.ping()
            return redis_client
        except:
            logger.warning("Redis connection lost, attempting to reconnect...")
            
            # 尝试重新创建连接
            if redis_pool:
                try:
                    redis_client = Redis(connection_pool=redis_pool)
                    redis_client.ping()
                    logger.info("✅ Redis reconnected")
                    return redis_client
                except Exception as e:
                    logger.error(f"❌ Redis reconnection failed: {e}")
            else:
                logger.error("❌ Redis pool not available")
    
    return None
