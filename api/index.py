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
    # 1. Check if Vercel forwarded the original client route in incoming headers
    orig_path = (
        request.headers.get("x-matched-path")
        or request.headers.get("x-forwarded-uri")
        or request.headers.get("x-invoke-path")
        or request.headers.get("x-vercel-matched-path")
    )
    if orig_path and orig_path.startswith("/api") and not orig_path.startswith("/api/index.py"):
        request.scope["path"] = orig_path.split("?")[0]
    else:
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

@app.get("/api/inspect")
def inspect_headers(request: Request):
    return {
        "scope_path": request.scope.get("path"),
        "raw_path": str(request.scope.get("raw_path")),
        "headers": dict(request.headers),
    }


import traceback

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
            "path": request.scope.get("path")
        }
    )

@app.get("/api/diagnostic-chat")
def diagnostic_chat():
    results = {}
    try:
        from web.app import ask_shashank_ama, AMAChatRequest, process_chat, ChatRequest
        req = AMAChatRequest(message="Test question", history=[])
        res = ask_shashank_ama(req)
        results["ama_status"] = "ok"
        results["ama_source"] = res.get("source")
        results["ama_reply"] = res.get("reply", "")[:100]
    except Exception as e:
        results["ama_status"] = "error"
        results["ama_error"] = str(e)
        results["ama_traceback"] = traceback.format_exc()

    try:
        from web.app import process_chat, ChatRequest
        chat_req = ChatRequest(message="Hello")
        chat_res = process_chat("diag_sess_test", chat_req)
        results["chat_status"] = "ok"
        results["chat_agent_response"] = chat_res.get("agent_response", "")[:100]
    except Exception as e:
        results["chat_status"] = "error"
        results["chat_error"] = str(e)
        results["chat_traceback"] = traceback.format_exc()

    return results

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
