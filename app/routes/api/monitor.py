from flask import Blueprint, jsonify, Response, stream_with_context
from app.services.monitor_service import MonitorService
import app.extensions as extensions
import json
from datetime import datetime
import time
import logging

logger = logging.getLogger(__name__)

bp = Blueprint('monitor', __name__)

@bp.route('/health', methods=['GET'])
def health_check():
    """MCP 客户端健康检查端点"""
    return jsonify({
        "status": "healthy",
        "service": "HexStrike AI Backend",
        "timestamp": datetime.now().isoformat(),
        "redis_connected": extensions.redis_client is not None
    })

@bp.route('/stats', methods=['GET'])
def get_stats():
    return jsonify(MonitorService.get_system_stats())

@bp.route('/logs/<task_id>/stream')
def stream_logs(task_id):
    def generate():
        if not extensions.redis_client:
            yield f"data: {json.dumps({'error': 'Redis not available'})}\n\n"
            return

        max_retries = 3
        retry_delay = 2.0  # 秒
        
        for attempt in range(max_retries):
            try:
                pubsub = extensions.redis_client.pubsub()
                pubsub.subscribe('hexstrike:logs')

                # 读取历史日志（从 Redis List，按时间正序）
                history = extensions.redis_client.lrange(f"task:{task_id}:logs", 0, -1)
                for msg in history:
                    msg_str = msg.decode('utf-8') if isinstance(msg, bytes) else msg
                    try:
                        log_data = json.loads(msg_str)
                        yield f"data: {json.dumps({'task_id': task_id, **log_data})}\n\n"
                    except json.JSONDecodeError:
                        yield f"data: {json.dumps({'task_id': task_id, 'message': msg_str, 'timestamp': None})}\n\n"

                # 监听实时日志（从 Redis Pub/Sub）
                for message in pubsub.listen():
                    if message is None:
                        logger.warning(f"Redis Pub/Sub connection lost for task {task_id}")
                        break
                    
                    if message['type'] == 'message':
                        data = message['data'].decode('utf-8') if isinstance(message['data'], bytes) else message['data']
                        parts = data.split('|', 1)
                        if len(parts) == 2 and parts[0] == task_id:
                            try:
                                log_data = json.loads(parts[1])
                                yield f"data: {json.dumps({'task_id': task_id, **log_data})}\n\n"
                            except json.JSONDecodeError:
                                yield f"data: {json.dumps({'task_id': task_id, 'message': parts[1], 'timestamp': None})}\n\n"
                
                pubsub.close()
                
            except Exception as e:
                logger.error(f"Redis Pub/Sub error (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    yield f"data: {json.dumps({'error': 'Log stream disconnected', 'message': str(e)})}\n\n"
                    return

    return Response(stream_with_context(generate()), mimetype='text/event-stream')
