"""
统一日志 API - 从文件读取系统日志
所有日志都从 /var/log/hexstrike_ai/ 目录读取
"""

from flask import Blueprint, request, jsonify, Response
import os
import json
import re
import logging
from datetime import datetime
from pathlib import Path

bp = Blueprint('journal_logs', __name__)
logger = logging.getLogger(__name__)

# 日志文件路径
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
    logger.info(f"[Journal API] GET /api/journal/{unit} - requested_lines={lines_count}")

    # 确定要读取的日志文件
    if unit == 'all':
        files_to_read = list(LOG_FILES.values())
    elif unit in LOG_FILES:
        files_to_read = [LOG_FILES[unit]]
    else:
        files_to_read = [unit]

    logger.info(f"[Journal API] Resolved files: {files_to_read}")
    all_logs = []

    for log_file in files_to_read:
        try:
            path = Path(log_file)
            exists = path.exists()
            size = path.stat().st_size if exists else 0
            logger.info(f"[Journal API] Check file: {log_file} | exists={exists} | size={size} bytes")

            if exists:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    file_lines = f.readlines()
                    recent_lines = file_lines[-lines_count:] if len(file_lines) > lines_count else file_lines
                    valid_lines = [line.strip() for line in recent_lines if line.strip()]
                    all_logs.extend(valid_lines)
                    logger.info(f"[Journal API] Loaded {len(valid_lines)} valid lines from {log_file} (total in file: {len(file_lines)})")
            else:
                logger.warning(f"[Journal API] File not found: {log_file}")
        except PermissionError:
            logger.error(f"[Journal API] Permission denied reading: {log_file}")
            all_logs.append(f"ERROR: Permission denied for {log_file}")
        except Exception as e:
            logger.error(f"[Journal API] Failed to read {log_file}: {e}", exc_info=True)
            all_logs.append(f"ERROR reading {log_file}: {e}")

    logger.info(f"[Journal API] Returning {len(all_logs)} total log lines")
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
    logger.info(f"[Journal API] SSE /api/journal/{unit}/stream - initial_lines={lines_count}")

    # 确定服务单元
    if unit == 'all':
        files_to_read = list(LOG_FILES.values())
    elif unit in LOG_FILES:
        files_to_read = [LOG_FILES[unit]]
    else:
        files_to_read = [unit]

    logger.info(f"[Journal API] SSE Resolved files: {files_to_read}")

    def event_stream():
        file_positions = {f: 0 for f in files_to_read}
        logger.info(f"[Journal API] SSE stream started for {unit}")

        while True:
            try:
                for log_file in files_to_read:
                    try:
                        path = Path(log_file)
                        if not path.exists():
                            logger.debug(f"[Journal API] SSE File not found: {log_file}")
                            continue

                        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                            f.seek(file_positions[log_file])
                            new_lines = f.readlines()
                            file_positions[log_file] = f.tell()

                        pushed = 0
                        for line in new_lines:
                            line = line.strip()
                            if line:
                                log_entry = parse_log_line(line, log_file)
                                yield f"data: {json.dumps(log_entry)}\n\n"
                                pushed += 1
                        if pushed > 0:
                            logger.debug(f"[Journal API] SSE Pushed {pushed} new lines from {log_file}")
                    except Exception as e:
                        logger.error(f"[Journal API] SSE Error reading {log_file}: {e}")
                        yield f"data: {json.dumps({'type': 'error', 'message': f'Error reading {log_file}: {e}'})}\n\n"

                import time
                time.sleep(2)

            except Exception as e:
                logger.error(f"[Journal API] SSE Stream error: {e}", exc_info=True)
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
    logger.info(f"[Journal API] GET /api/journal/{unit}/stats")
    # 确定要读取的文件
    if unit == 'all':
        files_to_read = list(LOG_FILES.values())
    elif unit in LOG_FILES:
        files_to_read = [LOG_FILES[unit]]
    else:
        files_to_read = [unit]

    logger.info(f"[Journal API] Stats Resolved files: {files_to_read}")
    stats = {
        "services": {},
        "total_lines": 0,
        "level_counts": {"ERROR": 0, "WARNING": 0, "INFO": 0, "DEBUG": 0}
    }

    for log_file in files_to_read:
        try:
            path = Path(log_file)
            if not path.exists():
                logger.warning(f"[Journal API] Stats File not found: {log_file}")
                continue

            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()
                total = len(all_lines)
                logger.info(f"[Journal API] Stats Reading {log_file}: {total} lines")

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
            logger.error(f"[Journal API] Stats Failed for {log_file}: {e}", exc_info=True)
            stats["services"][log_file] = {"error": str(e)}

    logger.info(f"[Journal API] Stats Returning: total_lines={stats['total_lines']}, levels={stats['level_counts']}")
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


def parse_log_line(line: str, source_file: str) -> dict:
    """解析日志行"""
    import re
    
    # werkzeug 日志格式：IP - - [timestamp] "METHOD path HTTP/1.1" status size
    werkzeug_pattern = r'(\d+\.\d+\.\d+\.\d+) - - \[([^\]]+)\] "([^"]+)" (\d+)'
    match = re.match(werkzeug_pattern, line)
    
    if match:
        return {
            "type": "log",
            "timestamp": match.group(2),
            "level": "INFO",
            "logger": "werkzeug",
            "message": line
        }
    
    # 标准日志格式：timestamp - logger - LEVEL - message
    standard_pattern = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - ([\w.]+) - (\w+) - (.+)'
    match = re.match(standard_pattern, line)
    
    if match:
        return {
            "type": "log",
            "timestamp": match.group(1),
            "level": match.group(3),
            "logger": match.group(2),
            "message": match.group(4)
        }
    
    # 无法解析的格式
    return {
        "type": "log",
        "timestamp": datetime.now().isoformat(),
        "level": "INFO",
        "logger": "unknown",
        "message": line,
        "source": source_file
    }
