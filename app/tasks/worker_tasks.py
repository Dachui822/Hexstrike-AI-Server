"""
Celery 任务定义 - 安全工具执行器
架构说明：
- 所有耗时任务通过 Celery 异步执行
- 支持任务重试、超时控制、进度跟踪
- 与 Web 服务完全解耦
"""

import os
import sys
import time
import signal
import logging
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional, List
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

# 导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.extensions import db
from app.models.task import Task, TaskStatus, TaskLog
from app.models.tool import Tool

logger = logging.getLogger(__name__)

# ============================================================================
# 安全命令执行器
# ============================================================================

class SecureCommandExecutor:
    """安全命令执行器 - 防止命令注入"""
    
    # 白名单：允许的工具名称
    ALLOWED_TOOLS = {
        'nmap', 'dirsearch', 'gobuster', 'sqlmap', 'nikto', 'ffuf',
        'nuclei', 'subfinder', 'amass', 'httpx', 'katana', 'feroxbuster',
        'wpscan', 'hydra', 'hashcat', 'john', 'masscan', 'rustscan',
        'theHarvester', 'fierce', 'shodan', 'wfuzz', 'jaeles', 'dalfox',
        'arjun', 'medusa', 'patator', 'responder', 'nxc', 'crackmapexec',
        'exploit-db', 'searchsploit', 'waybackurls', 'dotdotpwn', 'hakrawler'
    }
    
    # 参数白名单映射（工具名 -> 允许的参数名）
    ALLOWED_PARAMS = {
        'nmap': {'scan_type', 'ports', 'additional_args'},
        'dirsearch': {'extensions', 'wordlist', 'threads', 'recursive', 'additional_args'},
        'gobuster': {'mode', 'wordlist', 'threads', 'extensions', 'recursive', 'status_codes', 'method', 'additional_args'},
        'sqlmap': {'level', 'risk', 'additional_args'},
        'nikto': {'port', 'ssl', 'timeout', 'useragent', 'tuning', 'output', 'format', 'vhost', 'id', 'additional_args'},
        'ffuf': {'wordlist', 'method', 'headers', 'additional_args'},
        'nuclei': {'templates', 'severity', 'tags', 'additional_args'},
        'subfinder': {'sources', 'recursive', 'additional_args'},
        'amass': {'mode', 'sources', 'additional_args'},
        'httpx': {'status_code', 'title', 'content_length', 'server', 'additional_args'},
        'katana': {'depth', 'scope', 'additional_args'},
        'feroxbuster': {'wordlist', 'threads', 'depth', 'additional_args'},
        'wpscan': {'api_token', 'enumerate', 'plugins_detection', 'additional_args'},
        'hydra': {'service', 'username', 'wordlist', 'threads', 'timeout', 'additional_args'},
        'hashcat': {'hash_type', 'wordlist', 'force', 'additional_args'},
        'john': {'wordlist', 'format', 'additional_args'},
        'masscan': {'ports', 'rate', 'additional_args'},
        'rustscan': {'ports', 'top', 'additional_args'},
        'theHarvester': {'source', 'limit', 'additional_args'},
        'fierce': {'threads', 'additional_args'},
        'shodan': {'limit', 'additional_args'},
        'wfuzz': {'wordlist', 'hc', 'additional_args'},
        'jaeles': {'templates', 'additional_args'},
        'dalfox': {'blind', 'additional_args'},
        'arjun': {'stable', 'additional_args'},
        'medusa': {'service', 'username', 'wordlist', 'threads', 'additional_args'},
        'patator': {'module', 'user', 'password', 'additional_args'},
        'responder': {'interface', 'analyze', 'additional_args'},
        'nxc': {'protocol', 'username', 'password', 'additional_args'},
        'crackmapexec': {'protocol', 'username', 'password', 'additional_args'},
        'exploit-db': {'additional_args'},
        'searchsploit': {'additional_args'},
        'waybackurls': {'additional_args'},
        'dotdotpwn': {'additional_args'},
        'hakrawler': {'depth', 'additional_args'},
    }
    
    # IP/域名验证正则
    import re
    IP_PATTERN = re.compile(r'^(\d{1,3}\.){3}\d{1,3}(:\d+)?$')
    DOMAIN_PATTERN = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$')
    URL_PATTERN = re.compile(r'^https?://[^\s/$.?#].[^\s]*$')
    
    @classmethod
    def validate_target(cls, target: str) -> bool:
        """验证目标参数（防止命令注入）"""
        if not target or not isinstance(target, str):
            return False
        
        # 检查是否包含危险字符
        dangerous_chars = [';', '|', '&', '$', '`', '(', ')', '{', '}', '[', ']', '<', '>', '\n', '\r']
        if any(char in target for char in dangerous_chars):
            return False
        
        # 验证格式（IP、域名或 URL）
        return bool(cls.IP_PATTERN.match(target) or cls.DOMAIN_PATTERN.match(target) or cls.URL_PATTERN.match(target))
    
    @classmethod
    def build_command(cls, tool_name: str, target: str, params: Dict[str, Any]) -> List[str]:
        """构建命令列表（不使用 shell=True）"""
        if tool_name not in cls.ALLOWED_TOOLS:
            raise ValueError(f"Tool '{tool_name}' not in whitelist")
        
        if not cls.validate_target(target):
            raise ValueError(f"Invalid target format: {target}")
        
        allowed_params = cls.ALLOWED_PARAMS.get(tool_name, set())
        
        # 基础命令
        cmd = [tool_name]
        
        # 特殊工具处理
        if tool_name == 'dirsearch':
            cmd = ['dirsearch', '-u', target]
            if 'extensions' in params:
                cmd.extend(['-e', str(params['extensions'])])
            if 'wordlist' in params:
                cmd.extend(['-w', str(params['wordlist'])])
            if 'threads' in params:
                cmd.extend(['-t', str(params['threads'])])
            if params.get('recursive'):
                cmd.append('-r')
                
        elif tool_name == 'gobuster':
            mode = params.get('mode', 'dir')
            cmd = ['gobuster', mode, '-u', target]
            if 'wordlist' in params:
                cmd.extend(['-w', str(params['wordlist'])])
            if 'threads' in params:
                cmd.extend(['-t', str(params['threads'])])
            if 'extensions' in params:
                cmd.extend(['-x', str(params['extensions'])])
            if params.get('recursive'):
                cmd.append('-r')
                
        elif tool_name == 'nmap':
            cmd = ['nmap', target]
            if 'scan_type' in params:
                cmd.append(str(params['scan_type']))
            if 'ports' in params:
                cmd.extend(['-p', str(params['ports'])])
                
        elif tool_name == 'sqlmap':
            cmd = ['sqlmap', '-u', target]
            if 'level' in params:
                cmd.extend(['--level', str(params['level'])])
            if 'risk' in params:
                cmd.extend(['--risk', str(params['risk'])])
                
        elif tool_name == 'nikto':
            cmd = ['nikto', '-host', target]
            if 'port' in params:
                cmd.extend(['-port', str(params['port'])])
            if params.get('ssl'):
                cmd.append('-ssl')
                
        elif tool_name == 'ffuf':
            cmd = ['ffuf', '-u', target]
            if 'wordlist' in params:
                cmd.extend(['-w', str(params['wordlist'])])
            if 'method' in params:
                cmd.extend(['-X', str(params['method'])])
                
        elif tool_name == 'nuclei':
            cmd = ['nuclei', '-target', target]
            if 'templates' in params:
                cmd.extend(['-t', str(params['templates'])])
            if 'severity' in params:
                cmd.extend(['-severity', str(params['severity'])])
        
        # 通用参数处理
        if 'additional_args' in params:
            # 安全解析额外参数
            additional = str(params['additional_args'])
            # 简单分割（不处理引号）
            for arg in additional.split():
                if arg and not any(c in arg for c in [';', '|', '&', '$', '`']):
                    cmd.append(arg)
        
        return cmd


# ============================================================================
# Celery 任务
# ============================================================================

@shared_task(
    bind=True,
    name='app.tasks.execute_tool_task',
    queue='hexstrike_default',
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    default_retry_delay=60,
    time_limit=3600,
    soft_time_limit=3300,
)
def execute_tool_task(
    self,
    task_id: str,
    tool_name: str,
    target: str,
    params: Dict[str, Any]
) -> Dict[str, Any]:
    """
    执行安全工具任务
    
    Args:
        task_id: 任务 ID（UUID）
        tool_name: 工具名称
        target: 目标地址
        params: 工具参数
    
    Returns:
        执行结果字典
    """
    from flask import current_app
    
    logger.info(f" Starting task {task_id}: {tool_name} on {target}")
    
    # 更新任务状态为 RUNNING
    try:
        task = Task.query.get(task_id)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}
        
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        db.session.commit()
        logger.info(f"📝 Task {task_id} status updated to RUNNING")
        
    except Exception as e:
        logger.error(f"Failed to update task status: {e}")
        db.session.rollback()
        raise
    
    # 构建安全命令
    try:
        cmd = SecureCommandExecutor.build_command(tool_name, target, params)
        logger.info(f"🔧 Command built: {' '.join(cmd)}")
    except ValueError as e:
        logger.error(f"❌ Command validation failed: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"❌ Failed to build command: {e}")
        return {"success": False, "error": f"Command build error: {e}"}
    
    # 执行命令
    process = None
    output_path = f"/tmp/{task_id}.log"
    
    try:
        # 启动进程
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
        
        logger.info(f" Process {process.pid} started for task {task_id}")
        
        # 读取输出
        output_lines = []
        start_time = time.time()
        last_output_time = start_time
        idle_timeout = int(os.environ.get("IDLE_TIMEOUT", 300))
        
        while True:
            # 检查进程是否结束
            exit_code = process.poll()
            if exit_code is not None:
                break
            
            # 读取 stdout
            try:
                line = process.stdout.readline()
                if line:
                    output_lines.append(line.rstrip())
                    last_output_time = time.time()
                    # 推送日志
                    try:
                        from app.services.log_service import push_log
                        push_log(task_id, line.rstrip(), 'stdout')
                    except Exception as log_err:
                        logger.warning(f"Failed to push log: {log_err}")
            except Exception as read_err:
                logger.warning(f"Read error: {read_err}")
            
            # 检查空闲超时
            if time.time() - last_output_time > idle_timeout:
                logger.warning(f" Task {task_id} idle timeout ({idle_timeout}s)")
                process.kill()
                return {
                    "success": False,
                    "error": f"Idle timeout after {idle_timeout}s",
                    "output_path": output_path
                }
            
            # 检查是否被撤销
            if self.request.id and Task.query.get(task_id):
                task = Task.query.get(task_id)
                if task.status == TaskStatus.CANCELLED:
                    logger.info(f"🛑 Task {task_id} cancelled")
                    process.kill()
                    return {"success": False, "error": "Task cancelled by user"}
            
            time.sleep(1)
        
        # 读取剩余输出
        remaining = process.stdout.read()
        if remaining:
            output_lines.extend(remaining.strip().split('\n'))
        
        # 写入输出文件
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_lines))
        
        logger.info(f"✅ Task {task_id} completed with exit code {exit_code}")
        
        return {
            "success": exit_code == 0,
            "output_path": output_path,
            "exit_code": exit_code
        }
        
    except SoftTimeLimitExceeded:
        logger.error(f"⏰ Task {task_id} soft time limit exceeded")
        if process:
            process.kill()
        return {"success": False, "error": "Task time limit exceeded"}
        
    except TimeLimitExceeded:
        logger.error(f"🔥 Task {task_id} hard time limit exceeded")
        return {"success": False, "error": "Task hard time limit exceeded"}
        
    except Exception as e:
        logger.error(f"❌ Task {task_id} execution error: {e}", exc_info=True)
        if process and process.poll() is None:
            process.kill()
        return {"success": False, "error": str(e)}
        
    finally:
        # 清理
        if process:
            try:
                process.wait(timeout=5)
            except:
                pass
