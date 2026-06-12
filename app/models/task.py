from app.extensions import db
from sqlalchemy import Column, String, Float, Integer, DateTime, Text, Enum as SQLEnum, LongText
from sqlalchemy.sql import func
import enum

class TaskStatus(enum.Enum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCESS = 'SUCCESS'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'
    TIMEOUT = 'TIMEOUT'

class Task(db.Model):
    __tablename__ = 'tasks'
    
    id = db.Column(String(36), primary_key=True)
    tool_name = db.Column(String(64), nullable=False, index=True)
    target = db.Column(String(255), nullable=False)
    params = db.Column(db.JSON, nullable=True)
    status = db.Column(SQLEnum(TaskStatus), default=TaskStatus.PENDING, index=True)
    progress = db.Column(Float, default=0.0)
    priority = db.Column(Integer, default=0)
    worker_id = db.Column(String(64), nullable=True)
    mcp_request_id = db.Column(String(64), nullable=True, index=True)
    
    started_at = db.Column(DateTime, nullable=True)
    completed_at = db.Column(DateTime, nullable=True)
    created_at = db.Column(DateTime, server_default=func.now())
    updated_at = db.Column(DateTime, server_default=func.now(), onupdate=func.now())
    error_message = db.Column(Text, nullable=True)
    output_path = db.Column(String(512), nullable=True)
    
    logs = db.relationship('TaskLog', backref='task', lazy='dynamic', cascade='all, delete-orphan')

class TaskLog(db.Model):
    __tablename__ = 'task_logs'

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    task_id = db.Column(String(36), db.ForeignKey('tasks.id'), nullable=False, index=True)
    level = db.Column(SQLEnum('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'), default='INFO')
    message = db.Column(LongText, nullable=False)
    source = db.Column(SQLEnum('stdout', 'stderr', 'system', 'progress'), default='system')
    timestamp = db.Column(DateTime, server_default=func.now())
