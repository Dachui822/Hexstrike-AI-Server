"""
Celery Worker 日志 API 端点
提供 Worker 运行日志的查询和实时推送
"""

from flask import Blueprint, request, jsonify, Response
import os
import json
import subprocess
from datetime import datetime
from pathlib import Path

bp = Blueprint('worker_logs', __name__)

# Worker 日志文件路径（支持多个位置）
WORKER_LOG_FILES = [
    os.environ.get('WORKER_LOG_FILE', '/var/log/hexstrike_ai/hexstrike_worker_service.log'),
    '/var/log/hexstrike_ai/hexstrike_worker_error.log',
]


@bp.route('/worker', methods=['GET'])
def get_worker_logs():
    """获取 Worker 日志（分页）"""
    limit = request.args.get('limit', 100, type=int)
    since_line = request.args.get('since_line', 0, type=int)
    is_incremental = since_line > 0
    
    try:
        # 优先尝试从 systemd journal 获取日志
        try:
            result = subprocess.run(
                ['journalctl', '-u', 'hexstrike-worker', '-n', str(limit), '--no-pager', '-o', 'cat'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                logs = result.stdout.strip().split('\n')
                return jsonify({
                    "success": True,
                    "logs": logs,
                    "total_lines": len(logs),
                    "is_incremental": is_incremental,
                    "source": "systemd-journal"
                })
        except Exception as journal_err:
            pass  # journal 失败则尝试文件
        
        # 从文件读取
        for log_file_path in WORKER_LOG_FILES:
            log_file = Path(log_file_path)
            if log_file.exists():
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    all_lines = f.readlines()
                
                total_lines = len(all_lines)
                
                if is_incremental:
                    start_line = min(since_line, total_lines)
                    new_lines = all_lines[start_line:]
                else:
                    start_line = max(0, total_lines - limit)
                    new_lines = all_lines[start_line:]
                
                logs = [line.rstrip('\n') for line in new_lines]
                
                return jsonify({
                    "success": True,
                    "logs": logs,
                    "total_lines": total_lines,
                    "is_incremental": is_incremental,
                    "source": f"file:{log_file_path}"
                })
        
        # 所有源都无日志
        return jsonify({
            "success": True,
            "logs": [],
            "total_lines": 0,
            "is_incremental": is_incremental,
            "message": "No worker logs found"
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route('/worker/stream', methods=['GET'])
def stream_worker_logs():
    """Server-Sent Events: 实时推送 Worker 日志（从 systemd journal）"""
    def event_stream():
        last_position = 0
        
        while True:
            try:
                # 使用 journalctl 跟踪新日志
                result = subprocess.run(
                    ['journalctl', '-u', 'hexstrike-worker', '-n', '10', '--no-pager', '-o', 'cat', '-f'],
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                
                if result.stdout:
                    new_lines = result.stdout.strip().split('\n')
                    for line in new_lines:
                        if line.strip():
                            log_entry = parse_log_line(line)
                            yield f"data: {json.dumps(log_entry)}\n\n"
                
                import time
                time.sleep(2)
                
            except Exception as e:
                # journalctl 失败则尝试文件轮询
                try:
                    for log_file_path in WORKER_LOG_FILES:
                        log_file = Path(log_file_path)
                        if log_file.exists():
                            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                f.seek(last_position)
                                new_lines = f.readlines()
                                last_position = f.tell()
                            
                            for line in new_lines:
                                line = line.rstrip('\n')
                                if line:
                                    log_entry = parse_log_line(line)
                                    yield f"data: {json.dumps(log_entry)}\n\n"
                            break
                except Exception as file_err:
                    pass
                
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


def parse_log_line(line: str) -> dict:
    """解析日志行，提取时间、级别、消息"""
    import re
    
    # systemd journal 格式：Jul 20 17:23:36 hostname service[pid]: message
    journal_pattern = r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+\S+\s+\S+\[(\d+)\]:\s+(.*)'
    match = re.match(journal_pattern, line)
    
    if match:
        timestamp_str = match.group(1)
        message = match.group(3)
        
        # 添加年份
        from datetime import datetime
        try:
            dt = datetime.strptime(f"2026 {timestamp_str}", "%Y %b %d %H:%M:%S")
            timestamp = dt.isoformat()
        except:
            timestamp = datetime.now().isoformat()
        
        # 检测日志级别
        level = "INFO"
        if "ERROR" in message or "error" in message or "Failed" in message:
            level = "ERROR"
        elif "WARNING" in message or "warning" in message or "WARN" in message:
            level = "WARNING"
        elif "DEBUG" in message or "debug" in message:
            level = "DEBUG"
        
        return {
            "type": "log",
            "timestamp": timestamp,
            "level": level,
            "logger": "celery.worker",
            "message": message
        }
    
    # 标准日志格式：[ HexStrike Worker] 2026-07-20 17:23:36 [INFO] logger: message
    standard_pattern = r'\[ HexStrike Worker\] (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] ([^:]+): (.+)'
    match = re.match(standard_pattern, line)
    
    if match:
        return {
            "type": "log",
            "timestamp": match.group(1),
            "level": match.group(2),
            "logger": match.group(3),
            "message": match.group(4)
        }
    
    # 无法解析的格式
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
        # 尝试从 journalctl 获取统计
        result = subprocess.run(
            ['journalctl', '-u', 'hexstrike-worker', '--no-pager', '-o', 'cat'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0 and result.stdout:
            all_lines = result.stdout.strip().split('\n')
        else:
            # journal 失败则尝试文件
            all_lines = []
            for log_file_path in WORKER_LOG_FILES:
                log_file = Path(log_file_path)
                if log_file.exists():
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        all_lines = f.readlines()
                    break
        
        # 统计日志级别分布
        level_counts = {"INFO": 0, "ERROR": 0, "WARNING": 0, "DEBUG": 0}
        
        for line in all_lines:
            if '[ERROR]' in line or 'ERROR' in line or 'error' in line or 'Failed' in line:
                level_counts["ERROR"] += 1
            elif '[WARNING]' in line or 'WARNING' in line or 'WARN' in line:
                level_counts["WARNING"] += 1
            elif '[DEBUG]' in line or 'DEBUG' in line:
                level_counts["DEBUG"] += 1
            elif '[INFO]' in line or 'INFO' in line:
                level_counts["INFO"] += 1
        
        total_lines = sum(level_counts.values())
        
        return jsonify({
            "success": True,
            "stats": {
                "total_lines": total_lines,
                "level_counts": level_counts,
                "source": "systemd-journal" if result.returncode == 0 else "file"
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
