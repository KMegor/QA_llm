import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse

from core.schemas import (
    TestOpsConnectionTestRequest,
    TestOpsExportRequest,
    TestOpsSyncExamplesRequest,
)
from core.security import (
    get_current_user,
    get_project_or_404,
    get_testops_settings,
    require_admin,
    require_project_access,
)
from core.state import get_knowledge_base, index_testops_in_background
from db import client as db
from utils.markdown_convert import convert_shared_step_to_markdown, extract_test_case_fields
from utils.sort import extract_version
from utils.steps_convert import clean_expected_result, normalize_steps, steps_to_text
from testops import AllureTestOpsClient, TestOpsAPIError, TestOpsCaseGoneError

logger = logging.getLogger(__name__)

router = APIRouter()

def _testops_client(project: Dict[str, Any], user: Dict[str, Any]):
    settings = get_testops_settings(project, user)
    client = AllureTestOpsClient(base_url=settings["url"], api_key=settings["api_key"])
    return client, settings["project_id"]

@router.post("/api/testops/sync-examples")
async def sync_testops_examples(
    request: TestOpsSyncExamplesRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    """Инкрементальная синхронизация: полностью скачиваются только новые кейсы и кейсы,
    у которых в TestOps сменилась lastModifiedDate; пропавшие из TestOps удаляются.
    Изменения индексируются в фоне сразу после ответа."""
    require_admin(user)
    try:
        project = get_project_or_404(request.project_id)
        require_project_access(request.project_id, user)

        client, testops_project_id = _testops_client(project, user)

        if not client.test_connection():
            raise HTTPException(status_code=400, detail="Не удалось подключиться к TestOps")

        try:
            folder_titles = client.get_folder_titles(testops_project_id)
            db.replace_testops_folders(request.project_id, folder_titles)
            logger.info(
                f"Сохранено дерево папок TestOps ({len(folder_titles)} записей) "
                f"проекта {request.project_id}"
            )
        except Exception as e:
            logger.warning(
                f"Не удалось загрузить дерево папок TestOps для проекта "
                f"{request.project_id}: {e}. Примеры будут проиндексированы "
                f"без учёта названий разделов в поиске."
            )

        if request.sync_mode == "replace":
            # Эмбеддинги удаляются каскадом, OpenSearch вычищается при следующей индексации.
            db.delete_testops_test_cases(request.project_id)
            if request.include_shared_steps:
                db.delete_testops_shared_steps(request.project_id)

        logger.info(f"Начинаем синхронизацию примеров из TestOps для проекта {request.project_id}")

        cases = client.get_all_test_cases(
            project_id=testops_project_id,
            max_cases=request.max_cases
        )
        # Полный список (не обрезан max_cases) — можно удалять то, чего в нём нет.
        listing_complete = not request.max_cases or len(cases) < request.max_cases

        # После replace база пуста — скачается всё.
        known_versions = db.get_testops_versions(request.project_id)
        if cases and cases[0].get("lastModifiedDate") is None:
            logger.warning(
                "В списке кейсов TestOps нет lastModifiedDate — инкрементальная "
                "синхронизация невозможна, все кейсы будут скачаны заново"
            )

        saved_examples = 0
        unchanged_examples = 0
        saved_shared_steps = 0
        skipped_no_version = 0
        listed_ids = []
        errors = []

        for i, case in enumerate(cases, 1):
            try:
                case_id = case.get("id")
                if not case_id:
                    continue

                # Номер раздела берём из названия в строке списка: кейсы без номера
                # не скачиваем вовсе (в базу знаний они всё равно не попадают).
                list_case_num = extract_version(case.get("name") or "")
                if not list_case_num or len(list_case_num.split('.')) != 4:
                    skipped_no_version += 1
                    continue
                listed_ids.append(case_id)

                modified_at = case.get("lastModifiedDate")
                if modified_at is not None and known_versions.get(int(case_id)) == modified_at:
                    unchanged_examples += 1
                    continue

                full_case = client.get_test_case_overview(case_id)
                steps, expected_result = client.get_test_case_steps(
                    case_id, project_id=testops_project_id
                )

                fields = extract_test_case_fields(full_case, steps, expected_result)

                case_num = extract_version(fields['title'])
                if not case_num or len(case_num.split('.')) != 4:
                    skipped_no_version += 1
                    continue

                db.upsert_testops_test_case(
                    project_id=request.project_id,
                    testops_id=case_id,
                    case_num=case_num,
                    title=fields['title'],
                    description=fields['description'],
                    steps=fields['steps'],
                    expected_result=fields['expected_result'],
                    tags=fields['tags'],
                    status=fields['status'],
                    workflow=fields.get('workflow') or '',
                    # Скрытые поля только для поиска: пишет их одна синхронизация.
                    expected_result_block=fields['expected_result_block'],
                    parameters=fields['parameters'],
                    testops_modified_at=full_case.get("lastModifiedDate", modified_at),
                    testops_modified_by=full_case.get("lastModifiedBy") or case.get("lastModifiedBy"),
                )

                saved_examples += 1

                if i % 10 == 0:
                    logger.info(f"Сохранено {saved_examples}/{len(cases)} test cases")

            except Exception as e:
                logger.error(f"Ошибка обработки test case {case.get('id')}: {e}")
                errors.append({
                    "type": "test_case",
                    "id": case.get("id"),
                    "name": case.get("name", ""),
                    "error": str(e)
                })

        if request.include_shared_steps:
            try:
                shared_steps = client.get_all_shared_steps(testops_project_id, max_steps=request.max_cases)

                for i, shared_step in enumerate(shared_steps, 1):
                    try:
                        shared_step_id = shared_step.get("id")
                        if not shared_step_id:
                            continue

                        shared_steps_content = client.get_shared_step_steps(
                            shared_step_id, known_name=shared_step.get("name")
                        )
                        content = convert_shared_step_to_markdown(shared_step, i, shared_steps_content)
                        if not content:
                            continue

                        db.upsert_testops_shared_step(
                            project_id=request.project_id,
                            testops_id=shared_step_id,
                            name=shared_step.get("name") or "",
                            content=content,
                        )

                        saved_shared_steps += 1

                    except Exception as e:
                        logger.error(f"Ошибка обработки shared step {shared_step.get('id')}: {e}")
                        errors.append({
                            "type": "shared_step",
                            "id": shared_step.get("id"),
                            "name": shared_step.get("name", ""),
                            "error": str(e)
                        })

            except Exception as e:
                logger.error(f"Ошибка загрузки shared steps: {e}")
                errors.append({
                    "type": "shared_steps_batch",
                    "error": str(e)
                })

        deleted_examples = 0
        if listing_complete and not listed_ids and known_versions:
            # Пустой список при непустой базе — скорее сбой TestOps или прав токена,
            # чем удаление всех кейсов. Базу знаний не трогаем.
            logger.warning(
                "TestOps не вернул ни одного кейса с номером раздела, а в базе знаний они есть — "
                "удаление пропущено"
            )
        elif listing_complete:
            # Кейсы, удалённые в TestOps или потерявшие номер раздела, — из базы знаний.
            deleted_examples = db.delete_testops_test_cases_except(request.project_id, listed_ids)
        else:
            logger.warning(
                f"Список кейсов обрезан max_cases={request.max_cases}: удалённые в TestOps "
                f"кейсы из базы знаний не убираются"
            )

        # Эмбеддинги пересчитываются только для изменившихся записей (по хэшу текста);
        # заодно из OpenSearch убираются удалённые.
        background_tasks.add_task(index_testops_in_background, request.project_id)

        message = (
            f"Синхронизация завершена: новых и изменённых кейсов {saved_examples}, "
            f"без изменений {unchanged_examples}"
            + (f", удалено {deleted_examples}" if deleted_examples else "")
            + (f", shared steps {saved_shared_steps}" if request.include_shared_steps else "")
            + (f". Пропущено без номера раздела: {skipped_no_version}" if skipped_no_version else "")
            + ". Изменения индексируются в фоне и через несколько минут появятся в поиске."
        )

        return JSONResponse({
            "success": True,
            "message": message,
            "count": saved_examples,
            "examples_saved": saved_examples,
            "examples_unchanged": unchanged_examples,
            "examples_deleted": deleted_examples,
            "shared_steps_saved": saved_shared_steps,
            "examples_skipped_no_version": skipped_no_version,
            "errors": errors
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка синхронизации TestOps examples: {e}")
        raise HTTPException(status_code=500, detail=str(e))

_NUMBER_PREFIX_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)\.?\s*')

def _next_case_numbers(existing_names, folder_path: str):
    prefix = (folder_path or '').strip().rstrip('.')
    max_index = 0
    for name in existing_names:
        match = _NUMBER_PREFIX_RE.match(name or '')
        if not match:
            continue
        parts = match.group(1).split('.')
        if len(parts) != len(prefix.split('.')) + 1:
            continue
        if '.'.join(parts[:-1]) != prefix:
            continue
        try:
            max_index = max(max_index, int(parts[-1]))
        except ValueError:
            continue

    counter = max_index

    def next_name(title: str) -> str:
        nonlocal counter
        counter += 1

        clean_title = _NUMBER_PREFIX_RE.sub('', title or '').strip() or "Без названия"
        return f"{prefix}.{counter}. {clean_title}"

    return next_name

def _sync_exported_case_to_knowledge_base(
    project_id: str, testops_id: int, case_name: str,
    steps: List[Dict[str, Any]], expected: str, case: Dict[str, Any],
    modified_at: Optional[int] = None, modified_by: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Экспортированный кейс сразу попадает в testops_test_cases и индексируется одной записью."""
    try:
        case_num = extract_version(case_name)
        if not case_num or len(case_num.split('.')) != 4:
            logger.warning(
                f"Кейс TestOps #{testops_id} без номера раздела в названии "
                f"(«{case_name}») -- в базу знаний сразу не попадёт, только "
                f"по /sync-examples."
            )
            return None

        test_case_id = db.upsert_testops_test_case(
            project_id=project_id,
            testops_id=testops_id,
            case_num=case_num,
            title=case_name,
            description=case.get("description") or "",
            steps=steps,
            expected_result=expected,
            tags=case.get("tags") or [],
            status=case.get("status") or "",
            workflow=case.get("workflow") or "",
            # expected_result_block и parameters не передаём: экспорт их в TestOps не меняет,
            # у существующей записи они остаются как были, у новой — пустые.
            testops_modified_at=modified_at,
            testops_modified_by=modified_by,
        )
        _index_case_now(project_id, test_case_id, testops_id)
        return {"test_case_id": test_case_id, "case_num": case_num}
    except Exception as e:
        logger.error(
            f"Не удалось записать экспортированный кейс TestOps #{testops_id} в базу знаний: {e}. "
            f"Появится в базе знаний при следующей синхронизации.",
            exc_info=True,
        )
        return None


def _index_case_now(project_id: str, test_case_id: str, testops_id: int) -> None:
    try:
        get_knowledge_base(project_id).index_single_test_case(test_case_id)
    except Exception as e:
        logger.error(
            f"Не удалось сразу проиндексировать кейс TestOps #{testops_id}: {e}. "
            f"Он проиндексируется после следующей синхронизации.",
            exc_info=True,
        )


def _current_version(client, testops_id: int) -> Tuple[Optional[int], Optional[str]]:
    """lastModifiedDate / lastModifiedBy кейса прямо сейчас (после нашей записи)."""
    try:
        data = client.get_test_case(testops_id) or {}
        return data.get("lastModifiedDate"), data.get("lastModifiedBy")
    except Exception as e:
        logger.warning(
            f"Не удалось перечитать версию кейса TestOps #{testops_id} после экспорта: {e}. "
            f"Следующий экспорт этого кейса покажет конфликт — выберите «Перезаписать»."
        )
        return None, None


def _fetch_remote_case(client, testops_id: int, testops_project_id) -> Dict[str, Any]:
    """Текущая версия кейса в TestOps в том же виде, в каком её сохраняет синхронизация."""
    overview = client.get_test_case_overview(testops_id)
    steps, expected = client.get_test_case_steps(testops_id, project_id=testops_project_id)
    fields = extract_test_case_fields(overview, steps, expected)
    fields["modified_at"] = overview.get("lastModifiedDate")
    fields["modified_by"] = overview.get("lastModifiedBy")
    return fields


def _store_remote_in_knowledge_base(project_id: str, testops_id: int, remote: Dict[str, Any]) -> Optional[str]:
    """Версия из TestOps — в базу знаний (как при синхронизации) и сразу в индекс."""
    case_num = extract_version(remote["title"])
    if not case_num or len(case_num.split('.')) != 4:
        return None
    test_case_id = db.upsert_testops_test_case(
        project_id=project_id,
        testops_id=testops_id,
        case_num=case_num,
        title=remote["title"],
        description=remote["description"],
        steps=remote["steps"],
        expected_result=remote["expected_result"],
        tags=remote["tags"],
        status=remote["status"],
        workflow=remote.get("workflow") or "",
        expected_result_block=remote["expected_result_block"],
        parameters=remote["parameters"],
        testops_modified_at=remote.get("modified_at"),
        testops_modified_by=remote.get("modified_by"),
    )
    _index_case_now(project_id, test_case_id, testops_id)
    return test_case_id


def _version_conflict(case: Dict[str, Any], remote_row: Dict[str, Any]) -> bool:
    """Кейс менялся в TestOps после того, как пользователь начал правку."""
    remote_at = remote_row.get("lastModifiedDate")
    if remote_at is None:
        return False
    base_at = case.get("base_modified_at")
    return base_at is None or int(base_at) != int(remote_at)


def _conflict_payload(
    case: Dict[str, Any], remote_row: Dict[str, Any], remote: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Данные для окна конфликтов: обе версии и кто/когда менял кейс в TestOps."""
    local_steps = normalize_steps(case.get("steps"))
    return {
        "id": case["id"],
        "title": (case.get("title") or "").strip() or "Без названия",
        "testops_id": case.get("testops_id"),
        "base_modified_at": case.get("base_modified_at"),
        "remote_modified_at": remote_row.get("lastModifiedDate"),
        "remote_modified_by": remote_row.get("lastModifiedBy"),
        "local": {
            "title": case.get("title") or "",
            "steps": steps_to_text(local_steps),
            "expected_result": case.get("expected_result") or "",
        },
        "remote": None if remote is None else {
            "title": remote["title"],
            "steps": steps_to_text(remote["steps"]),
            "expected_result": remote["expected_result"],
        },
    }


@router.post("/api/testops/export")
async def export_test_cases(request: TestOpsExportRequest, user=Depends(get_current_user)):
    session = db.get_chat_session(request.session_id, user["id"])
    if not session:
        raise HTTPException(status_code=404, detail="Чат не найден")

    project_id = session["project_id"]
    project = get_project_or_404(project_id)
    require_project_access(project_id, user)
    client, testops_project_id = _testops_client(project, user)
    resolutions = request.resolutions or {}

    if request.test_case_ids:
        candidates = db.get_test_cases_by_ids(request.session_id, request.test_case_ids)
    else:
        candidates = [c for c in db.get_test_cases(request.session_id) if c.get("selected")]

    to_export = [c for c in candidates if c.get("needs_export")]
    skipped_synced = len(candidates) - len(to_export)

    if not candidates:
        raise HTTPException(status_code=400, detail="Не отмечен ни один тест-кейс для экспорта")
    if not to_export:
        return JSONResponse({
            "success": True,
            "exported": 0,
            "created": 0,
            "updated": 0,
            "skipped": skipped_synced,
            "errors": [],
            "conflicts": [],
            "message": "Все отмеченные тест-кейсы уже синхронизированы с TestOps.",
        })

    try:
        folder = client.find_folder(testops_project_id, request.folder_path)
        folder_id = folder["id"]
        folder_name = folder.get("name", "")
        # В строках списка есть lastModifiedDate / lastModifiedBy — по ним проверяются версии,
        # отдельный запрос на каждый кейс не нужен.
        existing = client.list_folder_test_cases(testops_project_id, folder_name)
    except TestOpsAPIError as e:
        logger.error(f"Экспорт остановлен: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Не удалось прочитать папку TestOps: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"TestOps недоступен: {e}")

    existing_by_id = {c.get("id"): c for c in existing}
    next_name = _next_case_numbers([c.get("name", "") for c in existing], request.folder_path)

    created, updated, taken_remote, skipped_conflicts = 0, 0, 0, 0
    errors: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []

    def create_new(case, title, steps, expected) -> None:
        case_name = next_name(title)
        new_id = client.create_test_case(testops_project_id, folder_id, case_name)
        client.write_scenario(new_id, steps, expected)
        modified_at, modified_by = _current_version(client, new_id)
        synced = _sync_exported_case_to_knowledge_base(
            project_id, new_id, case_name, steps, expected, case, modified_at, modified_by,
        ) or {}
        db.mark_test_case_exported(
            case["id"], new_id,
            case_num=synced.get("case_num"), source_test_case_id=synced.get("test_case_id"),
            base_modified_at=modified_at,
        )

    for case in to_export:
        title = (case.get("title") or "").strip() or "Без названия"
        steps = normalize_steps(case.get("steps"))
        expected = clean_expected_result(case.get("expected_result") or "")
        testops_id = case.get("testops_id")
        action = resolutions.get(str(case["id"]))

        try:
            if testops_id is None:
                create_new(case, title, steps, expected)
                created += 1
                continue

            if testops_id not in existing_by_id:
                errors.append({
                    "id": case["id"],
                    "title": title,
                    "testops_id": testops_id,
                    "error": (
                        f"Кейс TestOps #{testops_id} не найден в папке "
                        f"«{folder_name}» — он лежит в другой папке или удалён. "
                        f"Пропущен."
                    ),
                })
                logger.error(
                    f"Экспорт кейса {case['id']}: TestOps #{testops_id} "
                    f"вне папки {folder_name}"
                )
                continue

            remote_row = existing_by_id[testops_id]

            if action == "skip":
                skipped_conflicts += 1
                continue

            if action is None and _version_conflict(case, remote_row):
                # Кейс менялся в TestOps после начала правки: не перезаписываем,
                # отдаём обе версии в окно конфликтов.
                try:
                    remote = _fetch_remote_case(client, testops_id, testops_project_id)
                except Exception as e:
                    logger.warning(f"Не удалось прочитать версию TestOps #{testops_id} для сравнения: {e}")
                    remote = None
                conflicts.append(_conflict_payload(case, remote_row, remote))
                logger.info(
                    f"Конфликт версий кейса {case['id']} (TestOps #{testops_id}): "
                    f"база {case.get('base_modified_at')}, в TestOps {remote_row.get('lastModifiedDate')} "
                    f"({remote_row.get('lastModifiedBy')})"
                )
                continue

            if action == "take_remote":
                remote = _fetch_remote_case(client, testops_id, testops_project_id)
                source_id = _store_remote_in_knowledge_base(project_id, testops_id, remote)
                db.apply_remote_version(
                    case["id"], remote["title"], remote["steps"], remote["expected_result"],
                    base_modified_at=remote.get("modified_at"), source_test_case_id=source_id,
                )
                taken_remote += 1
                continue

            if action == "save_as_new":
                create_new(case, title, steps, expected)
                created += 1
                continue

            # Конфликта нет либо выбрано «Перезаписать».
            case_name = remote_row.get("name") or title
            client.update_test_case(testops_id, case_name)
            client.write_scenario(testops_id, steps, expected)
            modified_at, modified_by = _current_version(client, testops_id)
            synced = _sync_exported_case_to_knowledge_base(
                project_id, testops_id, case_name, steps, expected, case, modified_at, modified_by,
            ) or {}
            db.mark_test_case_exported(
                case["id"], testops_id,
                case_num=synced.get("case_num"), source_test_case_id=synced.get("test_case_id"),
                base_modified_at=modified_at,
            )
            updated += 1

        except TestOpsCaseGoneError as e:
            logger.error(f"Экспорт кейса {case['id']} не удался: {e}")
            errors.append({"id": case["id"], "title": title,
                           "testops_id": testops_id, "error": str(e)})
        except Exception as e:
            logger.error(f"Ошибка экспорта тест-кейса {case['id']} ({title}): {e}", exc_info=True)
            errors.append({"id": case["id"], "title": title,
                           "testops_id": testops_id, "error": str(e)})

    message = f"Экспорт завершён: создано {created}, обновлено {updated}"
    if taken_remote:
        message += f", заменено версией из TestOps {taken_remote}"
    if skipped_synced:
        message += f", пропущено без изменений {skipped_synced}"
    if skipped_conflicts:
        message += f", пропущено по решению {skipped_conflicts}"
    if conflicts:
        message += f", ждут решения (изменены в TestOps) {len(conflicts)}"
    if errors:
        message += f", с ошибками {len(errors)}"

    return JSONResponse({
        "success": not errors,
        "exported": created + updated,
        "created": created,
        "updated": updated,
        "taken_remote": taken_remote,
        "skipped": skipped_synced,
        "skipped_conflicts": skipped_conflicts,
        "errors": errors,
        "conflicts": conflicts,
        "folder": folder_name,
        "folder_path": request.folder_path,
        "message": message,
        "test_cases": db.get_test_cases(request.session_id),
    })

@router.post("/api/testops/connection-test")
async def test_testops_connection(request: TestOpsConnectionTestRequest, user=Depends(get_current_user)):
    try:
        client = AllureTestOpsClient(base_url=request.testops_url, api_key=request.testops_api_key)
        is_connected = client.test_connection()
        return JSONResponse({
            "connected": is_connected,
            "message": "Подключение успешно" if is_connected else "Не удалось подключиться",
            "url": request.testops_url
        })
    except Exception as e:
        logger.error(f"Ошибка проверки подключения к TestOps: {e}")
        return JSONResponse({"connected": False, "message": str(e), "url": request.testops_url}, status_code=400)
