import base64
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from db import client as db
from core.config import GENERATE_REUSE_MIN_SCORE
from core.schemas import (
    ChecklistGenerateRequest,
    ExtendChecklistRequest,
    SearchTestCasesRequest,
    SessionChecklistRequest,
    UpdateChecklistRequest,
)
from core.security import get_current_user, require_project_access
from core.state import get_knowledge_base, sessions_store
from rag.knowledge_base import (
    KnowledgeBase,
    SearchRerankUnavailableError,
)
from routers.chats import REUSE_OFFER_KIND, reuse_offer_candidates
from tools.checklist_generator import ChecklistGenerationError
from tools.screenshot_describer import describe_message_images, screenshot_query
from utils.image_storage import decode_images
from utils.testcase_parser import search_results_to_checklist_items

logger = logging.getLogger(__name__)

router = APIRouter()

REUSE_PENDING = "pending"
REUSE_ACCEPTED = "accepted"
REUSE_DECLINED = "declined"

def _get_knowledge_base(project_id: str) -> KnowledgeBase:
    if not project_id:
        raise HTTPException(400, "Не удалось определить проект")
    return get_knowledge_base(project_id)

def _project_of(session_id: str, fallback: str = None) -> str:
    session = sessions_store.get(session_id) or {}
    return session.get("project_id") or fallback

def _normalize_count(count: Optional[int]) -> Optional[int]:
    return count if count and count > 0 else None

def _screenshot_query(message_id: Optional[int]) -> str:
    """Описание скриншотов сообщения с ТЗ как поисковый запрос (описания кэшируются в БД)."""
    return screenshot_query(describe_message_images(message_id)) if message_id else ""

def _find_existing_for_generation(kb: KnowledgeBase, tech_spec: str, x, z, count, image_query: str = ""):
    try:
        return kb.find_existing_test_cases(
            tech_spec,
            x=x,
            z=z,
            max_count=_normalize_count(count),
            extra_queries=[image_query] if image_query else None,
        )
    except SearchRerankUnavailableError as e:
        logger.warning(f"Проверка существующих кейсов перед генерацией пропущена: {e}")
        return []

def _reuse_offer_text(count: int) -> str:
    threshold_pct = int(round(GENERATE_REUSE_MIN_SCORE * 100))
    return (
        f"В базе уже есть тест-кейсы, похожие на запрос (совпадение от {threshold_pct}%): "
        f"{count}. Можно отредактировать их или сгенерировать новый чек-лист."
    )

def _run_generation(
    chat_id: str, project_id: str, tech_spec: str,
    images: Optional[List[str]], x, z, count, image_query: str = "",
) -> Dict[str, Any]:
    """Обычная генерация чек-листа: заменяет чек-лист чата и пишет итог в историю."""
    from tools.checklist_generator import GenerateChecklistTool

    kb = _get_knowledge_base(project_id)
    tool = GenerateChecklistTool(project_id=project_id, knowledge_base=kb)

    data = tool.generate(
        tech_spec=tech_spec,
        mode="generate",
        x=x,
        z=z,
        max_count=count,
        images=images,
        screenshot_query=image_query,
    )

    test_cases = db.sync_test_cases(chat_id, project_id, data.get("test_cases", []))
    data["test_cases"] = test_cases

    cases_count = len(test_cases)
    chat_summary = (
        f"Чек-лист сгенерирован! Найдено {cases_count} тест-кейсов.\n\n"
        f"Откройте панель чек-листа справа."
    )
    db.add_message(chat_id, "assistant", chat_summary)
    data["chat_summary"] = chat_summary
    data["reused_existing"] = False
    data["panel_mode"] = "checklist"
    return data

@router.post("/api/generate-checklist")
async def generate_checklist(request: ChecklistGenerateRequest, user=Depends(get_current_user)):
    try:
        require_project_access(request.project_id, user)

        tech_spec = request.tech_spec
        images = request.images if request.images else None

        db.ensure_chat_session_for_message(
            session_id=request.session_id,
            user_id=user["id"],
            project_id=request.project_id,
            first_user_message=tech_spec,
            mode="generate",
        )
        db.expire_pending_reuse_offers(request.session_id)
        tech_spec_message_id = db.add_message(request.session_id, "user", tech_spec)
        if images:
            decoded_images = decode_images(images)
            if decoded_images:
                db.add_message_images(tech_spec_message_id, decoded_images)

        kb = _get_knowledge_base(request.project_id)

        # Скриншот в поиск напрямую не попадает: сначала модель описывает его текстом,
        # и описание ищется отдельным запросом — и в проверке на похожие кейсы, и в RAG.
        image_query = _screenshot_query(tech_spec_message_id) if images else ""

        existing = _find_existing_for_generation(
            kb, tech_spec, request.x, request.z, request.count, image_query=image_query,
        )
        if existing:
            payload = {
                "status": REUSE_PENDING,
                "candidates": [
                    {"test_case_id": r["doc_id"], "score": r.get("score")}
                    for r in existing if r.get("doc_id")
                ],
                "request": {
                    "tech_spec_message_id": tech_spec_message_id,
                    "x": request.x,
                    "z": request.z,
                    "count": request.count,
                },
            }
            chat_summary = _reuse_offer_text(len(payload["candidates"]))
            message_id = db.add_message(
                request.session_id, "assistant", chat_summary,
                kind=REUSE_OFFER_KIND, payload=payload,
            )
            db.expire_pending_reuse_offers(request.session_id, except_message_id=message_id)
            return JSONResponse({
                "reuse_offer": {
                    "message_id": message_id,
                    "status": REUSE_PENDING,
                    "candidates": reuse_offer_candidates(request.project_id, payload),
                },
                "chat_summary": chat_summary,
                "panel_mode": "preview",
            })

        data = _run_generation(
            request.session_id, request.project_id, tech_spec, images,
            request.x, request.z, request.count, image_query=image_query,
        )
        return JSONResponse(data)
    except HTTPException:
        raise
    except ChecklistGenerationError as e:
        logger.error(f"Генерация чек-листа не удалась: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка генерации чек-листа: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def _get_pending_offer(message_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    message = db.get_message(message_id, user["id"])
    if not message or message.get("kind") != REUSE_OFFER_KIND:
        raise HTTPException(status_code=404, detail="Предложение не найдено")
    require_project_access(message["project_id"], user)
    if (message.get("payload") or {}).get("status") != REUSE_PENDING:
        raise HTTPException(status_code=409, detail="Предложение уже обработано или устарело")
    return message

def _claim_offer(message: Dict[str, Any], new_status: str) -> None:
    if not db.set_reuse_offer_status(message["id"], REUSE_PENDING, new_status):
        raise HTTPException(status_code=409, detail="Предложение уже обработано или устарело")

@router.post("/api/reuse-offer/{message_id}/accept")
async def accept_reuse_offer(message_id: int, user=Depends(get_current_user)):
    message = _get_pending_offer(message_id, user)
    _claim_offer(message, REUSE_ACCEPTED)
    chat_id, project_id = message["chat_id"], message["project_id"]
    try:
        payload = message.get("payload") or {}
        results = [
            {"doc_id": c.get("test_case_id"), "score": c.get("score")}
            for c in payload.get("candidates") or []
        ]
        # Кандидатов, которых уже нет в testops_test_cases, пропускаем.
        items = search_results_to_checklist_items(results, project_id)
        test_cases = db.sync_test_cases(chat_id, project_id, items)
        return JSONResponse({"test_cases": test_cases, "panel_mode": "search"})
    except Exception as e:
        db.set_reuse_offer_status(message["id"], REUSE_ACCEPTED, REUSE_PENDING)
        logger.error(f"Не удалось принять предложение {message_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/reuse-offer/{message_id}/decline")
async def decline_reuse_offer(message_id: int, user=Depends(get_current_user)):
    message = _get_pending_offer(message_id, user)
    _claim_offer(message, REUSE_DECLINED)
    chat_id, project_id = message["chat_id"], message["project_id"]

    try:
        db.set_chat_mode(chat_id, "generate")

        request_params = (message.get("payload") or {}).get("request") or {}
        spec_message = db.get_message(request_params.get("tech_spec_message_id"), user["id"])
        tech_spec = (spec_message or {}).get("content") or ""
        if not tech_spec.strip():
            raise HTTPException(status_code=400, detail="Не найдено ТЗ, по которому нужно сгенерировать чек-лист")

        images = [
            base64.b64encode(data).decode("ascii")
            for _, data in db.get_message_images_data(spec_message["id"])
        ] or None

        image_query = _screenshot_query(spec_message["id"]) if images else ""

        data = _run_generation(
            chat_id, project_id, tech_spec, images,
            request_params.get("x"), request_params.get("z"), request_params.get("count"),
            image_query=image_query,
        )
        return JSONResponse(data)
    except HTTPException:
        raise
    except ChecklistGenerationError as e:
        logger.error(f"Генерация чек-листа не удалась: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка генерации чек-листа: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/search-testcases")
async def search_testcases(request: SearchTestCasesRequest, user=Depends(get_current_user)):
    try:
        require_project_access(request.project_id, user)

        db.ensure_chat_session_for_message(
            session_id=request.session_id,
            user_id=user["id"],
            project_id=request.project_id,
            first_user_message=request.query,
            mode="search",
        )

        if request.offset == 0 and not request.limit:
            db.expire_pending_reuse_offers(request.session_id)
            db.add_message(request.session_id, "user", request.query)

        kb = _get_knowledge_base(request.project_id)
        data = kb.search_test_cases(
            request.query,
            x=request.x,
            z=request.z,
            offset=request.offset,
            limit=request.limit,
            debug=request.debug,
        )

        results = data.get("results", [])
        test_cases = search_results_to_checklist_items(results, request.project_id)
        if test_cases:
            test_cases = db.sync_test_cases(
                request.session_id, request.project_id, test_cases,
            )

        match_types = {r.get("doc_id"): r.get("match_type") for r in results if r.get("doc_id")}
        for item in test_cases:
            item["match_type"] = match_types.get(item.get("doc_id"))

        data["test_cases"] = test_cases
        data["chat_summary"] = _search_summary(data)

        if request.offset == 0 and not request.limit:
            db.add_message(request.session_id, "assistant", data["chat_summary"])

        return JSONResponse(data)
    except HTTPException:
        raise
    except SearchRerankUnavailableError as e:
        logger.error(f"Поиск по базе недоступен: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка поиска тест-кейсов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def _search_summary(data: dict) -> str:
    mode = data.get("mode")
    notices = " ".join(data.get("notices") or [])

    if mode == "empty":
        return "Пустой запрос — искать нечего."

    found = data.get("total_found", 0)
    shown = len(data.get("test_cases") or [])

    if not found:
        if mode == "dense":
            base = (
                "По этому запросу в базе не нашлось тест-кейсов с совпадением "
                f"от {int(KnowledgeBase.SEARCH_MIN_RERANK_SCORE * 100)}%. "
                "Попробуйте переформулировать запрос. Если ищете конкретную "
                "формулировку — возьмите её в кавычки, тогда поиск станет точным."
            )
        else:
            base = notices or "Ничего не нашлось."
            notices = ""
        return f"{base} {notices}".strip()

    parts = [f"Найдено: {found}."]
    exact, lemma = data.get("exact_count", 0), data.get("lemma_count", 0)
    if lemma:
        parts.append(f"Из них точных совпадений {exact}, по словоформам {lemma}.")
    if shown < found:
        parts.append(
            f"Показаны {shown} самых релевантных по свободным словам."
            if mode == "cascade"
            else f"Показаны первые {shown}."
        )
    parts.append("Результаты — в панели справа.")
    if notices:
        parts.append(notices)
    return " ".join(parts)

@router.post("/api/update-checklist")
async def update_checklist(request: UpdateChecklistRequest, user=Depends(get_current_user)):
    try:
        project_id = _project_of(request.session_id, request.project_id)
        require_project_access(project_id, user)
        if request.items:
            db.sync_test_cases(request.session_id, project_id, request.items)

        current_checklist = db.get_test_cases(request.session_id)
        if not current_checklist:
            raise HTTPException(404, "Чек-лист пуст — обновлять нечего")

        selected_ids = request.selected_ids
        if selected_ids is not None and not selected_ids:
            raise HTTPException(400, "Не отмечен ни один тест-кейс — обновлять нечего.")

        kb = _get_knowledge_base(project_id)

        from tools.checklist_generator import GenerateChecklistTool

        tool = GenerateChecklistTool(project_id=project_id, knowledge_base=kb)

        data = tool.generate(
            tech_spec=request.tech_spec,
            mode="update",
            current_checklist=current_checklist,
            allow_new=request.allow_new,
            editable_ids=set(selected_ids) if selected_ids is not None else None,
        )

        new_checklist = db.sync_test_cases(
            request.session_id, project_id, data.get("test_cases", []),
        )

        return JSONResponse({"test_cases": new_checklist})
    except HTTPException:
        raise
    except ChecklistGenerationError as e:
        logger.error(f"Обновление чек-листа не удалось: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка обновления чек-листа: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def _base_tech_spec_message(session_id: str) -> Optional[Dict[str, Any]]:
    """Первое сообщение пользователя в чате — исходное ТЗ (вместе с его скриншотами)."""
    try:
        for msg in db.get_messages(session_id):
            if msg.get("role") == "user" and msg.get("kind", "text") == "text" and msg.get("content"):
                return msg
    except Exception as e:
        logger.warning(f"Не удалось восстановить исходное ТЗ из истории чата: {e}")
    return None

@router.post("/api/extend-checklist")
async def extend_checklist(request: ExtendChecklistRequest, user=Depends(get_current_user)):
    try:
        from tools.checklist_generator import GenerateChecklistTool

        project_id = _project_of(request.session_id, request.project_id)
        require_project_access(project_id, user)

        if request.items:
            db.sync_test_cases(request.session_id, project_id, request.items)
        current_checklist = db.get_test_cases(request.session_id)

        if not current_checklist:
            raise HTTPException(400, "Чек-лист пуст — дополнять нечего. Сначала сгенерируйте его.")

        user_request = (request.tech_spec or "").strip()
        base_message = _base_tech_spec_message(request.session_id) or {}
        base_tech_spec = base_message.get("content") or ""
        # Новых скриншотов в «Дополнить» не прикрепить, но описания скриншотов исходного
        # ТЗ (если были) идут в подбор контекста и в запрос к модели.
        base_image_query = (
            _screenshot_query(base_message["id"])
            if base_message.get("id") and base_message.get("images") else ""
        )

        if not user_request and not base_tech_spec:
            raise HTTPException(
                400,
                "Опишите, какие тест-кейсы нужно добавить: исходное ТЗ этой сессии не найдено."
            )

        effective_request = user_request or (
            "Добавь новые тест-кейсы по исходному ТЗ, покрывающие сценарии, "
            "которых ещё нет в текущем чек-листе."
        )

        db.set_chat_mode(request.session_id, "generate")

        kb = _get_knowledge_base(project_id)
        tool = GenerateChecklistTool(project_id=project_id, knowledge_base=kb)

        data = tool.generate(
            tech_spec=effective_request,
            base_tech_spec=base_tech_spec,
            mode="extend",
            current_checklist=current_checklist,
            x=request.x,
            z=request.z,
            max_count=request.count,
            screenshot_query=base_image_query,
        )

        added_ids = [str(i) for i in data.get("added_ids", [])]
        new_checklist = db.sync_test_cases(
            request.session_id, project_id, data.get("test_cases", []),
        )

        added_count = len(added_ids)
        if added_count:
            chat_summary = (
                f"Чек-лист дополнен: добавлено {added_count} тест-кейсов. "
                f"Всего в чек-листе: {len(new_checklist)}."
            )
        else:
            chat_summary = (
                "Модель не предложила ни одного нового тест-кейса. "
                "Уточните требования и попробуйте снова."
            )

        try:
            db.add_message(request.session_id, "user", f"[Дополнение чек-листа] {effective_request}")
            db.add_message(request.session_id, "assistant", chat_summary)
        except Exception as e:
            logger.warning(f"Не удалось записать сообщения о дополнении в историю: {e}")

        return JSONResponse({
            "test_cases": new_checklist,
            "added_ids": added_ids,
            "added_count": added_count,
            "chat_summary": chat_summary,
        })

    except HTTPException:
        raise
    except ChecklistGenerationError as e:
        logger.error(f"Дополнение чек-листа не удалось: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка дополнения чек-листа: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/session/checklist")
async def save_session_checklist(request: SessionChecklistRequest, user=Depends(get_current_user)):
    session = db.get_chat_session(request.session_id, user["id"])
    if not session:
        raise HTTPException(404, "Чат не найден")

    items = db.sync_test_cases(
        request.session_id, session["project_id"], request.checklist,
    )
    return JSONResponse({"success": True, "test_cases": items})
