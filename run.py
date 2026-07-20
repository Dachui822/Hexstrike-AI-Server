from app import create_app
import os

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8888))
    debug = os.environ.get("FLASK_ENV") == "development"
    
    print("=" * 60)
    print("🔥 HexStrike AI Web Server")
    print("=" * 60)
    print(f"📍 Listening on: http://0.0.0.0:{port}")
    print(f"🔧 Debug mode: {debug}")
    print(f"️  Note: Celery Worker must be started separately")
    print(f"   Command: python worker.py")
    print("=" * 60)
    
    app.run(host="0.0.0.0", port=port, debug=debug)
