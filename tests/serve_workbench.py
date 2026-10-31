"""Real isolated backend for the Chrome acceptance flow; never uses production data."""
import socket
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gate_schedule.server import create_server_app

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(f"http://127.0.0.1:{sock.getsockname()[1]}", flush=True)
uvicorn.Server(uvicorn.Config(create_server_app(database_path=Path(sys.argv[1])), log_level="error")).run(sockets=[sock])
