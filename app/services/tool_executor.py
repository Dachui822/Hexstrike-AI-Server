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
            # 1. 匹配 MCP 服务端工具状态 (前置校验)
            tool = db.session.get(Tool, tool_name)
            if not tool:
                return {"success": False, "error": f"Tool '{tool_name}' not registered in database."}

            if not tool.is_available:
                logger.warning(f"⚠️ Tool '{tool_name}' is marked as unavailable. Attempting execution anyway...")

            # 2. 过滤无效参数，保留执行参数
            meta_params = {'async', 'priority', 'timeout'}
            valid_params = {k: v for k, v in params.items() if k not in meta_params}

            # 3. 构建命令
            cmd = f"{tool_name} {target}"
            if valid_params:
                param_str = " ".join([f"--{k}={v}" for k, v in valid_params.items()])
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
