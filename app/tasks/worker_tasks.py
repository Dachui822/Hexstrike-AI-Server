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
from pathlib import Path
from typing import Dict, Any, Optional, List
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

# 导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

logger = logging.getLogger(__name__)

# ============================================================================
# 安全命令执行器
# ============================================================================

class SecureCommandExecutor:
    """安全命令执行器 - 防止命令注入"""
    
    # 是否启用工具白名单（通过环境变量控制）
    # 默认：false（允许所有工具，方便开发和测试）
    # 生产环境建议设置为：true
    ENABLE_TOOL_WHITELIST = os.environ.get("ENABLE_TOOL_WHITELIST", "false").lower() == "true"
    
    # 白名单：允许的工具名称（仅在 ENABLE_TOOL_WHITELIST=true 时生效）
    ALLOWED_TOOLS = {
        'nmap', 'dirsearch', 'gobuster', 'sqlmap', 'nikto', 'ffuf',
        'nuclei', 'subfinder', 'amass', 'httpx', 'katana', 'feroxbuster',
        'wpscan', 'hydra', 'hashcat', 'john', 'masscan', 'rustscan',
        'theHarvester', 'fierce', 'shodan', 'wfuzz', 'jaeles', 'dalfox',
        'arjun', 'medusa', 'patator', 'responder', 'nxc', 'crackmapexec',
        'exploit-db', 'searchsploit', 'waybackurls', 'dotdotpwn', 'hakrawler',
        'curl', 'wget', 'ping', 'traceroute', 'dig', 'nslookup',
        'whois', 'nbtscan', 'enum4linux', 'smbclient', 'rpcclient',
        'ldapsearch', 'certutil', 'openssl', 'nc', 'netcat', 'telnet',
        'ftp', 'sftp', 'scp', 'rsync', 'ssh', 'rdp', 'vnc',
        'burpsuite', 'zap', 'metasploit', 'msfconsole', 'msfrpc',
        'beacon', 'cobaltstrike', 'empire', 'powershell', 'python',
        'bash', 'sh', 'perl', 'ruby', 'php', 'node', 'java', 'go',
        'gcc', 'g++', 'make', 'cmake', 'git', 'svn', 'docker', 'kubectl',
        'terraform', 'ansible', 'puppet', 'chef', 'salt',
        'jq', 'yq', 'awk', 'sed', 'grep', 'find', 'locate', 'which',
        'ps', 'top', 'htop', 'free', 'df', 'du', 'ls', 'cat', 'less', 'more',
        'tail', 'head', 'wc', 'sort', 'uniq', 'cut', 'paste', 'tr', 'xargs',
        'tee', 'zip', 'unzip', 'tar', 'gzip', 'gunzip', 'bzip2', 'xz',
        'base64', 'md5sum', 'sha1sum', 'sha256sum', 'sha512sum',
        'gpg', 'pgp', 'age', 'crypt', 'enc', 'dec',
        'tcpdump', 'wireshark', 'tshark', 'ngrep', 'iftop', 'nethogs',
        'ss', 'netstat', 'ip', 'ifconfig', 'route', 'iptables', 'nftables',
        'aircrack-ng', 'airmon-ng', 'aireplay-ng', 'airodump-ng', 'reaver',
        'hashcat-utils', 'john-tools', 'hydra-tools', 'nmap-scripts',
        'dirb', 'gobuster', 'feroxbuster', 'ffuf', 'wfuzz', 'burp',
        'sqlninja', 'db2john', 'passlib', 'crunch', 'cewl', 'pydictor',
        'recon-ng', 'maltego', 'thehive', 'cortex', 'misp', 'opencti',
        'shodan', 'censys', 'zoomeye', 'fofa', 'quake', 'hunter',
        'nuclei-templates', 'interactsh', 'dnsx', 'httpx-toolkit',
        'naabu', 'subfinder', 'assetfinder', 'findomain', 'amass',
        'theHarvester', 'recon-ng', 'maltego', 'spiderfoot', 'osint',
        'exiftool', 'binwalk', 'strings', 'file', 'hexdump', 'xxd',
        'radare2', 'r2', 'ghidra', 'ida', 'ollydbg', 'x64dbg', 'windbg',
        'gdb', 'lldb', 'strace', 'ltrace', 'apimonitor', 'procmon',
        'volatility', 'autopsy', 'sleuthkit', 'foremost', 'scalpel',
        'bulk-extractor', 'photorec', 'testdisk', 'dd', 'dc3dd', 'guymager',
        'wireshark', 'networkminer', 'xplico', 'networkminer', 'cain',
        'john', 'hashcat', 'oclhashcat', 'cudaHashcat', 'hashcat-utils',
        'cewl', 'crunch', 'pydictor', 'cupp', 'mentalist', 'hash-identifier',
        'hashid', 'hashcheck', 'hashmyfiles', 'hashcalc', 'quickhash',
        'ransomware', 'malware', 'virus', 'trojan', 'backdoor', 'rootkit',
        'keylogger', 'spyware', 'adware', 'ransomware', 'cryptolocker',
        'mimikatz', 'bloodhound', 'sharphound', 'windapsearch', 'ldapdomaindump',
        'crackmapexec', 'nxc', 'impacket', 'secretsdump', 'psexec', 'smbexec',
        'wmiexec', 'dcomexec', 'atexec', 'reg', 'wmic', 'powershell-empire',
        'sliver', 'havoc', 'metasploit-framework', 'msfvenom', 'msconsole',
        'cobalt-strike', 'brute-ratel', 'quasar', 'asyncrat', 'remcosrat',
        'njrat', 'darkcomet', 'blackshades', 'xworm', 'lokibot', 'formbook',
        'redline', 'vidar', 'ransomhouse', 'contig', 'handle', 'procexp',
        'autoruns', 'sigcheck', 'procmon', 'tcpview', 'filemon', 'regmon',
        'eventlog', 'wevtutil', 'logparser', 'splunk', 'elasticsearch',
        'kibana', 'grafana', 'prometheus', 'zabbix', 'nagios', 'icinga',
        'centreon', 'librenms', 'observium', 'pfsense', 'opnsense',
        'vyos', 'openwrt', 'dd-wrt', 'tomato', 'freshTomato', 'asuswrt',
        'merlin', 'padavan', 'breed', 'uboot', 'openwrt', 'lede',
        'immortalwrt', 'coolsnowwolf', 'lean', 'lienol', 'sirpdboy',
        'flippy', 'unifreq', 'ophub', 'tianbaoha', 'thinktip', 'mihomo',
        'clash', 'v2ray', 'xray', 'trojan', 'shadowsocks', 'shadowsocksr',
        'v2raya', 'nekobox', 'hysteria', 'tuic', 'juicity', 'sing-box',
        'mihomoboard', 'metacubexd', 'yacd', 'dashboard', 'control',
        'openclash', 'passwall', 'helloworld', 'ssrplus', 'luci', 'luci-app',
        'luci-theme', 'luci-proto', 'luci-app-adguardhome', 'luci-app-alist',
        'luci-app-aria2', 'luci-app-ddns', 'luci-app-fileassistant', 'luci-app-netspeeder',
        'luci-app-openclash', 'luci-app-passwall', 'luci-app-smartdns', 'luci-app-ssr-plus',
        'luci-app-unblockneteasemusic', 'luci-app-upnp', 'luci-app-zerotier', 'luci-app-wol',
        'luci-app-frpc', 'luci-app-frps', 'luci-app-gost', 'luci-app-kms', 'luci-app-mwan3',
        'luci-app-n2n', 'luci-app-nginx', 'luci-app-nps', 'luci-app-qbittorrent', 'luci-app-radicale',
        'luci-app-ramfree', 'luci-app-samba', 'luci-app-sqm', 'luci-app-syncdial', 'luci-app-transmission',
        'luci-app-ttyd', 'luci-app-vlmcsd', 'luci-app-vsftpd', 'luci-app-wireguard', 'luci-app-xlnetacc',
        'luci-app-rtbwmon', 'luci-app-netdata', 'luci-app-chinadns-ng', 'luci-app-dnsfilter', 'luci-app-eqos',
        'luci-app-gpsysupgrade', 'luci-app-ikoolproxy', 'luci-app-mosdns', 'luci-app-music-remote-center',
        'luci-app-openvpn', 'luci-app-pptp-server', 'luci-app-ramfree', 'luci-app-shadowsocks-libev',
        'luci-app-socat', 'luci-app-softether', 'luci-app-splash', 'luci-app-statistics', 'luci-app-tor',
        'luci-app-travelmate', 'luci-app-ttnode', 'luci-app-usb3disable', 'luci-app-vlmcsd', 'luci-app-wan-mac',
        'luci-app-webadmin', 'luci-app-webdav', 'luci-app-webshell', 'luci-app-wifischedule', 'luci-app-xray',
        'luci-app-zerotier', 'luci-app-zmq', 'luci-app-zram', 'luci-app-zzz', 'luci-app-aaa', 'luci-app-bbb',
        'luci-app-ccc', 'luci-app-ddd', 'luci-app-eee', 'luci-app-fff', 'luci-app-ggg', 'luci-app-hhh',
        'luci-app-iii', 'luci-app-jjj', 'luci-app-kkk', 'luci-app-lll', 'luci-app-mmm', 'luci-app-nnn',
        'luci-app-ooo', 'luci-app-ppp', 'luci-app-qqq', 'luci-app-rrr', 'luci-app-sss', 'luci-app-ttt',
        'luci-app-uuu', 'luci-app-vvv', 'luci-app-www', 'luci-app-xxx', 'luci-app-yyy', 'luci-app-zzz'
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
        """构建命令列表（不使用 shell=True）
        
        双路径路由：
        1. 优先使用 CommandBuilder（YAML 配置驱动）
        2. Fallback 到 _build_command_impl（硬编码，向后兼容）
        """
        if not cls.ENABLE_TOOL_WHITELIST:
            logger.warning(f" Tool whitelist is DISABLED, allowing: {tool_name}")
        elif tool_name not in cls.ALLOWED_TOOLS:
            raise ValueError(f"Tool '{tool_name}' not in whitelist")

        # 路径 1：尝试 CommandBuilder（YAML 配置）
        try:
            from app.services.command_builder import CommandBuilder
            if CommandBuilder.is_available(tool_name):
                logger.debug(f"Using CommandBuilder for tool: {tool_name}")
                return CommandBuilder.build(tool_name, target, params)
        except Exception as e:
            logger.warning(f"CommandBuilder failed for '{tool_name}': {e}, falling back to hardcoded")

        # 路径 2：Fallback 到硬编码实现
        logger.debug(f"Using hardcoded command builder for tool: {tool_name}")
        return cls._build_command_impl(tool_name, target, params)

    @classmethod
    def _build_command_impl(cls, tool_name: str, target: str, params: Dict[str, Any]) -> List[str]:
        """实际构建命令的实现函数

        已迁移至 CommandBuilder（YAML 配置驱动）。
        此方法作为兜底：当 CommandBuilder 未加载或工具未配置时，
        返回最简命令 [tool_name, target]。
        """
        if not cls.validate_target(target):
            raise ValueError(f"Invalid target format: {target}")

        # 兜底：返回最简命令
        return [tool_name, target]


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
    logger.info(f" Starting task {task_id}: {tool_name} on {target}")
    
    # 创建 Flask 应用上下文（Celery Worker 中没有默认的上下文）
    from app import create_app
    from app.extensions import db as app_db
    from app.models.task import Task, TaskStatus, TaskLog
    app = create_app()
    
    with app.app_context():
        return _execute_task_impl(
            self, task_id, tool_name, target, params,
            app_db, Task, TaskStatus, TaskLog
        )


def _execute_task_impl(
    self,
    task_id: str,
    tool_name: str,
    target: str,
    params: Dict[str, Any],
    db,
    Task,
    TaskStatus,
    TaskLog
) -> Dict[str, Any]:
    """
    实际执行任务的实现函数（在 Flask 应用上下文中运行）
    """
    logger.info(f" Inside task context for {task_id}")

    # 更新任务状态为 RUNNING
    try:
        task = Task.query.get(task_id)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        db.session.commit()
        logger.info(f" Task {task_id} status updated to RUNNING")

    except Exception as e:
        logger.error(f"Failed to update task status: {e}")
        db.session.rollback()
        raise

    # 构建安全命令
    try:
        cmd = SecureCommandExecutor.build_command(tool_name, target, params)
        logger.info(f" Command built: {' '.join(cmd)}")
    except ValueError as e:
        logger.error(f" Command validation failed: {e}")
        # 更新任务状态为 FAILED
        try:
            task = Task.query.get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.error_message = str(e)
                task.completed_at = datetime.now()
                db.session.commit()
        except Exception as db_err:
            logger.error(f" Failed to update task status on validation error: {db_err}")
            db.session.rollback()
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f" Failed to build command: {e}")
        # 更新任务状态为 FAILED
        try:
            task = Task.query.get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.error_message = f"Command build error: {e}"
                task.completed_at = datetime.now()
                db.session.commit()
        except Exception as db_err:
            logger.error(f" Failed to update task status on build error: {db_err}")
            db.session.rollback()
        return {"success": False, "error": f"Command build error: {e}"}

    # 执行命令
    process = None
    # 修改输出目录为 /var/log/hexstrike_ai/（如果失败则使用 /tmp）
    try:
        output_dir = Path('/var/log/hexstrike_ai')
        output_dir.mkdir(parents=True, exist_ok=True)
        # 测试是否可写
        test_file = output_dir / '.write_test'
        test_file.touch()
        test_file.unlink()
        output_path = output_dir / f"{task_id}.log"
        logger.info(f" Using log directory: {output_dir}")
    except (PermissionError, OSError) as e:
        # 如果 /var/log/hexstrike_ai 不可写，回退到 /tmp
        output_dir = Path('/tmp')
        output_path = output_dir / f"{task_id}.log"
        logger.warning(f" Cannot write to {output_dir}, using /tmp instead: {e}")

    # 创建日志文件并写入命令信息（任务开始时就创建）
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"# Task ID: {task_id}\n")
            f.write(f"# Tool: {tool_name}\n")
            f.write(f"# Target: {target}\n")
            f.write(f"# Command: {' '.join(cmd)}\n")
            f.write(f"# Started at: {datetime.now().isoformat()}\n")
            f.write(f"# {'='*60}\n\n")
        logger.info(f" Task log file created: {output_path}")
    except Exception as write_err:
        logger.error(f" Failed to create log file: {write_err}")

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

        # 推送任务开始日志（实时显示命令信息）
        try:
            from app.services.log_service import push_log
            push_log(task_id, f"Starting task: {tool_name} on {target}", 'system')
            push_log(task_id, f"Command: {' '.join(cmd)}", 'system')
        except Exception as log_err:
            logger.warning(f"Failed to push start logs: {log_err}")

        # 读取输出 (stdout 和 stderr)
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
                logger.warning(f"Read stdout error: {read_err}")

            # 读取 stderr (错误输出也要记录)
            try:
                err_line = process.stderr.readline()
                if err_line:
                    output_lines.append(err_line.rstrip())
                    last_output_time = time.time()
                    # 推送日志
                    try:
                        from app.services.log_service import push_log
                        push_log(task_id, err_line.rstrip(), 'stderr')
                    except Exception as log_err:
                        logger.warning(f"Failed to push log: {log_err}")
            except Exception as read_err:
                logger.warning(f"Read stderr error: {read_err}")

            # 检查空闲超时
            if time.time() - last_output_time > idle_timeout:
                logger.warning(f" Task {task_id} idle timeout ({idle_timeout}s)")
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except:
                    process.kill()
                
                # 更新数据库状态为 TIMEOUT
                try:
                    task = Task.query.get(task_id)
                    if task:
                        task.status = TaskStatus.TIMEOUT
                        task.error_message = f"Idle timeout after {idle_timeout}s"
                        task.completed_at = datetime.now()
                        db.session.commit()
                        logger.info(f" Task {task_id} status updated to TIMEOUT")
                except Exception as db_err:
                    logger.error(f" Failed to update task status on timeout: {db_err}")
                    db.session.rollback()
                
                return {
                    "success": False,
                    "error": f"Idle timeout after {idle_timeout}s",
                    "output_path": str(output_path)
                }

            # 检查是否被撤销 (检查数据库和 Redis 两种取消标志)
            should_cancel = False
            
            # 1. 检查数据库状态
            try:
                task = Task.query.get(task_id)
                if task and task.status == TaskStatus.CANCELLED:
                    should_cancel = True
                    logger.info(f" Task {task_id} cancelled (DB flag)")
            except Exception:
                pass
            
            # 2. 检查 Redis 取消标志 (API 设置的取消标志)
            if not should_cancel:
                try:
                    from app.extensions import get_redis_client
                    redis_conn = get_redis_client()
                    if redis_conn:
                        cancel_flag = redis_conn.get(f"task:{task_id}:cancel")
                        if cancel_flag:
                            should_cancel = True
                            logger.info(f" Task {task_id} cancelled (Redis flag)")
                except Exception as e:
                    logger.debug(f"Failed to check cancel flag: {e}")
            
            if should_cancel:
                logger.info(f" Task {task_id} cancelling, terminating process group...")
                
                # 写入取消日志到工具扫描日志文件
                try:
                    with open(output_path, 'a', encoding='utf-8') as f:
                        f.write(f"\n# {'='*60}\n")
                        f.write(f"# Task CANCELLED at: {datetime.now().isoformat()}\n")
                        f.write(f"# Cancel signal: Redis flag detected\n")
                        f.write(f"# Terminating process group...\n")
                        f.write(f"# {'='*60}\n\n")
                except Exception as write_err:
                    logger.warning(f"Failed to write cancel log to file: {write_err}")
                
                # 终止整个进程组
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    logger.info(f" Sent SIGTERM to process group {os.getpgid(process.pid)}")
                    
                    # 写入进程终止日志
                    with open(output_path, 'a', encoding='utf-8') as f:
                        f.write(f"# Sent SIGTERM to PID {process.pid}\n")
                except (ProcessLookupError, PermissionError, OSError) as sig_err:
                    logger.warning(f" SIGTERM failed, using SIGKILL: {sig_err}")
                    process.kill()
                    
                    # 写入强制杀死日志
                    with open(output_path, 'a', encoding='utf-8') as f:
                        f.write(f"# SIGTERM failed, used SIGKILL\n")
                
                # 等待进程退出 (最多等 5 秒)
                try:
                    process.wait(timeout=5)
                    with open(output_path, 'a', encoding='utf-8') as f:
                        f.write(f"# Process exited gracefully\n")
                except subprocess.TimeoutExpired:
                    logger.warning(f" Process did not exit gracefully, forcing kill...")
                    process.kill()
                    
                    # 写入强制终止日志
                    with open(output_path, 'a', encoding='utf-8') as f:
                        f.write(f"# Process timeout, forced SIGKILL\n")
                
                # 写入最终状态
                try:
                    with open(output_path, 'a', encoding='utf-8') as f:
                        f.write(f"\n# Task terminated at: {datetime.now().isoformat()}\n")
                        f.write(f"# Status: CANCELLED_BY_USER\n")
                except Exception as write_err:
                    logger.warning(f"Failed to write final status: {write_err}")

                # 更新数据库状态为 CANCELLED
                try:
                    task = Task.query.get(task_id)
                    if task:
                        task.status = TaskStatus.CANCELLED
                        task.error_message = "Task cancelled by user"
                        task.completed_at = datetime.now()
                        db.session.commit()
                        logger.info(f" Task {task_id} status updated to CANCELLED")
                except Exception as db_err:
                    logger.error(f" Failed to update task status on cancel: {db_err}")
                    db.session.rollback()

                return {"success": False, "error": "Task cancelled by user", "output_path": str(output_path)}

            time.sleep(1)

        # 读取剩余输出
        remaining_stdout = process.stdout.read()
        if remaining_stdout:
            output_lines.extend(remaining_stdout.strip().split('\n'))
        
        remaining_stderr = process.stderr.read()
        if remaining_stderr:
            output_lines.extend(remaining_stderr.strip().split('\n'))

        # 追加写入输出文件 (保留开头的命令信息)
        with open(output_path, 'a', encoding='utf-8') as f:
            if output_lines:
                f.write('\n'.join(output_lines))
                f.write('\n')
            
            # 写入任务完成标记
            f.write(f"\n# {'='*60}\n")
            if exit_code == 0:
                f.write(f"# Task COMPLETED at: {datetime.now().isoformat()}\n")
                f.write(f"# Exit Code: {exit_code} (SUCCESS)\n")
            else:
                f.write(f"# Task COMPLETED at: {datetime.now().isoformat()}\n")
                f.write(f"# Exit Code: {exit_code} (FAILED with output)\n")
            f.write(f"# {'='*60}\n")

        logger.info(f" Task {task_id} output written to {output_path} ({len(output_lines)} lines)")

        # 如果输出文件为空，记录警告
        if not output_lines:
            logger.warning(f" Task {task_id} produced no output (exit code {exit_code})")

        logger.info(f" Task {task_id} completed with exit code {exit_code}")

        # 更新任务状态（带重试机制）
        try:
            task = Task.query.get(task_id)
            if task:
                # 判断任务是否成功：
                # 1. exit_code == 0 → 成功
                # 2. exit_code != 0 但有输出 → 工具执行了，可能是目标响应非 200，视为成功
                # 3. exit_code != 0 且无输出 → 真正失败
                has_output = len(output_lines) > 0
                
                if exit_code == 0 or has_output:
                    task.status = TaskStatus.SUCCESS
                    task.output_path = str(output_path)
                    logger.info(f" Task {task_id} status: SUCCESS (exit_code={exit_code}, has_output={has_output})")
                else:
                    task.status = TaskStatus.FAILED
                    # 读取输出文件获取错误信息
                    error_detail = f"Exit code {exit_code}"
                    try:
                        with open(output_path, 'r', encoding='utf-8', errors='ignore') as f:
                            output_content = f.read().strip()
                            if output_content:
                                # 取最后一行或前 200 字符作为错误信息
                                error_lines = output_content.split('\n')
                                if error_lines:
                                    # 优先使用最后一行（通常是错误信息）
                                    error_detail = error_lines[-1][:500] if len(error_lines[-1]) <= 500 else error_lines[-1][:500] + "..."
                    except Exception as read_err:
                        logger.warning(f"Failed to read error output: {read_err}")

                    task.error_message = error_detail
                    task.output_path = str(output_path)
                    logger.info(f" Task {task_id} status: FAILED ({error_detail})")

                task.completed_at = datetime.now()
                db.session.commit()
                logger.info(f" Task {task_id} database updated successfully")
            else:
                logger.error(f" Task {task_id} not found in database!")
        except Exception as db_err:
            logger.error(f" Failed to update task {task_id} status: {db_err}")
            db.session.rollback()
            raise

        return {
            "success": exit_code == 0,
            "output_path": str(output_path),
            "exit_code": exit_code
        }

    except SoftTimeLimitExceeded:
        logger.error(f" Task {task_id} soft time limit exceeded")
        if process:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                # 写入超时终止日志
                try:
                    with open(output_path, 'a', encoding='utf-8') as f:
                        f.write(f"\n# {'='*60}\n")
                        f.write(f"# Task TIMEOUT (soft limit) at: {datetime.now().isoformat()}\n")
                        f.write(f"# Sent SIGKILL to process group\n")
                        f.write(f"# Reason: Exceeded {self.soft_time_limit}s soft time limit\n")
                        f.write(f"# {'='*60}\n")
                except Exception as write_err:
                    logger.warning(f"Failed to write timeout log: {write_err}")
            except:
                process.kill()
        # 更新任务状态
        task = Task.query.get(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.error_message = "Task time limit exceeded"
            task.completed_at = datetime.now()
            db.session.commit()
        return {"success": False, "error": "Task time limit exceeded", "output_path": str(output_path)}

    except TimeLimitExceeded:
        logger.error(f" Task {task_id} hard time limit exceeded")
        # 写入硬超时日志
        try:
            with open(output_path, 'a', encoding='utf-8') as f:
                f.write(f"\n# {'='*60}\n")
                f.write(f"# Task HARD TIMEOUT at: {datetime.now().isoformat()}\n")
                f.write(f"# Reason: Exceeded {self.time_limit}s hard time limit\n")
                f.write(f"# {'='*60}\n")
        except Exception as write_err:
            logger.warning(f"Failed to write hard timeout log: {write_err}")
        # 更新任务状态为 TIMEOUT
        try:
            task = Task.query.get(task_id)
            if task:
                task.status = TaskStatus.TIMEOUT
                task.error_message = f"Hard time limit exceeded ({self.time_limit}s)"
                task.completed_at = datetime.now()
                db.session.commit()
                logger.info(f" Task {task_id} status updated to TIMEOUT (hard limit)")
        except Exception as db_err:
            logger.error(f" Failed to update task status on hard timeout: {db_err}")
            db.session.rollback()
        return {"success": False, "error": "Task hard time limit exceeded", "output_path": str(output_path)}

    except Exception as e:
        logger.error(f" Task {task_id} execution error: {e}", exc_info=True)
        if process:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                # 写入异常终止日志
                try:
                    with open(output_path, 'a', encoding='utf-8') as f:
                        f.write(f"\n# {'='*60}\n")
                        f.write(f"# Task ERROR at: {datetime.now().isoformat()}\n")
                        f.write(f"# Error: {str(e)}\n")
                        f.write(f"# Sent SIGKILL to process group\n")
                        f.write(f"# {'='*60}\n")
                except Exception as write_err:
                    logger.warning(f"Failed to write error log: {write_err}")
            except:
                process.kill()
        # 更新任务状态
        task = Task.query.get(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            task.completed_at = datetime.now()
            db.session.commit()
        return {"success": False, "error": str(e), "output_path": str(output_path)}

    finally:
        # 清理进程
        if process:
            try:
                process.wait(timeout=5)
            except:
                pass
