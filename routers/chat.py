import json
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from db import client as db
from core.config import MAX_IMAGES, OLLAMA_URL
from core.schemas import ChatRequest
from core.security import get_current_user
from core.state import get_knowledge_base, sessions_store
from tools.ollama_client import build_chat_payload
from tools.prompts.chat_system_message import build_chat_system_message
from utils.image_storage import decode_images

logger = logging.getLogger(__name__)

router = APIRouter()

OLLAMA_CHAT_URL = f"{OLLAMA_URL}/api/chat"

CHAT_TOP_K_DOCS = 4
CHAT_TOP_K_EXAMPLES = 2
CHAT_TOP_K_SHARED = 1

CHAT_HISTORY_LIMIT = 12

def _project_name(project_id: Optional[str]) -> str:
    proj = db.get_project(project_id) if project_id else None
    return proj["name"] if proj else (project_id or "проект")

def _get_knowledge_base(session: Dict[str, Any]):
    project_id = session.get("project_id")
    if not project_id:
        return None
    try:
        return get_knowledge_base(project_id)
    except Exception as e:
        logger.error(f"Не удалось поднять базу знаний для проекта {project_id}: {e}")
        return None

def _build_context_block(kb, query: str) -> str:
    if not kb or not query.strip():
        return ""

    try:
        context = kb.get_rag_context(
            query=query,
            top_k_docs=CHAT_TOP_K_DOCS,
            top_k_examples=CHAT_TOP_K_EXAMPLES,
            top_k_shared=CHAT_TOP_K_SHARED,
        )
    except Exception as e:
        logger.error(f"Ошибка получения RAG-контекста для чата: {e}")
        return ""

    parts: List[str] = []

    if context.get("docs"):
        block = "=== ДОКУМЕНТАЦИЯ ПРОЕКТА ===\n"
        for i, item in enumerate(context["docs"], 1):
            block += f"[D{i}] {item['content']}\n(Источник: {item['source']})\n\n"
        parts.append(block)

    if context.get("shared_steps"):
        block = "=== SHARED STEPS ===\n"
        for i, item in enumerate(context["shared_steps"], 1):
            block += f"[S{i}] {item['content']}\n(Источник: {item['source']})\n\n"
        parts.append(block)

    if context.get("examples"):
        block = "=== ПРИМЕРЫ ТЕСТ-КЕЙСОВ ПРОЕКТА ===\n"
        for i, item in enumerate(context["examples"], 1):
            block += f"[E{i}] {item['content']}\n(Источник: {item['source']})\n\n"
        parts.append(block)

    if not parts:
        logger.info("RAG-контекст для чата пуст: релевантных фрагментов не нашлось")
        return ""

    return "".join(parts)

@router.post("/api/chat")
async def chat(request: ChatRequest, user=Depends(get_current_user)):
    session = sessions_store.get(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")

    db.ensure_chat_session_for_message(
        session_id=request.session_id,
        user_id=user["id"],
        project_id=session.get("project_id"),
        first_user_message=request.message,
        mode="chat",
    )
    user_message_id = db.add_message(request.session_id, "user", request.message)

    images = request.images or None
    if images:
        decoded_images = decode_images(images)
        if decoded_images:
            db.add_message_images(user_message_id, decoded_images)
        if len(images) > MAX_IMAGES:
            logger.warning(f"Ограничение числа изображений до {MAX_IMAGES}")
            images = images[:MAX_IMAGES]

    kb = _get_knowledge_base(session)
    context_block = _build_context_block(kb, request.message)

    history: List[Dict[str, Any]] = session.get("messages", [])
    trimmed_history = history[-CHAT_HISTORY_LIMIT:] if CHAT_HISTORY_LIMIT else history

    user_content = (
        f"{context_block}ВОПРОС ПОЛЬЗОВАТЕЛЯ:\n{request.message}"
        if context_block else request.message
    )

    llm_messages = list(trimmed_history) + [{"role": "user", "content": user_content}]
    system_message = build_chat_system_message(_project_name(session.get("project_id")))

    history.append({"role": "user", "content": request.message})
    session["messages"] = history

    payload = build_chat_payload(
        system_message=system_message,
        messages=llm_messages,
        temperature=request.temperature,
        images=images,
        stream=request.stream,
    )

    if request.stream:
        async def generate():
            full_response = ""
            try:
                async with httpx.AsyncClient(timeout=600.0) as client:
                    async with client.stream("POST", OLLAMA_CHAT_URL, json=payload) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError:
                                logger.warning(f"Не удалось разобрать чанк стрима: {line[:200]}")
                                continue

                            chunk = (data.get("message", {}) or {}).get("content", "")
                            if chunk:
                                full_response += chunk
                                yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                            if data.get("done"):
                                break

                history.append({"role": "assistant", "content": full_response})
                session["messages"] = history
                db.add_message(request.session_id, "assistant", full_response)
                yield f"data: {json.dumps({'content': '', 'done': True})}\n\n"
            except httpx.ConnectError:
                logger.error("Не удалось подключиться к Ollama")
                yield f"data: {json.dumps({'error': 'Не удалось подключиться к Ollama'})}\n\n"
            except Exception as e:
                logger.error(f"Ошибка стриминга чата: {e}", exc_info=True)
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    try:
        with httpx.Client(timeout=600.0) as client:
            response = client.post(OLLAMA_CHAT_URL, json=payload)
            response.raise_for_status()
            data = response.json()

        message = data.get("message", {}) or {}
        full_response = (message.get("content") or "").strip()
        if not full_response:
            thinking = (message.get("thinking") or "").strip()
            if thinking:
                logger.warning("message.content пуст, использую message.thinking (баг Ollama #14716)")
                full_response = thinking

        history.append({"role": "assistant", "content": full_response})
        session["messages"] = history
        db.add_message(request.session_id, "assistant", full_response)
        return JSONResponse({"response": full_response, "done": True})
    except httpx.ConnectError:
        logger.error("Не удалось подключиться к Ollama")
        raise HTTPException(status_code=503, detail="Не удалось подключиться к Ollama")
    except Exception as e:
        logger.error(f"Ошибка чата: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
