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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Top-level FastAPI instance explicitly defined for Vercel framework AST detection
app = FastAPI(
    title="Paylane AI Support Agent — Workspace API",
    description="Thin, safe HTTP adapter connecting the verified Paylane AI Support Agent runtime to a modern support console.",
    version="1.0.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Resilient ASGI middleware to normalize any rewritten paths from Vercel
@app.middleware("http")
async def normalize_vercel_rewrites(request: Request, call_next):
    raw_path = request.scope.get("path", "")
    if raw_path.startswith("/api/index.py"):
        suffix = raw_path[len("/api/index.py"):]
        if not suffix or suffix == "/":
            request.scope["path"] = "/api"
        elif suffix.startswith("/"):
            request.scope["path"] = "/api" + suffix
        else:
            request.scope["path"] = "/api/" + suffix
    return await call_next(request)

# Import and attach all routes from the core web application
try:
    from web.app import app as web_app
    app.include_router(web_app.router)
except Exception as e:
    import traceback
    startup_error = str(e)
    startup_traceback = traceback.format_exc()

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
