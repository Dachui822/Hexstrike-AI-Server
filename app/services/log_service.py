"""
日志队列服务 - 异步批量处理日志写入（支持多任务并发）
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
from collections import defaultdict

logger = logging.getLogger(__name__)

# 全局日志队列（按任务 ID 分离）
_log_queues = defaultdict(lambda: queue.Queue(maxsize=200))

# 消费者线程控制
_consumer_thread = None
_consumer_running = False

# 按任务 ID 分组的批量缓冲区
_batch_buffers = defaultdict(list)


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
    """日志消费者线程 - 按任务分组批量处理"""
    global _consumer_running

    flush_interval = 0.5  # 每 0.5 秒刷新一次
    batch_size_per_task = 20  # 每个任务累积 20 条写入
    last_flush = time.time()

    while _consumer_running:
        try:
            should_flush = False
            
            # 从所有队列收集日志
            for task_id, log_queue in list(_log_queues.items()):
                batch = _batch_buffers[task_id]
                
                # 非阻塞获取日志
                while True:
                    try:
                        entry = log_queue.get_nowait()
                        batch.append(entry)
                    except queue.Empty:
                        break
                
                # 检查是否需要刷新（按任务独立判断）
                if len(batch) >= batch_size_per_task:
                    _flush_batch(task_id, batch)
                    _batch_buffers[task_id] = []
                    should_flush = True
            
            # 时间到刷新所有任务
            now = time.time()
            if now - last_flush >= flush_interval:
                for task_id, batch in list(_batch_buffers.items()):
                    if batch:
                        _flush_batch(task_id, batch)
                        _batch_buffers[task_id] = []
                last_flush = now
                should_flush = True
            
            # 清理空队列
            for task_id in list(_log_queues.keys()):
                if _log_queues[task_id].empty() and not _batch_buffers.get(task_id):
                    del _log_queues[task_id]
                    if task_id in _batch_buffers:
                        del _batch_buffers[task_id]
            
            # 短暂休眠，避免空转
            if not should_flush:
                time.sleep(0.1)

        except Exception as e:
            logger.error(f"Log consumer error: {e}")
            time.sleep(1.0)

    # 线程退出前刷新所有剩余日志
    for task_id, batch in list(_batch_buffers.items()):
        if batch:
            _flush_batch(task_id, batch)


def _flush_batch(task_id: str, batch):
    """批量写入数据库（每个任务独立上下文）"""
    if not batch:
        return

    try:
        # 每次写入创建独立的应用上下文（线程安全）
        from app import create_app
        app = create_app()
        
        with app.app_context():
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

        logger.debug(f"[{task_id}] Flushed {len(batch)} log entries to DB")

    except Exception as e:
        logger.error(f"Failed to flush log batch for {task_id}: {e}")
        # 降级：写入本地文件（按任务分离）
        try:
            with open(f'/tmp/{task_id}_fallback.log', 'a') as f:
                for entry in batch:
                    f.write(f"{entry.timestamp} [{entry.task_id}] {entry.message}\n")
        except Exception as fe:
            logger.error(f"Failed to write fallback log: {fe}")


def push_log(task_id: str, message: str, source: str, level: str = 'INFO'):
    """推送日志到队列（非阻塞，按任务分离）"""
    entry = LogEntry(task_id, message, source, level)

    # Redis 实时推送（不需要应用上下文）
    if extensions.redis_client:
        try:
            log_json = json.dumps(entry.to_dict())
            extensions.redis_client.lpush(f"task:{task_id}:logs", log_json)
            extensions.redis_client.publish("hexstrike:logs", f"{task_id}|{log_json}")
        except Exception as e:
            logger.error(f"Failed to push log to Redis: {e}")

    # 加入对应任务的队列（非阻塞）
    log_queue = _log_queues[task_id]
    try:
        log_queue.put_nowait(entry)
    except queue.Full:
        # 队列满：先尝试扩容
        if log_queue.maxsize < 1000:
            # 扩容到 1000 条
            new_queue = queue.Queue(maxsize=1000)
            # 转移旧数据
            while not log_queue.empty():
                try:
                    new_queue.put_nowait(log_queue.get_nowait())
                except queue.Empty:
                    break
            _log_queues[task_id] = new_queue
            new_queue.put_nowait(entry)
            logger.warning(f"[{task_id}] Log queue expanded to 1000")
        else:
            # 已达最大容量，降级写文件
            logger.warning(f"[{task_id}] Log queue full (1000), writing to fallback file")
            try:
                with open(f'/tmp/{task_id}_fallback.log', 'a') as f:
                    f.write(f"{entry.timestamp} [{entry.task_id}] {entry.message}\n")
            except Exception as fe:
                logger.error(f"[{task_id}] Failed to write fallback log: {fe}")


def start_consumer():
    """启动日志消费者线程"""
    global _consumer_thread, _consumer_running

    if _consumer_running:
        logger.warning("Log consumer already running")
        return False

    _consumer_running = True
    _consumer_thread = threading.Thread(target=_log_consumer, daemon=True, name="LogConsumer")
    _consumer_thread.start()
    logger.info("✅ Log consumer thread started (multi-task support)")
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


def get_queue_stats():
    """获取所有队列的统计信息"""
    stats = {
        'queue_count': len(_log_queues),
        'total_pending': sum(q.qsize() for q in _log_queues.values()),
        'buffer_count': len(_batch_buffers),
        'buffer_pending': sum(len(b) for b in _batch_buffers.values())
    }
    return stats


def cleanup_task(task_id: str):
    """清理指定任务的日志队列（任务完成时调用）"""
    if task_id in _log_queues:
        del _log_queues[task_id]
    if task_id in _batch_buffers:
        # 强制刷新剩余日志
        batch = _batch_buffers[task_id]
        if batch:
            _flush_batch(task_id, batch)
        del _batch_buffers[task_id]
    logger.debug(f"Cleaned up log queues for task {task_id}")
