"""
Celery Worker 日志 API 端点
提供 Worker 运行日志的查询和实时推送
"""

from flask import Blueprint, request, jsonify, Response
import os
import json
from datetime import datetime
from pathlib import Path

bp = Blueprint('worker_logs', __name__)

# Worker 日志文件路径
WORKER_LOG_FILE = os.environ.get('WORKER_LOG_FILE', '/tmp/hexstrike_worker.log')


@bp.route('/worker', methods=['GET'])
def get_worker_logs():
    """获取 Worker 日志（分页）"""
    limit = request.args.get('limit', 100, type=int)
    since_line = request.args.get('since_line', 0, type=int)
    is_incremental = since_line > 0
    
    try:
        log_file = Path(WORKER_LOG_FILE)
        
        # 如果日志文件不存在，返回空
        if not log_file.exists():
            return jsonify({
                "success": True,
                "logs": [],
                "total_lines": 0,
                "is_incremental": is_incremental,
                "message": "Worker log file not found"
            })
        
        # 读取日志文件
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
        
        total_lines = len(all_lines)
        
        # 增量获取：只返回新增的行
        if is_incremental:
            start_line = min(since_line, total_lines)
            new_lines = all_lines[start_line:]
        else:
            # 全量获取：返回最新的 limit 行
            start_line = max(0, total_lines - limit)
            new_lines = all_lines[start_line:]
        
        # 清理日志行（移除多余换行符）
        logs = [line.rstrip('\n') for line in new_lines]
        
        return jsonify({
            "success": True,
            "logs": logs,
            "total_lines": total_lines,
            "is_incremental": is_incremental
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route('/worker/stream', methods=['GET'])
def stream_worker_logs():
    """Server-Sent Events: 实时推送 Worker 日志"""
    def event_stream():
        log_file = Path(WORKER_LOG_FILE)
        last_position = 0
        
        while True:
            try:
                if not log_file.exists():
                    # 文件不存在，等待
                    yield f"data: {json.dumps({'type': 'wait', 'message': 'Waiting for log file...'})}\n\n"
                    import time
                    time.sleep(2)
                    continue
                
                # 读取新日志
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(last_position)
                    new_lines = f.readlines()
                    last_position = f.tell()
                
                # 推送新日志
                for line in new_lines:
                    line = line.rstrip('\n')
                    if line:
                        # 解析日志级别
                        log_entry = parse_log_line(line)
                        yield f"data: {json.dumps(log_entry)}\n\n"
                
                # 等待 2 秒后继续
                import time
                time.sleep(2)
                
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                import time
                time.sleep(2)
    
    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'  # Nginx: 禁用缓冲
        }
    )


def parse_log_line(line: str) -> dict:
    """解析日志行，提取时间、级别、消息"""
    import re
    
    # 日志格式：[ HexStrike Worker] 2026-07-20 17:23:36 [INFO] logger: message
    pattern = r'\[ HexStrike Worker\] (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] ([^:]+): (.+)'
    match = re.match(pattern, line)
    
    if match:
        return {
            "type": "log",
            "timestamp": match.group(1),
            "level": match.group(2),
            "logger": match.group(3),
            "message": match.group(4)
        }
    else:
        return {
            "type": "log",
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "logger": "unknown",
            "message": line
        }


@bp.route('/worker/stats', methods=['GET'])
def get_worker_stats():
    """获取 Worker 统计信息"""
    try:
        log_file = Path(WORKER_LOG_FILE)
        
        if not log_file.exists():
            return jsonify({
                "success": False,
                "error": "Worker log file not found"
            })
        
        # 统计日志级别分布
        level_counts = {"INFO": 0, "ERROR": 0, "WARNING": 0, "DEBUG": 0}
        
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if '[ERROR]' in line:
                    level_counts["ERROR"] += 1
                elif '[WARNING]' in line:
                    level_counts["WARNING"] += 1
                elif '[DEBUG]' in line:
                    level_counts["DEBUG"] += 1
                elif '[INFO]' in line:
                    level_counts["INFO"] += 1
        
        # 获取文件大小
        file_size = log_file.stat().st_size
        
        return jsonify({
            "success": True,
            "stats": {
                "total_lines": sum(level_counts.values()),
                "level_counts": level_counts,
                "file_size_bytes": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 2)
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
