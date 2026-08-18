import subprocess
import logging
import time
import os
import threading
import signal
import json
from datetime import datetime
from app.extensions import db
import app.extensions as extensions
from app.models.task import TaskLog, Task
from app.models.tool import Tool
from app.services.log_service import push_log as push_log_async

logger = logging.getLogger(__name__)



class _OutputReader:
    """非阻塞输出读取器：使用线程异步读取 stdout/stderr"""

    def __init__(self, pipe, source: str, task_id: str, output_file, push_log_fn):
        self.pipe = pipe
        self.source = source
        self.task_id = task_id
        self.output_file = output_file
        self.push_log = push_log_fn
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
        from flask import current_app
        app = current_app._get_current_object()
        
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

            # 3. 构建命令 (委托给 CommandBuilder，统一使用 shell=False)
            from app.services.command_builder import CommandBuilder

            try:
                cmd_list = CommandBuilder.build(tool_name, target, valid_params)
            except ValueError as e:
                return {"success": False, "error": f"Command build failed: {e}"}

            cmd_str = " ".join(cmd_list)
            logger.info(f"Executing: {cmd_str} [Task: {task_id}]")

            output_path = f"/tmp/{task_id}.log"
            stdout_reader = None
            stderr_reader = None
            process = None

            try:
                # 创建进程组，便于后续清理子进程
                if os.name == 'nt':  # Windows
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
                    process = subprocess.Popen(
                        cmd_list,
                        shell=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        creationflags=creationflags
                    )
                else:  # Linux/macOS - 使用进程组 + 降低优先级
                    def _set_child_pgid_and_nice():
                        os.setsid()
                        try:
                            os.nice(10)  # 降低子进程优先级，避免抢占主进程资源
                        except PermissionError:
                            pass  # 非 root 用户可能无法设置 nice 值

                    process = subprocess.Popen(
                        cmd_list,
                        shell=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        preexec_fn=_set_child_pgid_and_nice
                    )

                start_time = time.time()
                check_interval = 2  # 每2秒检查一次进程状态
                idle_timeout = int(os.environ.get("IDLE_TIMEOUT", 300))  # 空闲超时：5分钟无输出则终止

                # 打开输出文件
                with open(output_path, 'w', encoding='utf-8') as out_file:
                    # 启动非阻塞读取器
                    stdout_reader = _OutputReader(
                        process.stdout, 'stdout', task_id, out_file,
                        self._push_log
                    )
                    stderr_reader = _OutputReader(
                        process.stderr, 'stderr', task_id, out_file,
                        self._push_log
                    )
                    stdout_reader.start()
                    stderr_reader.start()

                    # 主循环：监控进程状态（空闲超时机制 + Redis 取消标志）
                    while True:
                        # 检查进程是否退出
                        exit_code = process.poll()
                        if exit_code is not None:
                            # 进程已退出，等待读取器完成
                            logger.info(f"Process exited with code {exit_code} for task {task_id}")
                            break

                        # 检查 Redis 取消标志
                        if extensions.redis_client:
                            try:
                                cancel_flag = extensions.redis_client.get(f"task:{task_id}:cancel")
                                if cancel_flag == "1":
                                    logger.info(f"🛑 Task {task_id} received cancel signal from Redis")
                                    self._push_log(task_id, "🛑 Task cancelled by user", 'system')
                                    self._terminate_process(process, task_id)
                                    exit_code = process.poll()
                                    # 清理取消标志
                                    extensions.redis_client.delete(f"task:{task_id}:cancel")
                                    break
                            except Exception as e:
                                logger.error(f"Failed to check cancel flag: {e}")

                        # 检查空闲超时（如果两个读取器都超过空闲时间没有输出）
                        stdout_idle = stdout_reader.get_idle_seconds()
                        stderr_idle = stderr_reader.get_idle_seconds()
                        if stdout_idle > idle_timeout and stderr_idle > idle_timeout:
                            logger.warning(f"️ Task {task_id} idle timeout after {stdout_idle:.0f}s (limit: {idle_timeout}s)")
                            self._push_log(task_id, f"⏸️ Task idle timeout - no output for {stdout_idle:.0f}s", 'system')
                            self._terminate_process(process, task_id)
                            exit_code = process.poll()
                            break

                        # 定期更新进度 (已移除)
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
                    return {"success": True, "output_path": str(output_path)}
                else:
                    return {"success": False, "error": f"Exit code {exit_code}", "output_path": str(output_path)}

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
        
        task_info = _active_tasks[task_id]
        process = task_info.get('process')
        
        if process and process.poll() is None:
            logger.info(f"🛑 Cancelling task {task_id}")
            self._push_log(task_id, "🛑 Task cancelled by user", 'system')
            self._terminate_process(process, task_id)
            return True
        return False


    @staticmethod
    def _static_terminate_process(process, task_id: str):
        """静态方法：终止进程及其子进程（供外部调用）"""
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

    def _terminate_process(self, process, task_id: str):
        """终止进程及其子进程"""
        self._static_terminate_process(process, task_id)

    def _push_log(self, task_id: str, message: str, source: str):
        """推送日志到队列（异步批量写入）"""
        # 使用异步日志队列（非阻塞）
        push_log_async(task_id, message, source)
