"""
工具元数据初始化脚本
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.extensions import db
from app.models.tool import Tool
from app.services.tool_registry import DEFAULT_TOOLS

def seed():
    app = create_app()
    with app.app_context():
        count = 0
        for t_data in DEFAULT_TOOLS:
            if not db.session.get(Tool, t_data['name']):
                tool = Tool(**t_data)
                db.session.add(tool)
                count += 1
        db.session.commit()
        print(f"✅ Successfully seeded {count} tools into database.")

if __name__ == "__main__":
    seed()
