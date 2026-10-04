from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator

class ChatRequest(BaseModel):
    session_id: str
    message: str
    stream: bool = True
    temperature: float = 0.7
    images: Optional[List[str]] = None

class ChecklistGenerateRequest(BaseModel):
    project_id: str
    session_id: str
    tech_spec: str
    temperature: float = 0.3
    x: Optional[int] = None
    z: Optional[int] = None
    count: Optional[int] = None
    images: Optional[List[str]] = None

class SessionResponse(BaseModel):
    session_id: str
    project_id: str
    message: str

class ProjectInfo(BaseModel):
    id: str
    name: str
    has_knowledge: bool = False
    documents_count: int = 0
    testops_configured: bool = False

class TestOpsConnectionTestRequest(BaseModel):
    testops_url: str
    testops_api_key: str

class ProjectSwitchRequest(BaseModel):
    project_id: str
    sync_documents: bool = True

EXPORT_RESOLUTIONS = ("overwrite", "take_remote", "save_as_new", "skip")


class TestOpsExportRequest(BaseModel):
    session_id: str

    folder_path: str

    test_case_ids: Optional[List[str]] = None

    # Решения по конфликтам версий: {id кейса чата: действие}. Действия:
    # overwrite — перезаписать кейс в TestOps; take_remote — взять версию из TestOps
    # в чат (в TestOps ничего не пишется); save_as_new — создать новый кейс;
    # skip — пропустить. Кейс с конфликтом без решения не экспортируется.
    resolutions: Optional[Dict[str, str]] = None

    @field_validator("resolutions")
    @classmethod
    def _check_resolutions(cls, value):
        for case_id, action in (value or {}).items():
            if action not in EXPORT_RESOLUTIONS:
                raise ValueError(f"Неизвестное действие «{action}» для кейса {case_id}")
        return value

class TestOpsSyncExamplesRequest(BaseModel):
    project_id: str
    max_cases: Optional[int] = 5000
    sync_mode: str = "append"
    include_shared_steps: bool = True

class ProjectCreateRequest(BaseModel):
    id: str
    name: str
    testops_url: Optional[str] = None
    testops_project_id: Optional[int] = None

class UpdateChecklistRequest(BaseModel):
    session_id: str
    project_id: Optional[str] = None
    items: List[Dict[str, Any]]
    tech_spec: str

    selected_ids: Optional[List[str]] = None

    allow_new: bool = True

class ExtendChecklistRequest(BaseModel):
    session_id: str
    project_id: Optional[str] = None
    tech_spec: str = ""
    items: Optional[List[Dict[str, Any]]] = None
    x: Optional[int] = None
    z: Optional[int] = None
    count: Optional[int] = None

class SessionChecklistRequest(BaseModel):
    session_id: str
    checklist: List[Dict[str, Any]]

class SessionCloseRequest(BaseModel):
    session_id: str

class NewChatRequest(BaseModel):
    project_id: str

class ChatRenameRequest(BaseModel):
    title: str

class SearchTestCasesRequest(BaseModel):
    project_id: str
    session_id: str
    query: str

    x: Optional[int] = None
    z: Optional[int] = None

    offset: int = 0
    limit: Optional[int] = None

    debug: bool = False

class LoginRequest(BaseModel):
    login: str
    password: str
