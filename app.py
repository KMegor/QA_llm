import os
from core.config import HF_OFFLINE
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
if HF_OFFLINE:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import client as db
from core.config import SEARCH_LOG_LEVEL
from core.state import requeue_interrupted_documents
from routers import auth, chat, chats, checklist, pages, projects, testops

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logging.getLogger('rag.search').setLevel(
    getattr(logging, str(SEARCH_LOG_LEVEL).upper(), logging.INFO)
)

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск QA LLM Assistant")
    db.delete_all_session_tokens()
    requeue_interrupted_documents()
    yield
    logger.info("Остановка QA LLM Assistant")

app = FastAPI(
    title="QA LLM Assistant",
    description="Локальный AI-ассистент для QA-инженеров с интеграцией TestOps",
    version="2.2.0",
    lifespan=lifespan
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(chats.router)
app.include_router(projects.router)
app.include_router(chat.router)
app.include_router(checklist.router)
app.include_router(testops.router)

if __name__ == "__main__":
    Path("static").mkdir(exist_ok=True)
    Path("templates").mkdir(exist_ok=True)

    print("=" * 60)
    print("QA LLM Assistant")
    print("=" * 60)
    print(f"Интерфейс доступен по адресу: http://localhost:8000")
    print(f"Офлайн-режим HuggingFace: {'включён' if HF_OFFLINE else 'выключен'}")
    try:
        all_projects = db.list_projects()
        print(f"Проектов в БД: {len(all_projects)}")
        for p in all_projects:
            print(f"  - {p['project_id']}: {p['name']}")
    except Exception as e:
        print(f"Не удалось прочитать проекты из БД: {e}")
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8000)
