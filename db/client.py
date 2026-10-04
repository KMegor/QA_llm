"""Слой данных (PostgreSQL).

Имена колонок в БД следуют новой схеме (chat_id, test_case_id, user_id, ...),
а словари, которые уходят во фронтенд, сохраняют прежние имена полей
(id, session_id, origin, doc_id, ...). Все переименования — здесь.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from core.config import DATABASE_URL
from utils.params_convert import normalize_params
from utils.steps_convert import normalize_steps, steps_equal

logger = logging.getLogger(__name__)


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_uuid(value: Any) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _uuid_list(values: Iterable[Any]) -> List[str]:
    return [u for u in (_as_uuid(v) for v in values or []) if u]


def _jsonb(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value

# ===================== USERS / AUTH =====================

def verify_login(login: str, password: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id AS id, login, role
            FROM users WHERE login = %s AND password = %s
            """,
            (login, password),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def create_session_token(user_id: int) -> str:
    token = uuid.uuid4().hex
    expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO auth_tokens (token, user_id, expires_at)
            VALUES (%s, %s, %s)
            """,
            (token, user_id, expires_at),
        )
    return token


def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.user_id AS id, u.login, u.role, u.api_key
            FROM auth_tokens AS t
            JOIN users AS u ON u.user_id = t.user_id
            WHERE t.token = %s
            """,
            (token,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def delete_token(token: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM auth_tokens WHERE token = %s", (token,))


def delete_all_session_tokens() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM auth_tokens")

# ===================== PROJECTS =====================

_PROJECT_COLUMNS = "project_id, name, testops_url, testops_project_id, created_at"


def list_projects() -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_PROJECT_COLUMNS} FROM projects ORDER BY created_at, project_id")
        return [dict(r) for r in cur.fetchall()]


def count_projects() -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM projects")
        return int(cur.fetchone()["n"])


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    if not project_id:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE project_id = %s", (project_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_user_projects(user_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.project_id, p.name, p.testops_url, p.testops_project_id, p.created_at
            FROM user_projects AS up
            JOIN projects AS p ON p.project_id = up.project_id
            WHERE up.user_id = %s
            ORDER BY up.created_at, p.project_id
            """,
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def create_project(
    project_id: str, name: str,
    testops_url: Optional[str] = None, testops_project_id: Optional[int] = None,
) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO projects (project_id, name, testops_url, testops_project_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (project_id) DO NOTHING
            """,
            (project_id, name, testops_url, _as_int(testops_project_id)),
        )
        return cur.rowcount > 0


def delete_project(project_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE project_id = %s", (project_id,))
        return cur.rowcount > 0


def update_project_testops(
    project_id: str, testops_url: Optional[str], testops_project_id: Optional[int],
) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE projects SET testops_url = %s, testops_project_id = %s
            WHERE project_id = %s
            """,
            (testops_url, _as_int(testops_project_id), project_id),
        )
        return cur.rowcount > 0


def get_user_project_ids(user_id: int) -> List[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT project_id FROM user_projects WHERE user_id = %s ORDER BY created_at",
            (user_id,),
        )
        return [row["project_id"] for row in cur.fetchall()]


def user_has_project_access(user_id: int, project_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM user_projects WHERE user_id = %s AND project_id = %s",
            (user_id, project_id),
        )
        return cur.fetchone() is not None


def grant_user_project_access(user_id: int, project_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_projects (user_id, project_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id, project_id) DO NOTHING
            """,
            (user_id, project_id),
        )


def revoke_user_project_access(user_id: int, project_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM user_projects WHERE user_id = %s AND project_id = %s",
            (user_id, project_id),
        )

# ===================== CHATS =====================

def list_chats(user_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT chat_id AS id, project_id, title, mode, created_at, updated_at
            FROM chats
            WHERE user_id = %s
            ORDER BY updated_at DESC
            """,
            (user_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def ensure_chat_session_for_message(
    session_id: str,
    user_id: int,
    project_id: str,
    first_user_message: str,
    mode: str = "chat",
) -> None:
    """Создаёт чат при первом сообщении; для существующего — обновляет режим (последний режим работы)."""
    title = (first_user_message or "").strip()[:200] or "Новый чат"
    if len((first_user_message or "").strip()) > 200:
        title = title[:199] + "…"

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chats (chat_id, user_id, project_id, title, mode)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE
            SET title = COALESCE(chats.title, EXCLUDED.title),
                mode = EXCLUDED.mode,
                updated_at = NOW()
            """,
            (session_id, user_id, project_id, title, mode),
        )


def set_chat_mode(chat_id: str, mode: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE chats SET mode = %s, updated_at = NOW() WHERE chat_id = %s",
            (mode, chat_id),
        )


def get_chat_session(chat_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    if not _as_uuid(chat_id):
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT chat_id AS id, user_id, project_id, created_at,
                   updated_at, title, mode
            FROM chats
            WHERE chat_id = %s AND user_id = %s
            """,
            (chat_id, user_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def delete_chat_session(chat_id: str, user_id: int) -> bool:
    if not _as_uuid(chat_id):
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM chats WHERE chat_id = %s AND user_id = %s",
            (chat_id, user_id),
        )
        return cur.rowcount > 0


def rename_chat_session(chat_id: str, user_id: int, title: str) -> bool:
    if not _as_uuid(chat_id):
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE chats SET title = %s WHERE chat_id = %s AND user_id = %s",
            (title, chat_id, user_id),
        )
        return cur.rowcount > 0

# ===================== MESSAGES =====================

def add_message(
    chat_id: str, role: str, content: str,
    kind: str = "text", payload: Optional[Dict[str, Any]] = None,
) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO messages (chat_id, role, kind, payload, content)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING message_id
            """,
            (chat_id, role, kind, Json(payload) if payload is not None else None, content or ""),
        )
        message_id = cur.fetchone()["message_id"]
        cur.execute(
            "UPDATE chats SET updated_at = NOW() WHERE chat_id = %s",
            (chat_id,),
        )
        return message_id


def add_message_images(message_id: int, images: List[Tuple[str, bytes]]) -> None:
    if not images:
        return
    with get_conn() as conn, conn.cursor() as cur:
        for position, (mime_type, data) in enumerate(images):
            cur.execute(
                """
                INSERT INTO message_images (message_id, position, mime_type, data)
                VALUES (%s, %s, %s, %s)
                """,
                (message_id, position, mime_type, psycopg2.Binary(data)),
            )


def get_messages(chat_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT message_id AS id, role, kind, payload, content, created_at
            FROM messages
            WHERE chat_id = %s
            ORDER BY created_at ASC, message_id ASC
            """,
            (chat_id,),
        )
        messages = [dict(row) for row in cur.fetchall()]
        if not messages:
            return messages

        message_ids = [m["id"] for m in messages]
        cur.execute(
            """
            SELECT id, message_id, mime_type
            FROM message_images
            WHERE message_id = ANY(%s)
            ORDER BY message_id, position, id
            """,
            (message_ids,),
        )
        images_by_message: Dict[int, List[Dict[str, Any]]] = {}
        for row in cur.fetchall():
            images_by_message.setdefault(row["message_id"], []).append(
                {"id": row["id"], "mime_type": row["mime_type"]}
            )

        for m in messages:
            m["payload"] = _jsonb(m.get("payload"))
            m["images"] = images_by_message.get(m["id"], [])
        return messages


def get_message(message_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Сообщение из чата текущего пользователя (иначе None)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.message_id AS id, m.chat_id, m.role, m.kind, m.payload, m.content,
                   m.created_at, c.project_id, c.user_id
            FROM messages AS m
            JOIN chats AS c ON c.chat_id = m.chat_id
            WHERE m.message_id = %s AND c.user_id = %s
            """,
            (message_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        item = dict(row)
        item["chat_id"] = str(item["chat_id"])
        item["payload"] = _jsonb(item.get("payload"))
        return item


def get_message_images_data(message_id: int) -> List[Tuple[str, bytes]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT mime_type, data FROM message_images
            WHERE message_id = %s ORDER BY position, id
            """,
            (message_id,),
        )
        return [(row["mime_type"], bytes(row["data"])) for row in cur.fetchall()]


def get_message_images_for_description(message_id: int) -> List[Dict[str, Any]]:
    """Скриншоты сообщения с уже сохранёнными описаниями (description = None — ещё не описан)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, mime_type, data, description FROM message_images
            WHERE message_id = %s ORDER BY position, id
            """,
            (message_id,),
        )
        return [
            {
                "id": row["id"],
                "mime_type": row["mime_type"],
                "data": bytes(row["data"]),
                "description": row["description"],
            }
            for row in cur.fetchall()
        ]


def set_message_image_description(image_id: int, description: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE message_images SET description = %s WHERE id = %s",
            (description, image_id),
        )


def update_message_payload(message_id: int, payload: Dict[str, Any]) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE messages SET payload = %s WHERE message_id = %s",
            (Json(payload), message_id),
        )


def set_reuse_offer_status(message_id: int, expected_status: str, new_status: str) -> bool:
    """Атомарно меняет статус предложения, если он сейчас равен expected_status."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE messages
            SET payload = jsonb_set(payload, '{status}', to_jsonb(%s::text))
            WHERE message_id = %s AND kind = 'reuse_offer'
              AND payload->>'status' = %s
            """,
            (new_status, message_id, expected_status),
        )
        return cur.rowcount > 0


def expire_pending_reuse_offers(chat_id: str, except_message_id: Optional[int] = None) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE messages
            SET payload = jsonb_set(payload, '{status}', '"expired"'::jsonb)
            WHERE chat_id = %s AND kind = 'reuse_offer'
              AND payload->>'status' = 'pending'
              AND (%s::bigint IS NULL OR message_id <> %s::bigint)
            """,
            (chat_id, except_message_id, except_message_id),
        )
        return cur.rowcount


def get_message_image(image_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT i.data, i.mime_type
            FROM message_images AS i
            JOIN messages AS m ON m.message_id = i.message_id
            JOIN chats AS c ON c.chat_id = m.chat_id
            WHERE i.id = %s AND c.user_id = %s
            """,
            (image_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        item = dict(row)
        item["data"] = bytes(item["data"])
        return item

# ===================== TEST CASES (чек-лист чата) =====================

_TEST_CASE_COLUMNS = """
    test_case_id, chat_id, project_id, source_test_case_id, testops_id, case_num,
    title, description, steps, expected_result, tags, workflow, status,
    origin_type, score, selected, edited_since_search, needs_export, base_modified_at, position
"""


def _row_to_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """Строка test_cases -> JSON для фронтенда (прежние имена полей)."""
    case_num = row.get("case_num")
    source_id = row.get("source_test_case_id")
    return {
        "id": str(row["test_case_id"]),
        "session_id": str(row["chat_id"]),
        "project_id": row["project_id"],
        "testops_id": row.get("testops_id"),
        "case_num": case_num,
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "steps": normalize_steps(_jsonb(row.get("steps"))),
        "expected_result": row.get("expected_result") or "",
        "tags": list(row.get("tags") or []),
        "workflow": row.get("workflow") or "",
        "status": row.get("status") or "",
        "origin": row.get("origin_type") or "generated",
        "source": f"{case_num}.md" if case_num else None,
        "doc_id": str(source_id) if source_id else None,
        "score": row.get("score"),
        "selected": bool(row.get("selected")),
        "edited_since_search": bool(row.get("edited_since_search")),
        "needs_export": bool(row.get("needs_export")),
        "base_modified_at": row.get("base_modified_at"),
        "position": row.get("position", 0),
    }


def get_test_cases(chat_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_TEST_CASE_COLUMNS}
            FROM test_cases
            WHERE chat_id = %s
            ORDER BY position ASC, created_at ASC
            """,
            (chat_id,),
        )
        return [_row_to_item(row) for row in cur.fetchall()]


def get_test_cases_by_ids(chat_id: str, ids: List[str]) -> List[Dict[str, Any]]:
    ids = _uuid_list(ids)
    if not ids:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_TEST_CASE_COLUMNS}
            FROM test_cases
            WHERE chat_id = %s AND test_case_id = ANY(%s::uuid[])
            ORDER BY position ASC, created_at ASC
            """,
            (chat_id, ids),
        )
        return [_row_to_item(row) for row in cur.fetchall()]


def _tags(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return [str(t) for t in value]


def sync_test_cases(
    chat_id: str,
    project_id: str,
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Заменяет чек-лист чата присланным списком (порядок = порядок в списке)."""
    items = items or []

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_TEST_CASE_COLUMNS} FROM test_cases WHERE chat_id = %s",
            (chat_id,),
        )
        existing = {str(row["test_case_id"]): dict(row) for row in cur.fetchall()}

        # testops_id и case_num новых строк клиенту не доверяем: берём их из
        # testops_test_cases по ссылке на исходный кейс базы знаний. Нет ссылки —
        # кейс новый, в TestOps его ещё нет (id появится только при экспорте).
        source_ids = _uuid_list(
            item.get("doc_id") or item.get("source_test_case_id")
            for item in items
            if _as_uuid(item.get("id")) not in existing
        )
        source_rows: Dict[str, Dict[str, Any]] = {}
        if source_ids:
            cur.execute(
                """
                SELECT test_case_id, testops_id, case_num, testops_modified_at FROM testops_test_cases
                WHERE project_id = %s AND test_case_id = ANY(%s::uuid[])
                """,
                (project_id, source_ids),
            )
            source_rows = {str(r["test_case_id"]): dict(r) for r in cur.fetchall()}

        incoming_ids = []
        for position, item in enumerate(items):
            item_id = _as_uuid(item.get("id"))
            if not item_id:
                logger.warning("Тест-кейс без корректного id пропущен при сохранении: %r", item.get("title"))
                continue
            if item_id in incoming_ids:
                logger.warning("Повтор тест-кейса %s в чек-листе пропущен", item_id)
                continue
            incoming_ids.append(item_id)

            title = (item.get("title") or "").strip()
            steps = normalize_steps(item.get("steps"))
            expected = (item.get("expected_result") or "").strip()
            old = existing.get(item_id)

            if old is None:
                origin = item.get("origin") or item.get("origin_type") or "generated"
                if origin not in ("generated", "search"):
                    origin = "generated"
                source = source_rows.get(
                    _as_uuid(item.get("doc_id") or item.get("source_test_case_id")) or ""
                )
                testops_id = source["testops_id"] if source else None
                case_num = source["case_num"] if source else None
                if item.get("testops_id") is not None and _as_int(item.get("testops_id")) != testops_id:
                    logger.warning(
                        "Клиент прислал testops_id=%r для нового кейса %s, сервер определил %r — "
                        "используется серверное значение",
                        item.get("testops_id"), item_id, testops_id,
                    )
                cur.execute(
                    """
                    INSERT INTO test_cases
                        (test_case_id, chat_id, project_id, source_test_case_id,
                         testops_id, case_num, title, description, steps,
                         expected_result, tags, workflow, status, origin_type,
                         score, selected, edited_since_search, needs_export,
                         base_modified_at, position)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        item_id, chat_id, project_id,
                        str(source["test_case_id"]) if source else None,
                        testops_id, case_num,
                        title, (item.get("description") or ""), Json(steps),
                        expected, _tags(item.get("tags")), (item.get("workflow") or ""),
                        (item.get("status") or ""), origin,
                        _as_float(item.get("score")),
                        bool(item.get("selected", False)),
                        bool(item.get("edited_since_search", False)),
                        # Кейса нет в TestOps — экспортировать нужно в любом случае.
                        True if testops_id is None else bool(item.get("needs_export", True)),
                        # Версия, от которой пользователь начинает правку, — из базы знаний.
                        source["testops_modified_at"] if source else None,
                        position,
                    ),
                )
                continue

            content_changed = (
                title != (old["title"] or "")
                or not steps_equal(steps, _jsonb(old["steps"]))
                or expected != (old["expected_result"] or "")
            )
            needs_export = bool(old["needs_export"]) or content_changed
            edited = bool(old["edited_since_search"]) or (
                content_changed and (old["origin_type"] == "search")
            )

            cur.execute(
                """
                UPDATE test_cases
                SET title = %s,
                    description = %s,
                    steps = %s,
                    expected_result = %s,
                    tags = %s,
                    workflow = %s,
                    status = %s,
                    selected = %s,
                    edited_since_search = %s,
                    needs_export = %s,
                    position = %s,
                    updated_at = NOW()
                WHERE test_case_id = %s
                """,
                (
                    title,
                    item["description"] if "description" in item else old["description"],
                    Json(steps),
                    expected,
                    _tags(item["tags"]) if "tags" in item else list(old["tags"] or []),
                    item["workflow"] if "workflow" in item else old["workflow"],
                    (item.get("status") or old["status"] or ""),
                    bool(item.get("selected", old["selected"])),
                    edited, needs_export, position, item_id,
                ),
            )

        stale = [case_id for case_id in existing if case_id not in set(incoming_ids)]
        if stale:
            cur.execute(
                "DELETE FROM test_cases WHERE chat_id = %s AND test_case_id = ANY(%s::uuid[])",
                (chat_id, stale),
            )
            logger.info("Из чек-листа %s убрано тест-кейсов: %d", chat_id, len(stale))

        cur.execute("UPDATE chats SET updated_at = NOW() WHERE chat_id = %s", (chat_id,))

    return get_test_cases(chat_id)


def mark_test_case_exported(
    case_id: str, testops_id: int,
    case_num: Optional[str] = None, source_test_case_id: Optional[str] = None,
    base_modified_at: Optional[int] = None,
) -> None:
    """base_modified_at — lastModifiedDate кейса в TestOps сразу после нашей записи:
    следующий экспорт из этого чата не примет нашу же правку за чужую."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE test_cases
            SET testops_id = %s,
                case_num = COALESCE(%s, case_num),
                source_test_case_id = COALESCE(
                    (SELECT test_case_id FROM testops_test_cases WHERE test_case_id = %s::uuid),
                    source_test_case_id),
                base_modified_at = %s,
                needs_export = FALSE,
                updated_at = NOW()
            WHERE test_case_id = %s
            """,
            (testops_id, case_num, _as_uuid(source_test_case_id), base_modified_at, case_id),
        )


def apply_remote_version(
    case_id: str, title: str, steps: Any, expected_result: str,
    base_modified_at: Optional[int], source_test_case_id: Optional[str] = None,
) -> None:
    """«Взять версию из TestOps»: правки пользователя заменяются содержимым кейса из TestOps."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE test_cases
            SET title = %s,
                steps = %s,
                expected_result = %s,
                base_modified_at = %s,
                source_test_case_id = COALESCE(
                    (SELECT test_case_id FROM testops_test_cases WHERE test_case_id = %s::uuid),
                    source_test_case_id),
                needs_export = FALSE,
                edited_since_search = FALSE,
                updated_at = NOW()
            WHERE test_case_id = %s
            """,
            (title or "", Json(normalize_steps(steps)), expected_result or "",
             base_modified_at, _as_uuid(source_test_case_id), case_id),
        )

# ===================== KNOWLEDGE BASE: кейсы TestOps =====================

_TTC_COLUMNS = """
    test_case_id, project_id, testops_id, case_num, section_x, test_type_z,
    title, description, steps, expected_result, expected_result_block, parameters,
    tags, workflow, status, testops_modified_at, testops_modified_by, created_at, updated_at
"""


def _ttc_row(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["test_case_id"] = str(item["test_case_id"])
    item["id"] = item["test_case_id"]
    item["steps"] = normalize_steps(_jsonb(item.get("steps")))
    item["parameters"] = normalize_params(_jsonb(item.get("parameters")))
    item["tags"] = list(item.get("tags") or [])
    return item


def list_testops_test_cases(project_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_TTC_COLUMNS} FROM testops_test_cases
            WHERE project_id = %s
            ORDER BY case_num, created_at
            """,
            (project_id,),
        )
        return [_ttc_row(r) for r in cur.fetchall()]


def get_testops_test_cases_by_ids(project_id: str, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    ids = _uuid_list(ids)
    if not ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_TTC_COLUMNS} FROM testops_test_cases
            WHERE project_id = %s AND test_case_id = ANY(%s::uuid[])
            """,
            (project_id, ids),
        )
        return {str(r["test_case_id"]): _ttc_row(r) for r in cur.fetchall()}


def get_testops_test_case(project_id: str, test_case_id: str) -> Optional[Dict[str, Any]]:
    return get_testops_test_cases_by_ids(project_id, [test_case_id]).get(_as_uuid(test_case_id) or "")


def upsert_testops_test_case(
    project_id: str,
    testops_id: int,
    case_num: str,
    title: str,
    description: str,
    steps: Any,
    expected_result: str,
    tags: List[str],
    status: str,
    workflow: str = "",
    expected_result_block: Optional[str] = None,
    parameters: Any = None,
    testops_modified_at: Optional[int] = None,
    testops_modified_by: Optional[str] = None,
) -> str:
    """expected_result_block и parameters — скрытые поля только для поиска. Их передаёт
    лишь синхронизация из TestOps; None (экспорт) — оставить как есть у существующей записи."""
    new_id = str(uuid.uuid4())
    hidden_params = Json(normalize_params(parameters)) if parameters is not None else None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO testops_test_cases
                (test_case_id, project_id, testops_id, case_num, title, description,
                 steps, expected_result, expected_result_block, parameters, tags, workflow, status,
                 testops_modified_at, testops_modified_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                    COALESCE(%s, '')::text,
                    COALESCE(%s::jsonb, '{"names": [], "combinations": []}'::jsonb),
                    %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, testops_id) DO UPDATE
            SET case_num = EXCLUDED.case_num,
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                steps = EXCLUDED.steps,
                expected_result = EXCLUDED.expected_result,
                expected_result_block = COALESCE(%s, testops_test_cases.expected_result_block),
                parameters = COALESCE(%s::jsonb, testops_test_cases.parameters),
                tags = EXCLUDED.tags,
                workflow = EXCLUDED.workflow,
                status = EXCLUDED.status,
                testops_modified_at = COALESCE(EXCLUDED.testops_modified_at, testops_test_cases.testops_modified_at),
                testops_modified_by = COALESCE(EXCLUDED.testops_modified_by, testops_test_cases.testops_modified_by),
                updated_at = NOW()
            RETURNING test_case_id
            """,
            (
                new_id, project_id, int(testops_id), case_num, title or "", description or "",
                Json(normalize_steps(steps)), expected_result or "",
                expected_result_block, hidden_params, list(tags or []),
                workflow or "", status or "",
                _as_int(testops_modified_at), (testops_modified_by or None),
                expected_result_block, hidden_params,
            ),
        )
        return str(cur.fetchone()["test_case_id"])


def get_testops_versions(project_id: str) -> Dict[int, Optional[int]]:
    """{testops_id: testops_modified_at} — для инкрементальной синхронизации."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT testops_id, testops_modified_at FROM testops_test_cases WHERE project_id = %s",
            (project_id,),
        )
        return {int(r["testops_id"]): r["testops_modified_at"] for r in cur.fetchall()}


def delete_testops_test_cases_except(project_id: str, keep_testops_ids: Iterable[int]) -> int:
    """Удаляет кейсы, которых больше нет в TestOps (эмбеддинги — каскадом)."""
    keep = [i for i in (_as_int(v) for v in keep_testops_ids) if i is not None]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM testops_test_cases WHERE project_id = %s AND NOT (testops_id = ANY(%s::int[]))",
            (project_id, keep),
        )
        return cur.rowcount


def delete_testops_test_cases(project_id: str) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM testops_test_cases WHERE project_id = %s", (project_id,))
        return cur.rowcount

# ===================== KNOWLEDGE BASE: общие шаги TestOps =====================

_TSS_COLUMNS = "shared_step_id, project_id, testops_id, name, content, created_at, updated_at"


def _tss_row(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["shared_step_id"] = str(item["shared_step_id"])
    item["id"] = item["shared_step_id"]
    return item


def list_testops_shared_steps(project_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_TSS_COLUMNS} FROM testops_shared_steps
            WHERE project_id = %s ORDER BY created_at
            """,
            (project_id,),
        )
        return [_tss_row(r) for r in cur.fetchall()]


def get_testops_shared_steps_by_ids(project_id: str, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    ids = _uuid_list(ids)
    if not ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_TSS_COLUMNS} FROM testops_shared_steps
            WHERE project_id = %s AND shared_step_id = ANY(%s::uuid[])
            """,
            (project_id, ids),
        )
        return {str(r["shared_step_id"]): _tss_row(r) for r in cur.fetchall()}


def get_testops_shared_steps_by_testops_ids(
    project_id: str, testops_ids: Sequence[int],
) -> Dict[int, Dict[str, Any]]:
    ids = [i for i in (_as_int(v) for v in testops_ids or []) if i is not None]
    if not ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_TSS_COLUMNS} FROM testops_shared_steps
            WHERE project_id = %s AND testops_id = ANY(%s::int[])
            """,
            (project_id, ids),
        )
        return {int(r["testops_id"]): _tss_row(r) for r in cur.fetchall()}


def upsert_testops_shared_step(project_id: str, testops_id: int, name: str, content: str) -> str:
    new_id = str(uuid.uuid4())
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO testops_shared_steps (shared_step_id, project_id, testops_id, name, content)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (project_id, testops_id) DO UPDATE
            SET name = EXCLUDED.name,
                content = EXCLUDED.content,
                updated_at = NOW()
            RETURNING shared_step_id
            """,
            (new_id, project_id, int(testops_id), name or "", content),
        )
        return str(cur.fetchone()["shared_step_id"])


def delete_testops_shared_steps(project_id: str) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM testops_shared_steps WHERE project_id = %s", (project_id,))
        return cur.rowcount

# ===================== KNOWLEDGE BASE: папки TestOps =====================

def replace_testops_folders(project_id: str, folder_titles: Dict[str, str]) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM testops_folders WHERE project_id = %s", (project_id,))
        for path, title in (folder_titles or {}).items():
            if not path:
                continue
            cur.execute(
                """
                INSERT INTO testops_folders (project_id, path, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (project_id, path) DO UPDATE SET title = EXCLUDED.title
                """,
                (project_id, str(path)[:64], title or ""),
            )
        return len(folder_titles or {})


def get_testops_folders(project_id: str) -> Dict[str, str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT path, title FROM testops_folders WHERE project_id = %s",
            (project_id,),
        )
        return {r["path"]: r["title"] for r in cur.fetchall()}


def count_knowledge_items(project_id: str) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM testops_test_cases WHERE project_id = %s) +
                (SELECT COUNT(*) FROM testops_shared_steps WHERE project_id = %s) +
                (SELECT COUNT(*) FROM documents WHERE project_id = %s) AS total
            """,
            (project_id, project_id, project_id),
        )
        return int(cur.fetchone()["total"])

# ===================== DOCUMENTS =====================

_DOC_META_COLUMNS = """
    d.document_id, d.project_id, d.filename, d.content_type, d.size_bytes, d.sha256,
    d.uploaded_by, u.login AS uploaded_by_login, d.index_status, d.index_error,
    d.indexed_at, d.created_at, d.updated_at
"""


def _doc_row(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["document_id"] = str(item["document_id"])
    item["id"] = item["document_id"]
    return item


def list_documents(project_id: str) -> List[Dict[str, Any]]:
    """Список документов без file_data и extracted_text, новые сверху."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_DOC_META_COLUMNS}
            FROM documents AS d
            LEFT JOIN users AS u ON u.user_id = d.uploaded_by
            WHERE d.project_id = %s
            ORDER BY d.updated_at DESC, d.filename
            """,
            (project_id,),
        )
        return [_doc_row(r) for r in cur.fetchall()]


def count_documents(project_id: str) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM documents WHERE project_id = %s", (project_id,))
        return int(cur.fetchone()["n"])


def get_document(project_id: str, document_id: str, with_text: bool = False) -> Optional[Dict[str, Any]]:
    if not _as_uuid(document_id):
        return None
    text_column = ", d.extracted_text" if with_text else ""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_DOC_META_COLUMNS}{text_column}
            FROM documents AS d
            LEFT JOIN users AS u ON u.user_id = d.uploaded_by
            WHERE d.project_id = %s AND d.document_id = %s
            """,
            (project_id, document_id),
        )
        row = cur.fetchone()
        return _doc_row(row) if row else None


def get_document_by_filename(project_id: str, filename: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, sha256 FROM documents
            WHERE project_id = %s AND filename = %s
            """,
            (project_id, filename),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"document_id": str(row["document_id"]), "sha256": row["sha256"]}


def create_document(
    project_id: str, filename: str, content_type: str, file_data: bytes,
    extracted_text: str, sha256: str, uploaded_by: Optional[int],
) -> str:
    new_id = str(uuid.uuid4())
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents
                (document_id, project_id, filename, content_type, size_bytes, sha256,
                 extracted_text, file_data, uploaded_by, index_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
            """,
            (
                new_id, project_id, filename, content_type or "application/octet-stream",
                len(file_data), sha256, extracted_text, psycopg2.Binary(file_data), uploaded_by,
            ),
        )
    return new_id


def update_document_content(
    document_id: str, content_type: str, file_data: bytes,
    extracted_text: str, sha256: str, uploaded_by: Optional[int],
) -> None:
    """Новая версия файла с тем же именем: document_id сохраняется, индексация — заново."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE documents
            SET content_type = %s, file_data = %s, extracted_text = %s,
                size_bytes = %s, sha256 = %s, uploaded_by = %s,
                index_status = 'pending', index_error = NULL,
                updated_at = NOW()
            WHERE document_id = %s
            """,
            (
                content_type or "application/octet-stream", psycopg2.Binary(file_data),
                extracted_text, len(file_data), sha256, uploaded_by, document_id,
            ),
        )


def get_document_file(project_id: str, document_id: str) -> Optional[Dict[str, Any]]:
    if not _as_uuid(document_id):
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT filename, content_type, file_data
            FROM documents
            WHERE project_id = %s AND document_id = %s
            """,
            (project_id, document_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        item = dict(row)
        item["file_data"] = bytes(item["file_data"])
        return item


def delete_document(project_id: str, document_id: str) -> bool:
    if not _as_uuid(document_id):
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM documents WHERE project_id = %s AND document_id = %s",
            (project_id, document_id),
        )
        return cur.rowcount > 0


def set_document_pending(project_id: str, document_id: str) -> bool:
    if not _as_uuid(document_id):
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE documents SET index_status = 'pending', index_error = NULL
            WHERE project_id = %s AND document_id = %s
            """,
            (project_id, document_id),
        )
        return cur.rowcount > 0


def set_document_status(document_id: str, status: str, error: Optional[str] = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE documents
            SET index_status = %s,
                index_error = %s,
                indexed_at = CASE WHEN %s = 'indexed' THEN NOW() ELSE indexed_at END
            WHERE document_id = %s
            """,
            (status, (error or None) and str(error)[:1000], status, document_id),
        )


def list_documents_for_indexing(project_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, filename, extracted_text, index_status
            FROM documents WHERE project_id = %s
            """,
            (project_id,),
        )
        return [_doc_row(r) for r in cur.fetchall()]


def get_documents_texts(
    project_id: str, ids: Sequence[str], only_indexed: bool = True,
) -> Dict[str, Dict[str, Any]]:
    ids = _uuid_list(ids)
    if not ids:
        return {}
    status_sql = "AND index_status = 'indexed'" if only_indexed else ""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT document_id, filename, extracted_text FROM documents
            WHERE project_id = %s AND document_id = ANY(%s::uuid[]) {status_sql}
            """,
            (project_id, ids),
        )
        return {str(r["document_id"]): _doc_row(r) for r in cur.fetchall()}


def existing_document_ids(project_id: str, ids: Sequence[str]) -> set:
    ids = _uuid_list(ids)
    if not ids:
        return set()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT document_id FROM documents WHERE project_id = %s AND document_id = ANY(%s::uuid[])",
            (project_id, ids),
        )
        return {str(r["document_id"]) for r in cur.fetchall()}


def list_documents_by_status(statuses: Sequence[str]) -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT project_id, document_id, filename FROM documents
            WHERE index_status = ANY(%s) ORDER BY updated_at
            """,
            (list(statuses),),
        )
        return [_doc_row(r) for r in cur.fetchall()]


def find_project_info_text(project_id: str) -> Optional[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT extracted_text FROM documents
            WHERE project_id = %s AND lower(filename) LIKE '%%info%%'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (project_id,),
        )
        row = cur.fetchone()
        return row["extracted_text"] if row else None
