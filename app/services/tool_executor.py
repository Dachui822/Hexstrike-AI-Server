import subprocess
import logging
import time
import os
import threading
import signal
from app.extensions import db
import app.extensions as extensions
from app.models.task import TaskLog, Task
from app.models.tool import Tool

logger = logging.getLogger(__name__)

# 全局字典：跟踪活跃任务
_active_tasks = {}


class _OutputReader:
    """非阻塞输出读取器：使用线程异步读取 stdout/stderr"""

    def __init__(self, pipe, source: str, task_id: str, output_file, push_log_fn, update_progress_fn):
        self.pipe = pipe
        self.source = source
        self.task_id = task_id
        self.output_file = output_file
        self.push_log = push_log_fn
        self.update_progress = update_progress_fn
        self.lines = []
        self._thread = None
        self._stop_event = threading.Event()
        self.last_output_time = time.time()  # 记录最后输出时间

    def start(self):
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        """后台线程：逐行读取输出"""
        try:
            for line in iter(self.pipe.readline, ''):
                if self._stop_event.is_set():
                    break
                line = line.rstrip('\n')
                if line:
                    self.last_output_time = time.time()  # 更新最后输出时间
                    self.lines.append(line)
                    if self.output_file:
                        self.output_file.write(line + '\n')
                        self.output_file.flush()
                    self.push_log(self.task_id, line, self.source)
                    self.update_progress(self.task_id, None)
        except Exception as e:
            logger.error(f"Output reader error [{self.source}]: {e}")
            self.push_log(self.task_id, f"[Reader Error] {e}", 'stderr')

    def stop(self):
        """停止读取线程"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def drain_remaining(self):
        """读取剩余输出"""
        try:
            remaining = self.pipe.read()
            if remaining:
                for line in remaining.strip().split('\n'):
                    if line:
                        self.lines.append(line)
                        if self.output_file:
                            self.output_file.write(line + '\n')
                            self.output_file.flush()
                        self.push_log(self.task_id, line, self.source)
        except Exception:
            pass

    def get_idle_seconds(self):
        """获取空闲时间（秒）"""
        return time.time() - self.last_output_time

class ToolExecutor:
    def run(self, task_id: str, tool_name: str, target: str, params: dict) -> dict:
        """执行工具命令"""
        from app import create_app
        app = create_app()
        with app.app_context():
            # 兼容处理：MCP 客户端可能传递 url/domain/hash 等参数名而非 target
            # 如果 target 为空，尝试从 params 中获取
            if not target and params:
                for alt_param in ['url', 'domain', 'hash', 'query', 'username', 'file', 'path']:
                    if alt_param in params:
                        target = params.pop(alt_param)
                        logger.info(f"[DEBUG] Mapped {alt_param} to target: {repr(target)}")
                        break
            
            # 如果仍然没有 target，尝试从常见参数中获取
            if not target:
                # 某些工具可能直接传递了目标参数但名称不同
                target_aliases = ['host', 'ip', 'input', 'filepath']
                for alias in target_aliases:
                    if alias in params:
                        target = params.pop(alias)
                        logger.info(f"[DEBUG] Mapped {alias} to target: {repr(target)}")
                        break
            
            # 调试日志：记录接收到的参数
            logger.info(f"[DEBUG] run() called: task_id={task_id}, tool_name={tool_name}, target={repr(target)}, params={params}")
            
            # 1. 匹配 MCP 服务端工具状态 (前置校验)
            tool = db.session.get(Tool, tool_name)
            if not tool:
                return {"success": False, "error": f"Tool '{tool_name}' not registered in database."}

            if not tool.is_available:
                logger.warning(f"⚠️ Tool '{tool_name}' is marked as unavailable. Attempting execution anyway...")

            # 2. 过滤无效参数，保留执行参数
            meta_params = {'async', 'priority', 'use_recovery'}
            valid_params = {k: v for k, v in params.items() if k not in meta_params}

            # 3. 构建命令 (安全拼接，兼容短参数如 -e, -t, -sV)
            # 针对特定工具的参数格式进行适配 (修复 dirsearch 等工具缺少 -u 参数的问题)

            # 默认命令基础
            cmd = f"{tool_name} {target}"

            # 特殊处理 dirsearch (需要 -u)
            if tool_name == 'dirsearch':
                # 确保 target 不为空
                if not target or target.strip() == '':
                    return {"success": False, "error": "URL target is missing for dirsearch. Please provide a valid URL."}
                cmd = f"dirsearch -u {target}"
                if 'extensions' in valid_params:
                    cmd += f" -e {valid_params.pop('extensions')}"
                if 'wordlist' in valid_params:
                    cmd += f" -w {valid_params.pop('wordlist')}"
                if 'threads' in valid_params:
                    cmd += f" -t {valid_params.pop('threads')}"
                if 'recursive' in valid_params and valid_params.pop('recursive') in [True, 'true', '1']:
                    cmd += " -r"
            
            # 特殊处理 gobuster (需要 mode 子命令和 -u)
            # 命令格式：gobuster <mode> -u <url> [options]
            # mode: dir, dns, vhost, fuzz, tftp, s3, gcs
            elif tool_name == 'gobuster':
                mode = valid_params.pop('mode', 'dir')
                cmd = f"gobuster {mode} -u {target}"
                if 'wordlist' in valid_params:
                    cmd += f" -w {valid_params.pop('wordlist')}"
                if 'threads' in valid_params:
                    cmd += f" -t {valid_params.pop('threads')}"
                if 'extensions' in valid_params:
                    cmd += f" -x {valid_params.pop('extensions')}"
                if 'recursive' in valid_params and valid_params.pop('recursive') in [True, 'true', '1']:
                    cmd += " -r"
                if 'status_codes' in valid_params:
                    cmd += f" -s {valid_params.pop('status_codes')}"
                if 'method' in valid_params:
                    cmd += f" -m {valid_params.pop('method')}"

            # 特殊处理 subfinder (需要 -d domain)
            elif tool_name == 'subfinder':
                cmd = f"subfinder -d {target}"
                if 'sources' in valid_params:
                    cmd += f" -s {valid_params.pop('sources')}"
                if 'recursive' in valid_params and valid_params.pop('recursive') in [True, 'true', '1']:
                    cmd += " -recursive"

            # 特殊处理 amass (需要 enum -domain)
            elif tool_name == 'amass':
                cmd = f"amass enum -d {target}"
                if 'mode' in valid_params:
                    mode = valid_params.pop('mode')
                    if mode == 'passive':
                        cmd = f"amass enum -passive -d {target}"
                    elif mode == 'active':
                        cmd = f"amass enum -active -d {target}"
                if 'sources' in valid_params:
                    cmd += f" -src {valid_params.pop('sources')}"

            # 特殊处理 fierce (需要 -domain)
            elif tool_name == 'fierce':
                cmd = f"fierce -domain {target}"
                if 'threads' in valid_params:
                    cmd += f" --threads {valid_params.pop('threads')}"

            # 特殊处理 theHarvester (需要 -d domain -b source)
            elif tool_name == 'theHarvester':
                source = valid_params.pop('source', 'all')
                cmd = f"theHarvester -d {target} -b {source}"
                if 'limit' in valid_params:
                    cmd += f" -l {valid_params.pop('limit')}"

            # 特殊处理 httpx (格式：httpx [OPTIONS] URL)
            elif tool_name == 'httpx':
                cmd = f"httpx {target}"
                if 'status_code' in valid_params and valid_params.pop('status_code') in [True, 'true', '1']:
                    cmd += " -sc"
                if 'title' in valid_params and valid_params.pop('title') in [True, 'true', '1']:
                    cmd += " -title"
                if 'content_length' in valid_params and valid_params.pop('content_length') in [True, 'true', '1']:
                    cmd += " -cl"
                if 'server' in valid_params and valid_params.pop('server') in [True, 'true', '1']:
                    cmd += " -server"

            # 特殊处理 nmap (支持 scan_type 和 ports)
            elif tool_name == 'nmap':
                cmd = f"nmap {target}"
                if 'scan_type' in valid_params:
                    cmd += f" {valid_params.pop('scan_type')}"
                if 'ports' in valid_params:
                    cmd += f" -p {valid_params.pop('ports')}"

            # 特殊处理 sqlmap (需要 -u)
            elif tool_name == 'sqlmap':
                cmd = f"sqlmap -u {target}"
                if 'level' in valid_params:
                    cmd += f" --level={valid_params.pop('level')}"
                if 'risk' in valid_params:
                    cmd += f" --risk={valid_params.pop('risk')}"

            # 特殊处理 dirb (需要 URL)
            elif tool_name == 'dirb':
                cmd = f"dirb {target}"
                if 'wordlist' in valid_params:
                    cmd += f" {valid_params.pop('wordlist')}"
                if 'extensions' in valid_params:
                    cmd += f" -e {valid_params.pop('extensions')}"
                if 'recursive' in valid_params and valid_params.pop('recursive') in [True, 'true', '1']:
                    cmd += " -r"

            # 特殊处理 nikto (需要 -host 参数)
            # 命令格式：nikto -host <url> [options]
            elif tool_name == 'nikto':
                cmd = f"nikto -host {target}"
                if 'port' in valid_params:
                    cmd += f" -port {valid_params.pop('port')}"
                if 'ssl' in valid_params and valid_params.pop('ssl') in [True, 'true', '1']:
                    cmd += " -ssl"
                if 'timeout' in valid_params:
                    cmd += f" -timeout {valid_params.pop('timeout')}"
                if 'useragent' in valid_params:
                    cmd += f" -useragent {valid_params.pop('useragent')}"
                if 'tuning' in valid_params:
                    cmd += f" -Tuning {valid_params.pop('tuning')}"
                if 'output' in valid_params:
                    cmd += f" -output {valid_params.pop('output')}"
                if 'format' in valid_params:
                    cmd += f" -Format {valid_params.pop('format')}"
                if 'vhost' in valid_params:
                    cmd += f" -vhost {valid_params.pop('vhost')}"
                if 'id' in valid_params:
                    cmd += f" -id {valid_params.pop('id')}"

            # 特殊处理 ffuf (需要 -u)
            elif tool_name == 'ffuf':
                cmd = f"ffuf -u {target}"
                if 'wordlist' in valid_params:
                    cmd += f" -w {valid_params.pop('wordlist')}"
                if 'method' in valid_params:
                    cmd += f" -X {valid_params.pop('method')}"
                if 'headers' in valid_params:
                    cmd += f" -H {valid_params.pop('headers')}"

            # 特殊处理 feroxbuster (需要 -u)
            elif tool_name == 'feroxbuster':
                cmd = f"feroxbuster -u {target}"
                if 'wordlist' in valid_params:
                    cmd += f" -w {valid_params.pop('wordlist')}"
                if 'threads' in valid_params:
                    cmd += f" -t {valid_params.pop('threads')}"
                if 'depth' in valid_params:
                    cmd += f" -d {valid_params.pop('depth')}"

            # 特殊处理 wpscan (需要 --url)
            elif tool_name == 'wpscan':
                cmd = f"wpscan --url {target}"
                if 'api_token' in valid_params:
                    cmd += f" --api-token {valid_params.pop('api_token')}"
                if 'enumerate' in valid_params:
                    cmd += f" --enumerate {valid_params.pop('enumerate')}"
                if 'plugins_detection' in valid_params:
                    cmd += f" --plugins-detection {valid_params.pop('plugins_detection')}"

            # 特殊处理 nuclei (需要 -target)
            elif tool_name == 'nuclei':
                cmd = f"nuclei -target {target}"
                if 'templates' in valid_params:
                    cmd += f" -t {valid_params.pop('templates')}"
                if 'severity' in valid_params:
                    cmd += f" -severity {valid_params.pop('severity')}"
                if 'tags' in valid_params:
                    cmd += f" -tags {valid_params.pop('tags')}"

            # 特殊处理 katana (需要 -u)
            elif tool_name == 'katana':
                cmd = f"katana -u {target}"
                if 'depth' in valid_params:
                    cmd += f" -d {valid_params.pop('depth')}"
                if 'scope' in valid_params:
                    cmd += f" -scope {valid_params.pop('scope')}"

            # 特殊处理 hakrawler (从 stdin 读取 URL，使用管道传递)
            elif tool_name == 'hakrawler':
                cmd = f"echo '{target}' | hakrawler"
                if 'depth' in valid_params:
                    cmd += f" -depth {valid_params.pop('depth')}"

            # 特殊处理 hydra (需要 target 和 service)
            elif tool_name == 'hydra':
                service = valid_params.pop('service', 'ssh')
                username = valid_params.pop('username', 'root')
                wordlist = valid_params.pop('wordlist', '/usr/share/wordlists/rockyou.txt')
                cmd = f"hydra -l {username} -P {wordlist} {target} {service}"
                if 'threads' in valid_params:
                    cmd += f" -t {valid_params.pop('threads')}"
                if 'timeout' in valid_params:
                    cmd += f" -w {valid_params.pop('timeout')}"

            # 特殊处理 hashcat (需要 hash_type 和 wordlist)
            elif tool_name == 'hashcat':
                hash_type = valid_params.pop('hash_type', '0')
                wordlist = valid_params.pop('wordlist', '/usr/share/wordlists/rockyou.txt')
                cmd = f"hashcat -m {hash_type} -a 0 {target} {wordlist}"
                if 'force' in valid_params and valid_params.pop('force') in [True, 'true', '1']:
                    cmd += " --force"

            # 特殊处理 john (需要 wordlist)
            elif tool_name == 'john':
                wordlist = valid_params.pop('wordlist', '/usr/share/wordlists/rockyou.txt')
                cmd = f"john --wordlist={wordlist} {target}"
                if 'format' in valid_params:
                    cmd += f" --format={valid_params.pop('format')}"

            # 特殊处理 rustscan (需要 -a)
            elif tool_name == 'rustscan':
                cmd = f"rustscan -a {target}"
                if 'ports' in valid_params:
                    cmd += f" -p {valid_params.pop('ports')}"
                if 'top' in valid_params:
                    cmd += f" --top {valid_params.pop('top')}"

            # 特殊处理 masscan (需要 -p)
            elif tool_name == 'masscan':
                cmd = f"masscan {target}"
                if 'ports' in valid_params:
                    cmd += f" -p {valid_params.pop('ports')}"
                if 'rate' in valid_params:
                    cmd += f" --rate {valid_params.pop('rate')}"

            # 特殊处理 responder (需要 -I 接口)
            elif tool_name == 'responder':
                interface = valid_params.pop('interface', 'eth0')
                cmd = f"responder -I {interface}"
                if 'analyze' in valid_params and valid_params.pop('analyze') in [True, 'true', '1']:
                    cmd += " -A"
                else:
                    cmd += " -wrf"

            # 特殊处理 nxc/crackmapexec (需要 target 和协议)
            elif tool_name in ['nxc', 'crackmapexec']:
                protocol = valid_params.pop('protocol', 'smb')
                cmd = f"nxc {protocol} {target}"
                if 'username' in valid_params:
                    cmd += f" -u {valid_params.pop('username')}"
                if 'password' in valid_params:
                    cmd += f" -p {valid_params.pop('password')}"

            # 特殊处理 shodan (需要 query)
            elif tool_name in ['shodan', 'shodan_search']:
                cmd = f"shodan host {target}"
                if 'limit' in valid_params:
                    cmd += f" --limit {valid_params.pop('limit')}"

            # 特殊处理 wfuzz (需要 -u 和 -w)
            elif tool_name == 'wfuzz':
                cmd = f"wfuzz -u {target}"
                if 'wordlist' in valid_params:
                    cmd += f" -w {valid_params.pop('wordlist')}"
                if 'hc' in valid_params:
                    cmd += f" --hc {valid_params.pop('hc')}"

            # 特殊处理 jaeles (需要 -t 和 -l)
            elif tool_name == 'jaeles':
                cmd = f"jaeles scan -t {target}"
                if 'templates' in valid_params:
                    cmd += f" -l {valid_params.pop('templates')}"

            # 特殊处理 dalfox (需要 -u)
            elif tool_name == 'dalfox':
                cmd = f"dalfox url {target}"
                if 'blind' in valid_params:
                    cmd += f" --blind {valid_params.pop('blind')}"

            # 特殊处理 arjun (需要 -u)
            elif tool_name == 'arjun':
                cmd = f"arjun -u {target}"
                if 'stable' in valid_params and valid_params.pop('stable') in [True, 'true', '1']:
                    cmd += " --stable"

            # 特殊处理 medusa (需要 -H 和 -M)
            elif tool_name == 'medusa':
                service = valid_params.pop('service', 'ssh')
                username = valid_params.pop('username', 'root')
                wordlist = valid_params.pop('wordlist', '/usr/share/wordlists/rockyou.txt')
                cmd = f"medusa -h {target} -u {username} -P {wordlist} -M {service}"
                if 'threads' in valid_params:
                    cmd += f" -t {valid_params.pop('threads')}"

            # 特殊处理 patator (需要 host 和 port)
            elif tool_name == 'patator':
                module = valid_params.pop('module', 'ssh_login')
                cmd = f"patator {module} host={target}"
                if 'user' in valid_params:
                    cmd += f" user={valid_params.pop('user')}"
                if 'password' in valid_params:
                    cmd += f" password={valid_params.pop('password')}"

            # 处理 additional_args (所有工具通用，追加到末尾)
            additional_args = valid_params.pop('additional_args', '')
            if additional_args:
                cmd += f" {additional_args}"
            
            # 处理剩余参数 (通用拼接，不强制加 --)
            if valid_params:
                param_str = " ".join([f"{k} {v}" for k, v in valid_params.items()])
                cmd += f" {param_str}"

            logger.info(f"Executing: {cmd} [Task: {task_id}] [Params: {valid_params}]")

            output_path = f"/tmp/{task_id}.log"
            stdout_reader = None
            stderr_reader = None
            process = None

            try:
                # 创建进程组，便于后续清理子进程
                if os.name == 'nt':  # Windows
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
                    process = subprocess.Popen(
                        cmd,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        creationflags=creationflags
                    )
                else:  # Linux/macOS
                    process = subprocess.Popen(
                        cmd,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        preexec_fn=os.setsid
                    )

                start_time = time.time()
                check_interval = 2  # 每2秒检查一次进程状态
                idle_timeout = int(os.environ.get("IDLE_TIMEOUT", 300))  # 空闲超时：5分钟无输出则终止

                # 注册活跃任务，支持手动取消
                _active_tasks[task_id] = {
                    'process': process,
                    'stdout_reader': None,
                    'stderr_reader': None,
                    'start_time': start_time
                }

                # 打开输出文件
                with open(output_path, 'w', encoding='utf-8') as out_file:
                    # 启动非阻塞读取器
                    stdout_reader = _OutputReader(
                        process.stdout, 'stdout', task_id, out_file,
                        self._push_log, self._update_progress
                    )
                    stderr_reader = _OutputReader(
                        process.stderr, 'stderr', task_id, out_file,
                        self._push_log, self._update_progress
                    )
                    stdout_reader.start()
                    stderr_reader.start()

                    # 更新活跃任务信息
                    _active_tasks[task_id]['stdout_reader'] = stdout_reader
                    _active_tasks[task_id]['stderr_reader'] = stderr_reader

                    # 主循环：监控进程状态（空闲超时机制）
                    while True:
                        # 检查进程是否退出
                        exit_code = process.poll()
                        if exit_code is not None:
                            # 进程已退出，等待读取器完成
                            logger.info(f"Process exited with code {exit_code} for task {task_id}")
                            break

                        # 检查空闲超时（如果两个读取器都超过空闲时间没有输出）
                        stdout_idle = stdout_reader.get_idle_seconds()
                        stderr_idle = stderr_reader.get_idle_seconds()
                        if stdout_idle > idle_timeout and stderr_idle > idle_timeout:
                            logger.warning(f"⏸️ Task {task_id} idle timeout after {stdout_idle:.0f}s (limit: {idle_timeout}s)")
                            self._push_log(task_id, f"⏸️ Task idle timeout - no output for {stdout_idle:.0f}s", 'system')
                            self._terminate_process(process, task_id)
                            exit_code = process.poll()
                            break

                        # 定期更新进度
                        self._update_progress(task_id, process)
                        time.sleep(check_interval)

                    # 停止读取器并读取剩余输出
                    if stdout_reader:
                        stdout_reader.stop()
                        stdout_reader.drain_remaining()
                    if stderr_reader:
                        stderr_reader.stop()
                        stderr_reader.drain_remaining()

                # 最终退出码
                if exit_code is None:
                    exit_code = process.returncode

                if exit_code == 0:
                    return {"success": True, "output_path": output_path}
                else:
                    return {"success": False, "error": f"Exit code {exit_code}", "output_path": output_path}

            except Exception as e:
                logger.error(f"Execution error for task {task_id}: {e}")
                self._push_log(task_id, f"Execution error: {str(e)}", 'stderr')
                # 确保清理进程
                if process and process.poll() is None:
                    self._terminate_process(process, task_id)
                return {"success": False, "error": str(e)}
            finally:
                # 确保读取器被清理
                if stdout_reader:
                    stdout_reader.stop()
                if stderr_reader:
                    stderr_reader.stop()
                if process and process.poll() is None:
                    try:
                        process.kill()
                    except Exception:
                        pass
                # 从活跃任务中移除
                _active_tasks.pop(task_id, None)

    def cancel_task(self, task_id: str) -> bool:
        """手动取消任务"""
        if task_id not in _active_tasks:
            return False
        
        task_info = _active_tasks[task_id]
        process = task_info.get('process')
        
        if process and process.poll() is None:
            logger.info(f"🛑 Cancelling task {task_id}")
            self._push_log(task_id, "🛑 Task cancelled by user", 'system')
            self._terminate_process(process, task_id)
            return True
        return False

    @staticmethod
    def get_active_tasks():
        """获取所有活跃任务"""
        return {
            task_id: {
                'pid': info['process'].pid if info['process'] else None,
                'start_time': info['start_time'],
                'elapsed': time.time() - info['start_time']
            }
            for task_id, info in _active_tasks.items()
        }

    def _terminate_process(self, process, task_id: str):
        """终止进程及其子进程"""
        try:
            if os.name == 'nt':  # Windows
                process.terminate()
            else:  # Linux/macOS - 终止整个进程组
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                # 等待一下，如果还没退出则强制 kill
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
        except Exception as e:
            logger.error(f"Failed to terminate process for task {task_id}: {e}")
            try:
                process.kill()
            except Exception:
                pass

    def _push_log(self, task_id: str, message: str, source: str):
        """推送日志到 MySQL 和 Redis"""
        if extensions.redis_client:
            try:
                extensions.redis_client.lpush(f"task:{task_id}:logs", message)
                extensions.redis_client.publish("hexstrike:logs", f"{task_id}|{message}")
                logger.info(f"[LOG] Pushed log for task {task_id}: {message[:80]}...")
            except Exception as e:
                logger.error(f"Failed to push log to Redis: {e}")

        log_entry = TaskLog(task_id=task_id, message=message, source=source, level='INFO')
        db.session.add(log_entry)
        db.session.commit()

    def _update_progress(self, task_id: str, process):
        """更新进度 (模拟)"""
        current = int(time.time() % 100)
        if extensions.redis_client:
            extensions.redis_client.hset(f"task:{task_id}", mapping={"progress": str(current)})
            extensions.redis_client.publish("hexstrike:progress", f"{task_id}|{current}")
