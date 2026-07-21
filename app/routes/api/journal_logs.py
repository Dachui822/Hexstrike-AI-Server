"""
统一日志 API - 基于 systemd journal
所有日志都从 systemd journal 读取，不再依赖文件
"""

from flask import Blueprint, request, jsonify, Response
import subprocess
import json
import re
from datetime import datetime

bp = Blueprint('journal_logs', __name__)

# 定义所有服务单元
SERVICES = {
    'api': 'hexstrike-ai.service',      # Web/API 服务
    'worker': 'hexstrike-worker.service',  # Celery Worker
    'all': ['hexstrike-ai.service', 'hexstrike-worker.service']
}


@bp.route('/journal', methods=['GET'])
@bp.route('/journal/<unit>', methods=['GET'])
def get_journal_logs(unit='all'):
    """
    获取 systemd journal 日志

    参数:
        unit: 服务名称 (api, worker, all)
        lines: 返回行数 (默认 100)
        since: 起始时间 (可选，如 "2024-01-01 00:00:00")
        priority: 日志级别 (0-7, 0=emerg, 3=err, 4=warn, 6=info, 7=debug)
    """
    lines = request.args.get('lines', 100, type=int)
    since = request.args.get('since', None)
    priority = request.args.get('priority', 6, type=int)

    # 确定要查询的服务单元
    if unit in SERVICES:
        service_units = SERVICES[unit]
        if isinstance(service_units, str):
            service_units = [service_units]
    else:
        # 自定义服务名
        service_units = [unit]
    
    # 构建 journalctl 命令
    cmd = ['journalctl', '--no-pager', '-o', 'cat', '-n', str(lines)]
    
    # 添加服务单元过滤
    for svc in service_units:
        cmd.extend(['-u', svc])
    
    # 添加时间过滤
    if since:
        cmd.extend(['--since', since])
    
    # 添加日志级别过滤
    cmd.extend(['-p', str(priority)])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            logs = result.stdout.strip().split('\n') if result.stdout.strip() else []
            
            return jsonify({
                "success": True,
                "logs": logs,
                "total_lines": len(logs),
                "services": service_units,
                "source": "systemd-journal"
            })
        else:
            return jsonify({
                "success": False,
                "error": result.stderr,
                "source": "systemd-journal"
            }), 500
            
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "error": "Journal query timeout"
        }), 500
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route('/journal/stream', methods=['GET'])
@bp.route('/journal/<unit>/stream', methods=['GET'])
def stream_journal_logs(unit='all'):
    """
    Server-Sent Events: 实时推送 journal 日志

    参数:
        unit: 服务名称 (api, worker, all)
        lines: 初始加载行数 (默认 50)
    """
    lines = request.args.get('lines', 50, type=int)

    # 确定服务单元
    if unit in SERVICES:
        service_units = SERVICES[unit]
        if isinstance(service_units, str):
            service_units = [service_units]
    else:
        service_units = [unit]
    
    def event_stream():
        # 构建 journalctl 命令（跟随模式）
        cmd = ['journalctl', '--no-pager', '-o', 'cat', '-n', str(lines), '-f']
        
        for svc in service_units:
            cmd.extend(['-u', svc])
        
        try:
            # 启动 journalctl 进程
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            
            for line in process.stdout:
                line = line.strip()
                if line:
                    log_entry = parse_journal_line(line)
                    yield f"data: {json.dumps(log_entry)}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            if process:
                process.terminate()
    
    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@bp.route('/journal/stats', methods=['GET'])
@bp.route('/journal/<unit>/stats', methods=['GET'])
def get_journal_stats(unit='all'):
    """获取 journal 统计信息"""
    # 确定服务单元
    if unit in SERVICES:
        service_units = SERVICES[unit]
        if isinstance(service_units, str):
            service_units = [service_units]
    else:
        service_units = [unit]
    
    stats = {
        "services": {},
        "total_lines": 0,
        "level_counts": {"ERROR": 0, "WARNING": 0, "INFO": 0, "DEBUG": 0}
    }
    
    for svc in service_units:
        try:
            # 获取该服务的日志总数
            result = subprocess.run(
                ['journalctl', '-u', svc, '--no-pager', '-o', 'cat', '-q'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0 and result.stdout:
                all_lines = result.stdout.strip().split('\n')
                total = len(all_lines)
                
                # 统计级别分布
                level_counts = {"ERROR": 0, "WARNING": 0, "INFO": 0, "DEBUG": 0}
                for line in all_lines:
                    if 'ERROR' in line or 'Failed' in line or 'error' in line:
                        level_counts["ERROR"] += 1
                    elif 'WARNING' in line or 'WARN' in line:
                        level_counts["WARNING"] += 1
                    elif 'DEBUG' in line:
                        level_counts["DEBUG"] += 1
                    else:
                        level_counts["INFO"] += 1
                
                stats["services"][svc] = {
                    "total_lines": total,
                    "level_counts": level_counts
                }
                stats["total_lines"] += total
                for level, count in level_counts.items():
                    stats["level_counts"][level] += count
                    
        except Exception as e:
            stats["services"][svc] = {"error": str(e)}
    
    return jsonify({
        "success": True,
        "stats": stats,
        "source": "systemd-journal"
    })


@bp.route('/journal/services', methods=['GET'])
def get_available_services():
    """获取可用的服务单元列表"""
    try:
        result = subprocess.run(
            ['systemctl', 'list-units', '--type=service', '--all', '--no-legend'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        services = []
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if 'hexstrike' in line.lower():
                    parts = line.split()
                    if parts:
                        services.append({
                            "name": parts[0],
                            "description": parts[1] if len(parts) > 1 else "",
                            "active": parts[2] if len(parts) > 2 else "",
                            "sub": parts[3] if len(parts) > 3 else ""
                        })
        
        return jsonify({
            "success": True,
            "services": services,
            "predefined": {
                "api": "hexstrike-ai.service (Web/API 服务)",
                "worker": "hexstrike-worker.service (Celery Worker)",
                "all": "所有 HexStrike 服务"
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route('/task/<task_id>/logs', methods=['GET'])
def get_task_logs(task_id):
    """获取指定任务的日志（从数据库）"""
    from app.models.task import TaskLog
    from app import create_app
    from app.extensions import db
    
    app = create_app()
    with app.app_context():
        try:
            logs = TaskLog.query.filter_by(task_id=task_id).order_by(TaskLog.timestamp.asc()).all()
            
            log_list = [{
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "level": log.level.value if hasattr(log.level, 'value') else str(log.level),
                "source": log.source,
                "message": log.message
            } for log in logs]
            
            return jsonify({
                "success": True,
                "task_id": task_id,
                "logs": log_list,
                "total": len(log_list)
            })
            
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500


@bp.route('/task/<task_id>/stream', methods=['GET'])
def stream_task_logs(task_id):
    """Server-Sent Events: 实时推送任务日志"""
    from app.models.task import TaskLog
    from app import create_app
    from app.extensions import db, redis_client
    import time
    
    def event_stream():
        app = create_app()
        last_timestamp = None
        
        while True:
            try:
                with app.app_context():
                    # 查询新日志
                    query = TaskLog.query.filter_by(task_id=task_id)
                    if last_timestamp:
                        query = query.filter(TaskLog.timestamp > last_timestamp)
                    
                    logs = query.order_by(TaskLog.timestamp.asc()).all()
                    
                    for log in logs:
                        log_entry = {
                            "type": "log",
                            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                            "level": log.level.value if hasattr(log.level, 'value') else str(log.level),
                            "source": log.source,
                            "message": log.message
                        }
                        yield f"data: {json.dumps(log_entry)}\n\n"
                        last_timestamp = log.timestamp
                    
                    # 检查任务是否完成
                    task = TaskLog.query.filter_by(task_id=task_id).first()
                    if not task:
                        yield f"data: {json.dumps({'type': 'error', 'message': 'Task not found'})}\n\n"
                        break
                
                time.sleep(1)
                
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                time.sleep(2)
    
    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


def parse_journal_line(line: str) -> dict:
    """解析 journal 日志行"""
    import re
    
    # systemd journal 格式：Jul 20 17:23:36 hostname service[pid]: message
    journal_pattern = r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+\S+\s+\S+\[(\d+)\]:\s+(.*)'
    match = re.match(journal_pattern, line)
    
    if match:
        timestamp_str = match.group(1)
        message = match.group(3)
        
        # 添加年份
        try:
            dt = datetime.strptime(f"2026 {timestamp_str}", "%Y %b %d %H:%M:%S")
            timestamp = dt.isoformat()
        except:
            timestamp = datetime.now().isoformat()
        
        # 检测日志级别
        level = "INFO"
        if "ERROR" in message or "error" in message or "Failed" in message or "Failed" in line:
            level = "ERROR"
        elif "WARNING" in message or "warning" in message or "WARN" in message:
            level = "WARNING"
        elif "DEBUG" in message or "debug" in message:
            level = "DEBUG"
        
        return {
            "type": "log",
            "timestamp": timestamp,
            "level": level,
            "logger": "systemd",
            "message": message
        }
    
    # 无法解析的格式
    return {
        "type": "log",
        "timestamp": datetime.now().isoformat(),
        "level": "INFO",
        "logger": "unknown",
        "message": line
    }


# ============================================================================
# 兼容旧 API（重定向到新 API）
# ============================================================================

@bp.route('/system', methods=['GET'])
def get_system_logs_compat():
    """兼容旧 API：获取系统日志（重定向到 journal）"""
    limit = request.args.get('limit', 100, type=int)
    since_line = request.args.get('since_line', 0, type=int)
    
    # 调用 journal API
    since_param = None
    if since_line > 0:
        # 简单实现：忽略 since_line，直接返回最新日志
        # 更好的实现需要记录时间戳
        pass
    
    return get_journal_logs()


@bp.route('/worker', methods=['GET'])
def get_worker_logs_compat():
    """兼容旧 API：获取 Worker 日志（重定向到 journal）"""
    limit = request.args.get('limit', 100, type=int)
    return get_journal_logs()


@bp.route('/worker/stream', methods=['GET'])
def stream_worker_logs_compat():
    """兼容旧 API：Worker 日志 SSE（重定向到 journal）"""
    return stream_journal_logs()


@bp.route('/worker/stats', methods=['GET'])
def get_worker_stats_compat():
    """兼容旧 API：Worker 统计（重定向到 journal）"""
    return get_journal_stats()
