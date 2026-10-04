"""Состояние процесса: сессии чатов в памяти и реестр баз знаний проектов."""
import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)

sessions_store = {}

_knowledge_bases: Dict[str, "KnowledgeBase"] = {}
_kb_lock = threading.Lock()


def get_knowledge_base(project_id: str) -> "KnowledgeBase":
    """Одна база знаний на проект. Создание объекта никогда не запускает индексацию."""
    if not project_id:
        raise ValueError("Не указан проект")
    kb = _knowledge_bases.get(project_id)
    if kb is not None:
        return kb
    with _kb_lock:
        kb = _knowledge_bases.get(project_id)
        if kb is None:
            from rag.knowledge_base import KnowledgeBase
            kb = KnowledgeBase(project_id)
            _knowledge_bases[project_id] = kb
        return kb


def forget_knowledge_base(project_id: str) -> None:
    with _kb_lock:
        _knowledge_bases.pop(project_id, None)


def index_document(project_id: str, document_id: str) -> None:
    """Фоновая индексация одного документа (эмбеддинг + OpenSearch)."""
    try:
        get_knowledge_base(project_id).index_document(document_id)
    except Exception as e:
        logger.error(f"Индексация документа {document_id} проекта {project_id} не удалась: {e}", exc_info=True)


def index_testops_in_background(project_id: str) -> None:
    """После синхронизации: индексация кейсов и общих шагов TestOps проекта.
    Пересчитываются только изменившиеся записи; удалённые убираются из OpenSearch."""
    try:
        stats = get_knowledge_base(project_id).index_testops()
        logger.info(f"Фоновая индексация TestOps проекта {project_id} завершена: {stats}")
    except Exception as e:
        logger.error(f"Фоновая индексация TestOps проекта {project_id} не удалась: {e}", exc_info=True)


def requeue_interrupted_documents() -> None:
    """При старте: документы в pending/indexing (индексация прервана перезапуском) — снова в очередь."""
    from db import client as db

    try:
        docs = db.list_documents_by_status(["pending", "indexing"])
    except Exception as e:
        logger.error(f"Не удалось получить документы, ожидающие индексации: {e}")
        return
    if not docs:
        return

    logger.info(f"Документов с прерванной индексацией: {len(docs)} — ставлю в очередь")

    def worker():
        for doc in docs:
            db.set_document_status(doc["document_id"], "pending")
            index_document(doc["project_id"], doc["document_id"])

    threading.Thread(target=worker, name="documents-requeue", daemon=True).start()
