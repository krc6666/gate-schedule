from __future__ import annotations

import argparse
import hashlib
import os
import sys
import sqlite3
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from .web import ROOT, create_app
from .workspace_api import attach_workspace


class LocalStaticFiles(StaticFiles):
    def is_not_modified(self, response_headers, request_headers):
        # A portable update must not leave an older entry page in the browser.
        return False


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return ROOT


def local_data_directory() -> Path:
    configured = os.environ.get("GATE_SCHEDULE_DATA")
    if configured:
        return Path(configured)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return ROOT / "data-v2"


def create_server_app(static_directory: Path | None = None, database_path: Path | None = None):
    static_directory = static_directory or runtime_root() / "web" / "dist"
    if not (static_directory / "index.html").is_file():
        raise FileNotFoundError(f"未找到已构建页面：{static_directory}；请先运行 npm run build --prefix web")
    database_path = database_path or local_data_directory() / "gate-schedule.sqlite3"
    try:
        app = create_app(database_path=database_path)
    except (ValueError, KeyError, TypeError, sqlite3.Error):
        from fastapi import FastAPI
        app = FastAPI(title="登机口保障排班 · 数据恢复")
    attach_workspace(app, database_path)

    @app.middleware("http")
    async def prevent_stale_pages(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/", LocalStaticFiles(directory=static_directory, html=True), name="web")
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="启动登机口保障排班本机服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    app = create_server_app()
    version = hashlib.sha256((runtime_root()/"web"/"dist"/"index.html").read_bytes()).hexdigest()[:12]
    address = f"http://{args.host}:{args.port}/?v={version}"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(address)).start()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
