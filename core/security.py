from typing import Any, Dict, Optional

from fastapi import Cookie, HTTPException

from db import client as db

ROLE_ADMIN = "admin"
ROLE_USER = "user"


def get_current_user(qa_session_token: Optional[str] = Cookie(default=None)):
    user = db.get_user_by_token(qa_session_token) if qa_session_token else None
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return user


def require_admin(user: Dict[str, Any]) -> None:
    if (user or {}).get("role") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Недостаточно прав для этого действия")


def require_project_access(project_id: Optional[str], user: Dict[str, Any]) -> None:
    if not project_id:
        raise HTTPException(status_code=400, detail="Не указан проект")
    if not db.user_has_project_access(user["id"], project_id):
        raise HTTPException(status_code=403, detail=f"Нет доступа к проекту {project_id}")


def get_project_or_404(project_id: Optional[str]) -> Dict[str, Any]:
    project = db.get_project(project_id) if project_id else None
    if not project:
        raise HTTPException(status_code=404, detail=f"Проект {project_id} не найден")
    return project


def get_testops_settings(project: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Настройки клиента TestOps: url и id проекта — из projects, ключ — текущего пользователя."""
    url = (project.get("testops_url") or "").strip()
    testops_project_id = project.get("testops_project_id")
    if not url or not testops_project_id:
        raise HTTPException(
            status_code=400,
            detail="TestOps не настроен для проекта: не заданы testops_url и testops_project_id в таблице projects",
        )
    api_key = ((user or {}).get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="У пользователя не задан API-ключ TestOps")
    return {"url": url, "api_key": api_key, "project_id": str(testops_project_id)}
