import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from db import client as db
from core.schemas import SessionCloseRequest, ChatRenameRequest, NewChatRequest
from core.security import get_current_user, get_project_or_404, require_project_access
from core.state import sessions_store
from utils.testcase_parser import testops_row_to_checklist_item

import logging

logger = logging.getLogger(__name__)

router = APIRouter()

REUSE_OFFER_KIND = "reuse_offer"

def _panel_mode(chat_mode: str, test_cases) -> str:
    if chat_mode == "search":
        return "search"
    if any(case.get("origin") == "search" for case in test_cases):
        return "search"
    return "checklist"

def reuse_offer_candidates(project_id: str, payload: dict) -> list:
    """Кандидаты предложения — кейсы из testops_test_cases в формате элемента чек-листа + score."""
    candidates = (payload or {}).get("candidates") or []
    rows = db.get_testops_test_cases_by_ids(project_id, [c.get("test_case_id") for c in candidates])
    result = []
    for candidate in candidates:
        row = rows.get(str(candidate.get("test_case_id")))
        if not row:
            continue
        result.append(testops_row_to_checklist_item(row, score=candidate.get("score")))
    return result

@router.get("/api/chats")
async def get_chats(user=Depends(get_current_user)):
    chats = db.list_chats(user["id"])
    return JSONResponse([
        {
            "id": str(c["id"]),
            "project_id": c["project_id"],
            "title": c["title"] or "Новый чат",
            "mode": c.get("mode") or "chat",
            "created_at": c["created_at"].isoformat(),
            "updated_at": c["updated_at"].isoformat(),
        }
        for c in chats
    ])

@router.post("/api/chats/new")
async def create_chat(request: NewChatRequest, user=Depends(get_current_user)):
    get_project_or_404(request.project_id)
    require_project_access(request.project_id, user)

    session_id = str(uuid.uuid4())
    sessions_store[session_id] = {
        "project_id": request.project_id,
        "messages": [],
        "created_at": datetime.now().isoformat(),
    }

    logger.info(f"Создан новый чат {session_id} для проекта {request.project_id}")
    return JSONResponse({"session_id": session_id, "project_id": request.project_id})

@router.get("/api/chats/{chat_id}")
async def open_chat(chat_id: str, user=Depends(get_current_user)):
    row = db.get_chat_session(chat_id, user["id"])
    if not row:
        raise HTTPException(status_code=404, detail="Чат не найден")

    project_id = row["project_id"]
    get_project_or_404(project_id)

    messages_db = db.get_messages(chat_id)
    test_cases = db.get_test_cases(chat_id)

    llm_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages_db if m.get("kind", "text") == "text"
    ]

    response_messages = []
    last_offer_status = None
    for m in messages_db:
        message = {
            "id": m["id"],
            "role": m["role"],
            "kind": m.get("kind") or "text",
            "payload": m.get("payload"),
            "content": m["content"],
            "images": [
                f"/api/chats/{chat_id}/images/{img['id']}" for img in m.get("images", [])
            ],
        }
        if message["kind"] == REUSE_OFFER_KIND:
            status = (m.get("payload") or {}).get("status")
            last_offer_status = status
            if status == "pending":
                message["candidates"] = reuse_offer_candidates(project_id, m.get("payload"))
        response_messages.append(message)

    sessions_store[chat_id] = {
        "project_id": project_id,
        "messages": llm_messages,
        "created_at": row["created_at"].isoformat(),
    }

    panel_mode = "preview" if last_offer_status == "pending" else _panel_mode(row.get("mode"), test_cases)

    return JSONResponse({
        "session_id": chat_id,
        "project_id": project_id,
        "mode": row.get("mode") or "chat",
        "panel_mode": panel_mode,
        "messages": response_messages,
        "test_cases": test_cases,
    })

@router.get("/api/chats/{chat_id}/images/{image_id}")
async def get_chat_message_image(chat_id: str, image_id: int, user=Depends(get_current_user)):
    row = db.get_chat_session(chat_id, user["id"])
    if not row:
        raise HTTPException(status_code=404, detail="Чат не найден")

    image = db.get_message_image(image_id, user["id"])
    if not image:
        raise HTTPException(status_code=404, detail="Изображение не найдено")

    return Response(content=image["data"], media_type=image["mime_type"])

@router.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, user=Depends(get_current_user)):
    deleted = db.delete_chat_session(chat_id, user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Чат не найден")
    sessions_store.pop(chat_id, None)
    return JSONResponse({"success": True})

@router.patch("/api/chats/{chat_id}")
async def rename_chat(chat_id: str, request: ChatRenameRequest, user=Depends(get_current_user)):
    title = request.title.strip()[:200]
    if not title:
        raise HTTPException(status_code=422, detail="Название чата не может быть пустым")
    renamed = db.rename_chat_session(chat_id, user["id"], title)
    if not renamed:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return JSONResponse({"success": True, "title": title})

@router.post("/api/session/close")
async def close_session(request: SessionCloseRequest, user=Depends(get_current_user)):
    sessions_store.pop(request.session_id, None)
    logger.info(f"Сессия {request.session_id} закрыта (данные сохранены в БД)")
    return JSONResponse({"success": True, "message": "Сессия завершена"})
