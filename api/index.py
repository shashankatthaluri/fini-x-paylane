"""Vercel Serverless Function entrypoint for FastAPI backend."""

import sys
from pathlib import Path

# Ensure root and src directories are in Python path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from web.app import app
