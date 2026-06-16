import subprocess
import logging
import time
import os
from app.extensions import db
import app.extensions as extensions
from app.models.task import TaskLog, Task
from app.models.tool import Tool

logger = logging.getLogger(__name__)

class ToolExecutor:
    def run(self, task_id: str, tool_name: str, target: str, params: dict) -> dict:
        """执行工具命令"""
        from app import create_app
        app = create_app()
        with app.app_context():
            # 调试日志：记录接收到的参数
            logger.info(f"[DEBUG] run() called: task_id={task_id}, tool_name={tool_name}, target={repr(target)}, params={params}")
            
            # 1. 匹配 MCP 服务端工具状态 (前置校验)
            tool = db.session.get(Tool, tool_name)
            if not tool:
                return {"success": False, "error": f"Tool '{tool_name}' not registered in database."}

            if not tool.is_available:
                logger.warning(f"⚠️ Tool '{tool_name}' is marked as unavailable. Attempting execution anyway...")

            # 2. 过滤无效参数，保留执行参数
            meta_params = {'async', 'priority', 'timeout', 'use_recovery'}
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
            
            # 特殊处理 gobuster (需要 -u 和 mode)
            elif tool_name == 'gobuster':
                mode = valid_params.pop('mode', 'dir')
                cmd = f"gobuster {mode} -u {target}"
                if 'wordlist' in valid_params:
                    cmd += f" -w {valid_params.pop('wordlist')}"
                if 'threads' in valid_params:
                    cmd += f" -t {valid_params.pop('threads')}"

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

            # 特殊处理 httpx (需要 -u 或 -l)
            elif tool_name == 'httpx':
                cmd = f"httpx -u {target}"
                if 'status_code' in valid_params and valid_params.pop('status_code') in [True, 'true', '1']:
                    cmd += " -sc"
                if 'title' in valid_params and valid_params.pop('title') in [True, 'true', '1']:
                    cmd += " -title"

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

            # 特殊处理 nikto (需要 -h)
            elif tool_name == 'nikto':
                cmd = f"nikto -h {target}"
                if 'port' in valid_params:
                    cmd += f" -p {valid_params.pop('port')}"
                if 'ssl' in valid_params and valid_params.pop('ssl') in [True, 'true', '1']:
                    cmd += " -ssl"

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

            # 特殊处理 hakrawler (需要 -u)
            elif tool_name == 'hakrawler':
                cmd = f"hakrawler -u {target}"
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
            try:
                process = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                # 打开输出文件，保存扫描结果
                with open(output_path, 'w', encoding='utf-8') as out_file:
                    while True:
                        output = process.stdout.readline()
                        if output == '' and process.poll() is not None:
                            break
                        if output:
                            out_file.write(output)
                            out_file.flush()
                            self._push_log(task_id, output.strip(), 'stdout')
                            self._update_progress(task_id, process)

                    # 读取 stderr
                    stderr_output = process.stderr.read()
                    if stderr_output:
                        out_file.write(f"\n--- STDERR ---\n{stderr_output}\n")
                        self._push_log(task_id, stderr_output, 'stderr')

                exit_code = process.poll()

                if exit_code == 0:
                    return {"success": True, "output_path": output_path}
                else:
                    return {"success": False, "error": f"Exit code {exit_code}", "output_path": output_path}

            except Exception as e:
                self._push_log(task_id, f"Execution error: {str(e)}", 'stderr')
                return {"success": False, "error": str(e)}

    def _push_log(self, task_id: str, message: str, source: str):
        """推送日志到 MySQL 和 Redis"""
        if extensions.redis_client:
            extensions.redis_client.lpush(f"task:{task_id}:logs", message)
            extensions.redis_client.publish("hexstrike:logs", f"{task_id}|{message}")

        log_entry = TaskLog(task_id=task_id, message=message, source=source, level='INFO')
        db.session.add(log_entry)
        db.session.commit()

    def _update_progress(self, task_id: str, process):
        """更新进度 (模拟)"""
        current = int(time.time() % 100)
        if extensions.redis_client:
            extensions.redis_client.hset(f"task:{task_id}", mapping={"progress": str(current)})
            extensions.redis_client.publish("hexstrike:progress", f"{task_id}|{current}")
