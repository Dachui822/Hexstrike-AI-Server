"""
统一日志 API - 系统服务日志 + 任务日志
系统服务日志从 /var/log/hexstrike_ai/ 目录读取
任务日志从数据库/Redis/文件读取
"""

from flask import Blueprint, request, jsonify, Response, current_app
import os
import json
import re
import logging
import time
from datetime import datetime
from pathlib import Path

bp = Blueprint('journal_logs', __name__)
logger = logging.getLogger(__name__)

# 系统服务日志文件路径
LOG_FILES = {
    'api': '/var/log/hexstrike_ai/hexstrike_ai_service.log',
    'worker': '/var/log/hexstrike_ai/hexstrike_worker_service.log',
}

# 定义所有服务单元
SERVICES = {
    'api': 'hexstrike.service',
    'worker': 'hexstrike-worker.service',
    'all': ['hexstrike.service', 'hexstrike-worker.service']
}


@bp.route('/', methods=['GET'])
@bp.route('/<unit>', methods=['GET'])
def get_journal_logs(unit='all'):
    """
    获取系统日志（从文件读取）
    参数：
        unit: 服务名称 (api, worker, all)
        lines: 返回行数 (默认 100)
    """
    lines_count = request.args.get('lines', 100, type=int)

    # 确定要读取的日志文件
    if unit == 'all':
        files_to_read = list(LOG_FILES.values())
    elif unit in LOG_FILES:
        files_to_read = [LOG_FILES[unit]]
    else:
        files_to_read = [unit]

    logger.info(f"Reading logs for {unit}, lines={lines_count}, files={files_to_read}")
    all_logs = []

    for log_file in files_to_read:
        try:
            path = Path(log_file)
            if path.exists():
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    file_lines = f.readlines()
                    recent_lines = file_lines[-lines_count:] if len(file_lines) > lines_count else file_lines
                    for line in recent_lines:
                        line = line.strip()
                        if line:
                            parsed = parse_log_line(line, log_file)
                            formatted = f"[{parsed['timestamp']}] [{parsed['level']}] {parsed['message']}"
                            all_logs.append(formatted)
                    logger.info(f"Loaded {len(all_logs)} lines from {log_file}")
            else:
                logger.warning(f"Log file not found: {log_file}")
        except PermissionError:
            logger.error(f"Permission denied reading: {log_file}")
            all_logs.append(f"ERROR: Permission denied for {log_file}")
        except Exception as e:
            logger.error(f"Failed to read {log_file}: {e}", exc_info=True)
            all_logs.append(f"ERROR reading {log_file}: {e}")

    return jsonify({
        "success": True,
        "logs": all_logs,
        "total_lines": len(all_logs),
        "source": "file",
        "files": files_to_read
    })


@bp.route('/stream', methods=['GET'])
@bp.route('/<unit>/stream', methods=['GET'])
def stream_journal_logs(unit='all'):
    """
    Server-Sent Events: 实时推送日志（从文件轮询）
    参数：
        unit: 服务名称 (api, worker, all)
        lines: 初始加载行数 (默认 50)
    """
    lines_count = request.args.get('lines', 50, type=int)

    # 确定服务单元
    if unit == 'all':
        files_to_read = list(LOG_FILES.values())
    elif unit in LOG_FILES:
        files_to_read = [LOG_FILES[unit]]
    else:
        files_to_read = [unit]

    logger.info(f"SSE stream opened for {unit}")

    def event_stream():
        file_positions = {f: 0 for f in files_to_read}

        while True:
            try:
                for log_file in files_to_read:
                    try:
                        path = Path(log_file)
                        if not path.exists():
                            continue

                        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                            f.seek(file_positions[log_file])
                            new_lines = f.readlines()
                            file_positions[log_file] = f.tell()

                        for line in new_lines:
                            line = line.strip()
                            if line:
                                log_entry = parse_log_line(line, log_file)
                                yield f"data: {json.dumps(log_entry)}\n\n"
                    except Exception as e:
                        logger.error(f"SSE Error reading {log_file}: {e}")
                        yield f"data: {json.dumps({'type': 'error', 'message': f'Error reading {log_file}: {e}'})}\n\n"

                import time
                time.sleep(2)

            except Exception as e:
                logger.error(f"SSE Stream error: {e}", exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                import time
                time.sleep(2)
    
    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@bp.route('/stats', methods=['GET'])
@bp.route('/<unit>/stats', methods=['GET'])
def get_journal_stats(unit='all'):
    """获取日志统计信息（从文件读取）"""
    # 确定要读取的文件
    if unit == 'all':
        files_to_read = list(LOG_FILES.values())
    elif unit in LOG_FILES:
        files_to_read = [LOG_FILES[unit]]
    else:
        files_to_read = [unit]

    logger.info(f"Calculating stats for {unit}, files={files_to_read}")
    stats = {
        "services": {},
        "total_lines": 0,
        "level_counts": {"ERROR": 0, "WARNING": 0, "INFO": 0, "DEBUG": 0}
    }

    for log_file in files_to_read:
        try:
            path = Path(log_file)
            if not path.exists():
                logger.warning(f"Log file not found for stats: {log_file}")
                continue

            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()
                total = len(all_lines)

                level_counts = {"ERROR": 0, "WARNING": 0, "INFO": 0, "DEBUG": 0}
                for line in all_lines:
                    if 'ERROR' in line or 'Failed' in line:
                        level_counts["ERROR"] += 1
                    elif 'WARNING' in line or 'WARN' in line:
                        level_counts["WARNING"] += 1
                    elif 'DEBUG' in line:
                        level_counts["DEBUG"] += 1
                    else:
                        level_counts["INFO"] += 1

                stats["services"][log_file] = {
                    "total_lines": total,
                    "level_counts": level_counts
                }
                stats["total_lines"] += total
                for level, count in level_counts.items():
                    stats["level_counts"][level] += count

        except Exception as e:
            logger.error(f"Stats Failed for {log_file}: {e}", exc_info=True)
            stats["services"][log_file] = {"error": str(e)}

    logger.info(f"Stats complete: total={stats['total_lines']} lines, levels={stats['level_counts']}")
    return jsonify({
        "success": True,
        "stats": stats,
        "source": "file"
    })


@bp.route('/journal/services', methods=['GET'])
def get_available_services():
    """获取可用的服务单元列表"""
    return jsonify({
        "success": True,
        "services": [
            {"name": "hexstrike.service", "description": "Web/API 服务"},
            {"name": "hexstrike-worker.service", "description": "Celery Worker"}
        ],
        "log_files": LOG_FILES
    })


@bp.route('/task/<task_id>', methods=['GET'])
def get_task_logs(task_id):
    """
    获取指定任务的执行日志
    优先从 Redis 读取实时日志，其次从数据库读取
    """
    lines_count = request.args.get('lines', 100, type=int)
    
    logs = []
    source = "unknown"
    
    # 1. 尝试从 Redis 读取（实时日志）
    try:
        from app.extensions import redis_client
        if redis_client:
            redis_logs = redis_client.lrange(f"task:{task_id}:logs", 0, -1)
            if redis_logs:
                for log_bytes in redis_logs[-lines_count:]:
                    try:
                        log_data = json.loads(log_bytes.decode('utf-8'))
                        formatted = f"[{log_data.get('timestamp', '')}] [{log_data.get('level', 'INFO')}] {log_data.get('message', '')}"
                        logs.append(formatted)
                    except:
                        logs.append(log_bytes.decode('utf-8', errors='ignore'))
                source = "redis"
    except Exception as e:
        logger.warning(f"Failed to read task logs from Redis: {e}")
    
    # 2. 如果 Redis 没有，从数据库读取
    if not logs:
        try:
            from app.models.task import TaskLog
            task_logs = TaskLog.query.filter_by(task_id=task_id).order_by(TaskLog.timestamp.asc()).limit(lines_count).all()
            if task_logs:
                for log in task_logs:
                    ts = log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else ''
                    formatted = f"[{ts}] [{log.level or 'INFO'}] {log.message}"
                    logs.append(formatted)
                source = "database"
        except Exception as e:
            logger.warning(f"Failed to read task logs from database: {e}")
    
    # 3. 如果数据库也没有，尝试从文件读取
    if not logs:
        try:
            fallback_path = f"/var/log/hexstrike_ai/{task_id}.log"
            path = Path(fallback_path)
            if path.exists():
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    file_lines = f.readlines()
                    recent_lines = file_lines[-lines_count:] if len(file_lines) > lines_count else file_lines
                    logs = [line.strip() for line in recent_lines if line.strip()]
                source = "file"
        except Exception as e:
            logger.warning(f"Failed to read task logs from file: {e}")
    
    return jsonify({
        "success": True,
        "logs": logs,
        "total_lines": len(logs),
        "source": source,
        "task_id": task_id
    })


@bp.route('/task/<task_id>/stream', methods=['GET'])
def stream_task_logs(task_id):
    """
    SSE 实时推送任务日志（从 Redis 订阅）
    """
    lines_count = request.args.get('lines', 50, type=int)
    
    logger.info(f"SSE task log stream opened for {task_id}")
    
    def event_stream():
        # 先发送历史日志
        try:
            from app.extensions import redis_client
            if redis_client:
                redis_logs = redis_client.lrange(f"task:{task_id}:logs", 0, -1)
                if redis_logs:
                    for log_bytes in redis_logs[-lines_count:]:
                        try:
                            log_data = json.loads(log_bytes.decode('utf-8'))
                            yield f"data: {json.dumps(log_data)}\n\n"
                        except:
                            pass
        except Exception as e:
            logger.error(f"Failed to read historical task logs: {e}")
        
        # 订阅新日志
        try:
            from app.extensions import redis_client
            if redis_client:
                pubsub = redis_client.pubsub()
                pubsub.psubscribe(f"**hexstrike:logs")
                
                for message in pubsub.listen():
                    try:
                        if message['type'] == 'pmessage':
                            channel = message.get('channel', b'').decode('utf-8', errors='ignore')
                            data = message.get('data', b'').decode('utf-8', errors='ignore')
                            # 数据格式：task_id|json
                            if '|' in data:
                                tid, log_json = data.split('|', 1)
                                if tid == task_id:
                                    log_data = json.loads(log_json)
                                    yield f"data: {json.dumps(log_data)}\n\n"
                    except Exception as e:
                        logger.debug(f"SSE task log error: {e}")
                    time.sleep(0.1)
        except Exception as e:
            logger.error(f"SSE task log subscription error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


def parse_log_line(line: str, source_file: str) -> dict:
    """解析日志行 - 支持 systemd journal 格式"""
    import re

    # systemd journal 格式：[timestamp] [LEVEL] original_message
    # 例如：[2026-07-21T14:25:12.123456] [INFO] 2026-07-19 04:49:23,861 - werkzeug - INFO - ...
    journal_pattern = r'^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s+\[(\w+)\]\s+(.+)$'
    match = re.match(journal_pattern, line)

    if match:
        outer_timestamp = match.group(1)
        outer_level = match.group(2)
        inner_message = match.group(3)
        
        # 递归解析内部消息，获取真正的 logger 和精简 message
        inner = _parse_inner_log(inner_message)
        
        return {
            "type": "log",
            "timestamp": outer_timestamp,
            "level": outer_level,
            "logger": inner.get("logger", "systemd"),
            "message": inner.get("message", inner_message)
        }

    # 直接解析普通日志格式，补充缺失字段
    inner = _parse_inner_log(line)
    return {
        "type": "log",
        "timestamp": datetime.now().isoformat(),
        "level": inner.get("level", "INFO"),
        "logger": inner.get("logger", "unknown"),
        "message": inner.get("message", line),
        "source": source_file
    }


def _parse_inner_log(line: str) -> dict:
    """解析内部日志内容（去除 systemd journal 前缀后的消息）"""
    import re

    # 标准日志格式：timestamp - logger - LEVEL - message
    # 例如：2026-07-19 04:49:23,861 - werkzeug - INFO - 172.29.5.42 - - ...
    standard_pattern = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?) - ([\w.]+) - (\w+) - (.+)'
    match = re.match(standard_pattern, line)

    if match:
        return {
            "timestamp": match.group(1),
            "level": match.group(3),
            "logger": match.group(2),
            "message": match.group(4)
        }

    # werkzeug 日志格式（无前缀）：IP - - [timestamp] "METHOD path HTTP/1.1" status size
    werkzeug_pattern = r'(\d+\.\d+\.\d+\.\d+) - - \[([^\]]+)\] "([^"]+)" (\d+)'
    match = re.match(werkzeug_pattern, line)

    if match:
        return {
            "timestamp": match.group(2),
            "level": "INFO",
            "logger": "werkzeug",
            "message": line
        }

    # 无法解析，返回原样
    return {
        "timestamp": None,
        "level": "INFO",
        "logger": "unknown",
        "message": line
    }
