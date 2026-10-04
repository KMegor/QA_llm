from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from core import config
from core.state import sessions_store
from db import client as db

router = APIRouter()

def get_html_content():
    template_path = Path("templates/index.html")
    if not template_path.exists():
        return "<!DOCTYPE html><html><head><title>QA LLM Assistant</title></head><body><h1>Создайте templates/index.html</h1></body></html>"
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()
    content = content.replace("{{ model }}", config.DEFAULT_MODEL)
    return content

@router.get("/", response_class=HTMLResponse)
async def get_chat_page():
    return get_html_content()

@router.get("/health")
async def health_check():
    try:
        projects_count = db.count_projects()
    except Exception:
        projects_count = None
    return JSONResponse({
        "status": "ok",
        "projects_count": projects_count,
        "sessions_count": len(sessions_store)
    })
