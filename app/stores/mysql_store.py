from typing import Dict, Any, List, Optional
from app.stores.base import TaskStore
from app.extensions import db
from sqlalchemy import text
import time

class MySQLTaskStore(TaskStore):
    def create_task(self, task_id: str, data: Dict[str, Any]) -> bool:
        try:
            sql = text("""
                INSERT INTO tasks (id, tool_name, params, status, progress, created_at)
                VALUES (:id, :tool, :params, 'PENDING', 0, :created)
            """)
            db.session.execute(sql, {
                "id": task_id,
                "tool": data.get("tool"),
                "params": str(data.get("params", {})),
                "created": time.time()
            })
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            return False
    
    def update_status(self, task_id: str, status: str, progress: float = 0.0, message: str = "") -> bool:
        try:
            sql = text("UPDATE tasks SET status = :status, progress = :progress WHERE id = :id")
            db.session.execute(sql, {"status": status, "progress": progress, "id": task_id})
            if message:
                log_sql = text("INSERT INTO task_logs (task_id, message, created_at) VALUES (:tid, :msg, :ts)")
                db.session.execute(log_sql, {"tid": task_id, "msg": message, "ts": time.time()})
            db.session.commit()
            return True
        except Exception:
            db.session.rollback()
            return False
    
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        sql = text("SELECT * FROM tasks WHERE id = :id")
        result = db.session.execute(sql, {"id": task_id}).fetchone()
        return dict(result._mapping) if result else None
    
    def list_tasks(self, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        base = "SELECT * FROM tasks"
        if status:
            base += " WHERE status = :status"
        base += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        sql = text(base)
        results = db.session.execute(sql, {"status": status, "limit": limit, "offset": offset}).fetchall()
        return [dict(r._mapping) for r in results]
    
    def cancel_task(self, task_id: str) -> bool:
        return self.update_status(task_id, "CANCELLED")
