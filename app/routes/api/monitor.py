from flask import Blueprint, jsonify, Response, stream_with_context
from app.services.monitor_service import MonitorService
import app.extensions as extensions
import json
from datetime import datetime

bp = Blueprint('monitor', __name__)

@bp.route('/health', methods=['GET'])
def health_check():
    """MCP客户端健康检查端点"""
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

        pubsub = extensions.redis_client.pubsub()
        pubsub.subscribe('hexstrike:logs')

        # 读取历史日志（从 Redis List，按时间正序）
        history = extensions.redis_client.lrange(f"task:{task_id}:logs", 0, -1)
        for msg in history:
            yield f"data: {json.dumps({'message': msg})}\n\n"

        # 监听实时日志（从 Redis Pub/Sub）
        for message in pubsub.listen():
            if message['type'] == 'message':
                data = message['data'].decode('utf-8') if isinstance(message['data'], bytes) else message['data']
                parts = data.split('|', 1)
                if len(parts) == 2 and parts[0] == task_id:
                    yield f"data: {json.dumps({'message': parts[1]})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')
