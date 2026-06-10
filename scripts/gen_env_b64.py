import sys
import os

# 添加项目根目录到路径以便导入 app 模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.crypto import encode_b64

if __name__ == "__main__":
    print("=== HexStrike AI Base64 编码工具 ===")
    print("请输入需要加密的数据库配置信息：\n")
    
    host = input("MYSQL_HOST (默认 localhost): ") or "localhost"
    port = input("MYSQL_PORT (默认 3306): ") or "3306"
    user = input("MYSQL_USER (默认 hexstrike): ") or "hexstrike"
    password = input("MYSQL_PASSWORD (默认 hexstrike): ") or "hexstrike"
    db = input("MYSQL_DB (默认 hexstrike): ") or "hexstrike"
    
    print("\n--- 生成的 Base64 编码值 ---")
    print(f"MYSQL_HOST={encode_b64(host)}")
    print(f"MYSQL_PORT={encode_b64(port)}")
    print(f"MYSQL_USER={encode_b64(user)}")
    print(f"MYSQL_PASSWORD={encode_b64(password)}")
    print(f"MYSQL_DB={encode_b64(db)}")
    print("\n请将上述值配置到您的环境变量或 docker-compose.yml 中。")
