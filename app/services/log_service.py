"""
日志队列服务 - 异步批量处理日志写入
"""
import queue
import threading
import time
import json
from datetime import datetime
from app.extensions import db
import app.extensions as extensions
from app.models.task import TaskLog
import logging

logger = logging.getLogger(__name__)

# 全局日志队列
_log_queue = queue.Queue(maxsize=1000)

# 消费者线程控制
_consumer_thread = None
_consumer_running = False


class LogEntry:
    """日志条目"""
    def __init__(self, task_id: str, message: str, source: str, level: str = 'INFO'):
        self.task_id = task_id
        self.message = message
        self.source = source
        self.level = level
        self.timestamp = datetime.now().isoformat()

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'message': self.message,
            'source': self.source,
            'level': self.level,
            'timestamp': self.timestamp
        }


def _log_consumer():
    """日志消费者线程 - 批量处理日志写入"""
    global _consumer_running

    batch = []
    batch_size = 50  # 每 50 条写入一次
    flush_interval = 1.0  # 或每 1 秒刷新一次
    last_flush = time.time()

    while _consumer_running:
        try:
            # 非阻塞获取日志
            try:
                entry = _log_queue.get(timeout=0.5)
                batch.append(entry)
            except queue.Empty:
                pass

            # 检查是否需要刷新
            now = time.time()
            should_flush = (len(batch) >= batch_size) or (batch and now - last_flush >= flush_interval)

            if should_flush:
                _flush_batch(batch)
                batch = []
                last_flush = now

        except Exception as e:
            logger.error(f"Log consumer error: {e}")

    # 线程退出前刷新剩余日志
    if batch:
        _flush_batch(batch)


def _flush_batch(batch):
    """批量写入数据库"""
    if not batch:
        return

    try:
        # 直接使用当前 db session（extensions.py 已初始化）
        entries = [
            TaskLog(
                task_id=entry.task_id,
                message=entry.message,
                source=entry.source,
                level=entry.level
            )
            for entry in batch
        ]
        db.session.add_all(entries)
        db.session.commit()

        logger.debug(f"Flushed {len(batch)} log entries to DB")

    except Exception as e:
        logger.error(f"Failed to flush log batch: {e}")
        # 降级：写入本地文件
        try:
            with open('/tmp/hexstrike_logs_fallback.log', 'a') as f:
                for entry in batch:
                    f.write(f"{entry.timestamp} [{entry.task_id}] {entry.message}\n")
        except:
            pass


def push_log(task_id: str, message: str, source: str, level: str = 'INFO'):
    """推送日志到队列（非阻塞）"""
    entry = LogEntry(task_id, message, source, level)

    # Redis 实时推送（不需要应用上下文）
    if extensions.redis_client:
        try:
            log_json = json.dumps(entry.to_dict())
            extensions.redis_client.lpush(f"task:{task_id}:logs", log_json)
            extensions.redis_client.publish("hexstrike:logs", f"{task_id}|{log_json}")
        except Exception as e:
            logger.error(f"Failed to push log to Redis: {e}")

    # 加入队列（异步写入 DB）
    try:
        _log_queue.put_nowait(entry)
    except queue.Full:
        logger.warning("Log queue full, dropping log entry")
        # 降级：直接写文件
        try:
            with open(f'/tmp/{task_id}_fallback.log', 'a') as f:
                f.write(f"{entry.timestamp} {message}\n")
        except:
            pass


def start_consumer():
    """启动日志消费者线程"""
    global _consumer_thread, _consumer_running

    if _consumer_running:
        logger.warning("Log consumer already running")
        return False

    _consumer_running = True
    _consumer_thread = threading.Thread(target=_log_consumer, daemon=True, name="LogConsumer")
    _consumer_thread.start()
    logger.info("✅ Log consumer thread started")
    return True


def stop_consumer():
    """停止日志消费者线程"""
    global _consumer_running

    if not _consumer_running:
        return False

    _consumer_running = False
    if _consumer_thread:
        _consumer_thread.join(timeout=5)

    logger.info("🛑 Log consumer thread stopped")
    return True


def get_queue_size():
    """获取队列大小"""
    return _log_queue.qsize()
