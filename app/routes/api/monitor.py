from flask import Blueprint, jsonify, Response, stream_with_context
from app.services.monitor_service import MonitorService
import app.extensions as extensions
import json

bp = Blueprint('monitor', __name__)

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

        history = extensions.redis_client.lrange(f"task:{task_id}:logs", 0, -1)
        for msg in reversed(history):
            yield f"data: {json.dumps({'task_id': task_id, 'message': msg})}\n\n"

        for message in pubsub.listen():
            if message['type'] == 'message':
                data = message['data'].split('|', 1)
                if len(data) == 2 and data[0] == task_id:
                    yield f"data: {json.dumps({'task_id': task_id, 'message': data[1]})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')
