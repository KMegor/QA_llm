import hashlib
import logging
import re
import tempfile
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from core.schemas import ProjectCreateRequest, ProjectInfo, ProjectSwitchRequest, SessionResponse
from core.security import (
    get_current_user,
    get_project_or_404,
    require_admin,
    require_project_access,
)
from core.state import forget_knowledge_base, get_knowledge_base, index_document, sessions_store
from db import client as db
from rag import sparse_store
from rag.texts import CATEGORY_DOCS
from utils.file_processor import FileProcessor

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/projects")
async def get_projects(user=Depends(get_current_user)):
    project_list = []
    for proj in db.list_user_projects(user["id"]):
        doc_count = db.count_knowledge_items(proj["project_id"])
        testops_configured = bool(
            proj.get("testops_url") and proj.get("testops_project_id") and user.get("api_key")
        )
        project_list.append(ProjectInfo(
            id=proj["project_id"],
            name=proj["name"],
            has_knowledge=doc_count > 0,
            documents_count=doc_count,
            testops_configured=testops_configured
        ))
    logger.info(f"Отдано {len(project_list)} проектов")
    return JSONResponse([p.model_dump() for p in project_list])

@router.post("/api/projects/switch", response_model=SessionResponse)
async def switch_project(request: ProjectSwitchRequest, user=Depends(get_current_user)):
    project_id = request.project_id
    proj = get_project_or_404(project_id)
    require_project_access(project_id, user)

    session_id = str(uuid.uuid4())
    logger.info(f"Переключение на проект {project_id}")

    try:
        doc_count = db.count_knowledge_items(project_id)

        sessions_store[session_id] = {
            "project_id": request.project_id,
            "messages": [],
            "created_at": datetime.now().isoformat(),
        }

        message = f"Переключение на проект «{proj['name']}» выполнено. Загружено документов: {doc_count}."
        return SessionResponse(session_id=session_id, project_id=project_id, message=message)
    except Exception as e:
        logger.error(f"Ошибка переключения проекта: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/project/{project_id}/reindex")
def reindex_project(project_id: str, user=Depends(get_current_user)):
    require_admin(user)
    get_project_or_404(project_id)
    require_project_access(project_id, user)

    stats = get_knowledge_base(project_id).load_documents()

    message = (
        f"Индексация завершена: проиндексировано {stats['indexed']}, "
        f"без изменений {stats['skipped']}, удалено из индекса {stats['deleted']}"
    )
    if stats.get('failed'):
        message += f", с ошибками {stats['failed']}"
    return JSONResponse({
        "success": True,
        "message": message,
        "stats": stats,
    })

@router.post("/api/projects/create")
async def create_project(request: ProjectCreateRequest, user=Depends(get_current_user)):
    require_admin(user)
    if not db.create_project(request.id, request.name, request.testops_url, request.testops_project_id):
        raise HTTPException(400, f"Проект с ID {request.id} уже существует")

    db.grant_user_project_access(user["id"], request.id)

    return JSONResponse({
        "success": True,
        "message": f"Проект {request.name} создан",
        "project": {
            "id": request.id,
            "name": request.name,
            "testops_url": request.testops_url,
            "testops_project_id": request.testops_project_id,
        },
    })

@router.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, user=Depends(get_current_user)):
    require_admin(user)
    proj = get_project_or_404(project_id)
    require_project_access(project_id, user)

    # Кейсы, общие шаги, документы, эмбеддинги и чаты проекта удаляются каскадом.
    db.delete_project(project_id)
    forget_knowledge_base(project_id)
    try:
        sparse_store.reset(project_id)
    except Exception as e:
        logger.warning(f"Не удалось очистить полнотекстовый индекс проекта {project_id}: {e}")

    return JSONResponse({"success": True, "message": f"Проект {proj['name']} удалён"})

@router.post("/api/project/{project_id}/settings/testops")
async def update_testops_settings(
    project_id: str, testops_url: str, testops_project_id: Optional[int] = None,
    user=Depends(get_current_user),
):
    require_admin(user)
    get_project_or_404(project_id)
    require_project_access(project_id, user)

    try:
        db.update_project_testops(project_id, testops_url, testops_project_id)
        return JSONResponse({"success": True, "message": "Настройки TestOps обновлены"})
    except Exception as e:
        logger.error(f"Ошибка обновления настроек TestOps: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ===================== Документы проекта =====================

def _doc_to_json(d: dict) -> dict:
    def iso(value):
        return value.isoformat() if value else None
    return {
        "id": d["document_id"],
        "filename": d["filename"],
        "content_type": d["content_type"],
        "size_bytes": d["size_bytes"],
        "uploaded_by": d.get("uploaded_by_login"),
        "created_at": iso(d.get("created_at")),
        "updated_at": iso(d.get("updated_at")),
        "index_status": d["index_status"],
        "index_error": d.get("index_error"),
        "indexed_at": iso(d.get("indexed_at")),
    }

@router.get("/api/projects/{project_id}/docs")
async def list_project_docs(project_id: str, user=Depends(get_current_user)):
    get_project_or_404(project_id)
    require_project_access(project_id, user)
    return JSONResponse([_doc_to_json(d) for d in db.list_documents(project_id)])

def _extract_text(filename: str, file_data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = Path(tmp.name)
        return FileProcessor.extract_text(tmp_path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

def _process_upload(project_id: str, upload: UploadFile, user: dict, background_tasks: BackgroundTasks) -> dict:
    filename = Path(upload.filename or "").name
    result = {"filename": filename}
    try:
        ext = Path(filename).suffix.lower()
        if ext not in FileProcessor.SUPPORTED_EXTENSIONS:
            allowed = ", ".join(sorted(FileProcessor.SUPPORTED_EXTENSIONS))
            raise ValueError(f"Формат «{ext or filename}» не поддерживается. Допустимые расширения: {allowed}")

        file_data = upload.file.read()
        if not file_data:
            raise ValueError("Пустой файл")

        extracted_text = _extract_text(filename, file_data)
        if extracted_text.startswith("[Ошибка") or extracted_text.startswith("[Файл"):
            raise ValueError(extracted_text.strip("[]"))
        if not extracted_text.strip():
            raise ValueError("Из файла не удалось извлечь текст")

        sha256 = hashlib.sha256(file_data).hexdigest()
        content_type = upload.content_type or "application/octet-stream"

        existing = db.get_document_by_filename(project_id, filename)
        if existing and existing["sha256"] == sha256:
            result.update({"status": "unchanged", "id": existing["document_id"]})
            return result

        if existing:
            document_id = existing["document_id"]
            db.update_document_content(
                document_id, content_type, file_data, extracted_text, sha256, user["id"],
            )
        else:
            document_id = db.create_document(
                project_id, filename, content_type, file_data, extracted_text, sha256, user["id"],
            )

        background_tasks.add_task(index_document, project_id, document_id)
        result.update({"status": "queued", "id": document_id})
        return result
    except Exception as e:
        logger.warning(f"Файл «{filename}» не загружен: {e}")
        result.update({"status": "error", "error": str(e)})
        return result

@router.post("/api/projects/{project_id}/docs")
def upload_project_docs(
    project_id: str,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    user=Depends(get_current_user),
):
    get_project_or_404(project_id)
    require_project_access(project_id, user)

    results = [_process_upload(project_id, f, user, background_tasks) for f in files or []]
    return JSONResponse({"results": results})

def _content_disposition(filename: str) -> str:
    """attachment; filename="<ascii>"; filename*=UTF-8''<percent-encoded> — корректно для кириллицы."""
    def to_ascii(value: str) -> str:
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        return re.sub(r'[\\"\r\n;/]+', "_", value)

    path = Path(filename)
    stem = to_ascii(path.stem).strip(" ._") or "document"
    ascii_name = f"{stem}{to_ascii(path.suffix)}"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"

@router.get("/api/projects/{project_id}/docs/{doc_id}/file")
async def download_project_doc(project_id: str, doc_id: str, user=Depends(get_current_user)):
    require_project_access(project_id, user)
    doc = db.get_document_file(project_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")

    return Response(
        content=doc["file_data"],
        media_type=doc["content_type"] or "application/octet-stream",
        headers={"Content-Disposition": _content_disposition(doc["filename"])},
    )

@router.delete("/api/projects/{project_id}/docs/{doc_id}")
async def delete_project_doc(project_id: str, doc_id: str, user=Depends(get_current_user)):
    require_project_access(project_id, user)
    deleted = db.delete_document(project_id, doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Документ не найден")
    try:
        sparse_store.delete_doc(project_id, CATEGORY_DOCS, doc_id)
    except Exception as e:
        logger.warning(f"Документ {doc_id} удалён из БД, но не из полнотекстового индекса: {e}")
    return JSONResponse({"success": True})

@router.post("/api/projects/{project_id}/docs/{doc_id}/reindex")
async def reindex_project_doc(
    project_id: str, doc_id: str, background_tasks: BackgroundTasks, user=Depends(get_current_user),
):
    require_project_access(project_id, user)
    if not db.set_document_pending(project_id, doc_id):
        raise HTTPException(status_code=404, detail="Документ не найден")
    background_tasks.add_task(index_document, project_id, doc_id)
    return JSONResponse({"success": True, "status": "pending"})
