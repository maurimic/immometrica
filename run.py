import uvicorn
import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Ensure current directory is in sys.path
sys.path.insert(0, os.path.dirname(__file__))

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload_mode = os.environ.get("ENV", "development").lower() != "production"
    print("=" * 65)
    print(f"[*] Starting ImmoMetrica Clean-Room Clone Server on {host}:{port}...")
    print(f"[*] Web Application: http://{host}:{port}")
    print(f"[*] Interactive API Docs: http://{host}:{port}/docs")
    print(f"[*] Model Context Protocol (MCP): http://{host}:{port}/mcp/tools")
    print("=" * 65)
    clone_dir = os.path.abspath(os.path.dirname(__file__))
    uvicorn.run("app.main:app", host=host, port=port, reload=reload_mode, reload_dirs=[clone_dir] if reload_mode else None)
