"""Vercel Serverless Function entrypoint for FastAPI backend."""

import os
import sys
from pathlib import Path

# Multi-candidate path resolution for Vercel Serverless Lambda environment
current_file = Path(__file__).resolve()
current_dir = current_file.parent

candidates = [
    current_dir.parent,          # standard project root (e.g. /var/task or repo root)
    current_dir,                 # in case files are packaged alongside
    Path(os.getcwd()),           # current working directory
    Path("/var/task"),           # Vercel AWS Lambda task root
]

for base in candidates:
    if base.exists():
        src_path = str(base / "src")
        base_path = str(base)
        if (base / "src").exists() and src_path not in sys.path:
            sys.path.insert(0, src_path)
        if base_path not in sys.path:
            sys.path.insert(0, base_path)

try:
    from web.app import app
except Exception as e:
    import traceback
    startup_error = str(e)
    startup_traceback = traceback.format_exc()
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Paylane API - Diagnostic Mode")

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
    def diagnostic_fallback(full_path: str):
        return JSONResponse(
            status_code=500,
            content={
                "status": "startup_error",
                "error": startup_error,
                "traceback": startup_traceback,
                "sys_path": sys.path,
                "cwd": os.getcwd(),
                "file": str(current_file),
            }
        )
