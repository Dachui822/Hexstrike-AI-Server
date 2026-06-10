from app.extensions import db
from sqlalchemy import Column, String, Boolean, DateTime, Text, JSON
from sqlalchemy.sql import func

class Tool(db.Model):
    __tablename__ = 'tools'
    
    name = db.Column(String(64), primary_key=True)
    display_name = db.Column(String(128), nullable=False)
    category = db.Column(String(32), default='utility')
    description = db.Column(Text, nullable=True)
    command_template = db.Column(Text, nullable=True)
    dependencies = db.Column(JSON, nullable=True)
    health_check_cmd = db.Column(String(255), nullable=True)
    
    is_available = db.Column(Boolean, default=False)
    last_health_check = db.Column(DateTime, nullable=True)
    installed_version = db.Column(String(32), nullable=True)
    
    created_at = db.Column(DateTime, server_default=func.now())
    updated_at = db.Column(DateTime, server_default=func.now(), onupdate=func.now())
