import base64
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from urllib.parse import quote

from testops.step_parser import StepParserMixin

logger = logging.getLogger(__name__)

EXPECTED_RESULT_ATTACHMENT_NAME = "Ожидаемый результат"

class TestOpsAPIError(Exception):
    def __init__(
            self,
            message: str,
            status_code: Optional[int] = None,
            response_text: Optional[str] = None,
            payload: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.payload = payload or {}

class TestOpsCaseGoneError(TestOpsAPIError):
    pass

class AllureTestOpsClient(StepParserMixin):
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Api-Token {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        self._shared_step_name_cache: Dict[int, str] = {}
        self._shared_step_cache_projects: set = set()
        logger.info(f"Инициализирован клиент TestOps для {base_url}")

    def _make_request(
            self,
            method: str,
            endpoint: str,
            data: Dict = None,
            retry: int = 1
    ) -> Dict:
        url = f"{self.base_url}{endpoint}"

        for attempt in range(retry):
            try:
                logger.info(f"TestOps запрос: {method} {url}")
                if data is not None:
                    logger.info(f"Payload: {json.dumps(data, ensure_ascii=False)[:4000]}")

                response = self.session.request(
                    method,
                    url,
                    json=data,
                    timeout=self.timeout
                )

                if response.status_code == 429:
                    wait_time = int(response.headers.get('Retry-After', 5))
                    logger.warning(f"Rate limiting, ждем {wait_time} секунд")
                    time.sleep(wait_time)
                    continue

                if response.status_code >= 400:
                    response_text = response.text[:4000]
                    logger.error(f"Ошибка запроса {method} {endpoint}: {response.status_code}")
                    logger.error(f"Ответ TestOps: {response_text}")
                    raise TestOpsAPIError(
                        message=f"{response.status_code} Error for {endpoint}",
                        status_code=response.status_code,
                        response_text=response_text,
                        payload=data
                    )

                if response.status_code == 204:
                    return {}

                if not response.text.strip():
                    return {}

                return response.json()

            except requests.exceptions.Timeout as e:
                logger.error(f"Таймаут запроса {method} {endpoint}: {e}")
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

            except requests.exceptions.RequestException as e:
                logger.error(f"Ошибка сети {method} {endpoint}: {e}")
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

        raise Exception(f"Не удалось выполнить запрос после {retry} попыток")

    def get_test_cases_with_pagination(self, project_id: str, page: int = 0, size: int = 100,
                                       include_deleted: bool = False) -> Tuple[List[Dict], int, int]:
        deleted_param = "&deleted=false" if not include_deleted else ""
        endpoint = f"/api/testcase?projectId={project_id}&page={page}&size={size}{deleted_param}"
        logger.info(f"📡 Запрос к TestOps: page={page}, size={size}, include_deleted={include_deleted}")
        result = self._make_request('GET', endpoint)

        content = result.get('content', [])
        total = result.get('totalElements', 0)
        total_pages = result.get('totalPages', 0)

        logger.info(f"📊 Получено {len(content)} записей из {total} всего (страница {page + 1}/{total_pages})")

        return content, total, total_pages

    def get_all_test_cases(self, project_id: str, max_cases: Optional[int] = None, include_deleted: bool = False) ->\
            List[Dict]:
        all_cases = []
        page = 0
        page_size = 100
        total_pages = None

        logger.info(f"Загрузка тест-кейсов из проекта {project_id} (include_deleted={include_deleted})")

        while True:
            cases, total, pages = self.get_test_cases_with_pagination(
                project_id,
                page=page,
                size=page_size,
                include_deleted=include_deleted
            )

            if not cases:
                break

            if total_pages is None and pages is not None:
                total_pages = pages
                logger.info(f"📊 Всего тест-кейсов: {total}, страниц: {total_pages}")

            all_cases.extend(cases)
            page += 1

            if max_cases and len(all_cases) >= max_cases:
                all_cases = all_cases[:max_cases]
                break

            if total_pages is not None and page >= total_pages:
                break

            logger.info(f"Загружено {len(all_cases)} тест-кейсов...")

        logger.info(f"✅ Всего загружено {len(all_cases)} тест-кейсов")
        return all_cases

    def get_test_cases(self, project_id: str, limit: int = 10, offset: int = 0) -> List[Dict]:
        page = offset // 10 if offset > 0 else 0
        content, total, pages = self.get_test_cases_with_pagination(project_id, page=page, size=10)
        return content

    def get_test_case(self, test_case_id: str) -> Dict:
        return self._make_request('GET', f'/api/testcase/{test_case_id}')

    def get_test_case_overview(self, test_case_id: str) -> Dict:
        """Полная карточка кейса: поля /api/testcase/{id} плюс expectedResult,
        параметры и их комбинации (parameters / examples), пользовательские поля.
        Пустые поля TestOps в ответ не включает."""
        try:
            return self._make_request('GET', f'/api/testcase/{test_case_id}/overview')
        except TestOpsAPIError as e:
            if e.status_code == 404:
                logger.warning(f"/overview для кейса {test_case_id} недоступен, беру /api/testcase/{test_case_id}")
                return self.get_test_case(test_case_id)
            raise

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        return str(text).strip()

    _CHARSET_RE = re.compile(r'charset=([\w\-]+)', re.IGNORECASE)

    def _extract_declared_charset(self, content_type: str) -> Optional[str]:
        match = self._CHARSET_RE.search(content_type or "")
        return match.group(1) if match else None

    def _maybe_unwrap_json_string(self, text: str) -> str:
        if text.startswith('"') and text.endswith('"'):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, str):
                    logger.info("🔍 Обнаружен JSON-строковый контент")
                    return self._normalize_text(parsed)
            except Exception:
                pass
        return self._normalize_text(text)

    def download_attachment_text(self, attachment_id: int, is_shared_step: bool = False) -> str:
        if is_shared_step:
            url = f"{self.base_url}/api/rs/sharedstep/attachment/{attachment_id}/content"
        else:
            url = f"{self.base_url}/api/rs/testcase/attachment/{attachment_id}/content"

        try:
            response = requests.get(
                url,
                headers={
                    'Authorization': f'Api-Token {self.api_key}',
                    'Accept': '*/*'
                },
                timeout=self.timeout
            )

            if response.status_code == 404:
                logger.warning(f"⚠️ Вложение {attachment_id} не найдено")
                return ""

            response.raise_for_status()
            content_bytes = response.content

            logger.info(f"📎 Загрузка вложения #{attachment_id}, размер: {len(content_bytes)} байт")
            logger.info(f"📋 Content-Type: {response.headers.get('Content-Type', 'Не указан')}")

            content_type = response.headers.get('Content-Type', '')
            if 'base64' in content_type.lower() or 'binary' in content_type.lower():
                try:
                    logger.info("🔍 Обнаружен base64 контент, пробуем декодировать...")
                    decoded_bytes = base64.b64decode(content_bytes)
                    content_bytes = decoded_bytes
                    logger.info(f"✅ Base64 декодирован, новый размер: {len(content_bytes)} байт")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка декодирования base64: {e}")

            declared_charset = self._extract_declared_charset(content_type)
            if declared_charset and declared_charset.lower().replace('_', '-') not in ('utf-8', 'utf8'):
                try:
                    declared_text = content_bytes.decode(declared_charset, errors='strict')
                    logger.info(f"✅ Кодировка объявлена сервером в Content-Type: {declared_charset}")
                    return self._maybe_unwrap_json_string(declared_text)
                except (LookupError, UnicodeDecodeError) as e:
                    logger.warning(
                        f"⚠️ Сервер объявил charset={declared_charset}, но декодирование им не удалось ({e}); "
                        f"пробуем UTF-8"
                    )

            try:
                strict_utf8 = content_bytes.decode('utf-8', errors='strict')
                logger.info("✅ Контент — валидный UTF-8, используем как есть")
                return self._maybe_unwrap_json_string(strict_utf8)
            except UnicodeDecodeError as e:
                logger.warning(f"⚠️ Контент вложения #{attachment_id} — не валидный UTF-8 ({e})")

            utf8_replaced = content_bytes.decode('utf-8', errors='replace')
            stripped = utf8_replaced.strip()
            bad_count = utf8_replaced.count('\ufffd')
            total_len = max(len(stripped), 1)
            bad_ratio = bad_count / total_len

            if stripped and bad_ratio <= 0.02:
                logger.warning(
                    f"⚠️ Заменено {bad_count} невалидных байт из {total_len} символов "
                    f"({bad_ratio:.1%}) — похоже на пару случайных символов в почти "
                    f"корректном UTF-8, принимаем этот вариант"
                )
                return self._normalize_text(utf8_replaced)

            logger.error(
                f"❌ Не удалось надёжно определить кодировку вложения #{attachment_id}: "
                f"{bad_count} невалидных байт из {total_len} символов ({bad_ratio:.0%}). "
                f"Charset сервером не объявлен (или не подошёл). Возвращаем текст с заменой "
                f"битых байт на '�' — ЭТО НЕ ГАРАНТИРОВАННО ВЕРНЫЙ ТЕКСТ, нужна ручная проверка"
            )
            return self._normalize_text(utf8_replaced)

        except Exception as e:
            logger.error(f"Ошибка загрузки вложения #{attachment_id}: {e}")
            return ""

    def get_folder_tree(self, project_id: str) -> List[Dict[str, Any]]:
        result = self._make_request('GET', f'/api/tree-folder/{project_id}/tree')
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get('folders') or result.get('content') or []
        return []

    @staticmethod
    def _folder_number(name: str) -> str:
        match = re.match(r'^\s*(\d+(?:\.\d+)*)', name or '')
        return match.group(1) if match else ''

    def find_folder(self, project_id: str, path: str) -> Dict[str, Any]:
        wanted = (path or '').strip().rstrip('.')
        if not wanted:
            raise TestOpsAPIError("Не указан путь папки для экспорта")

        matches: List[Dict[str, Any]] = []

        def walk(nodes):
            for node in nodes or []:
                if self._folder_number(node.get('name', '')) == wanted:
                    matches.append(node)
                walk(node.get('folders'))

        walk(self.get_folder_tree(project_id))

        if not matches:
            raise TestOpsAPIError(
                f"Папка «{wanted}» в проекте TestOps не найдена. "
                f"Создайте её вручную или проверьте путь."
            )
        if len(matches) > 1:
            names = ', '.join(f"«{m.get('name')}» (id={m.get('id')})" for m in matches)
            raise TestOpsAPIError(
                f"Пути «{wanted}» соответствует несколько папок: {names}. "
                f"Экспорт остановлен, чтобы не положить кейсы не туда."
            )
        return matches[0]

    @staticmethod
    def _folder_title(name: str) -> str:
        match = re.match(r'^\s*\d+(?:\.\d+)*\.?\s*[-–—]?\s*(.*)$', name or '')
        return (match.group(1).strip() if match else (name or '').strip())

    def get_folder_titles(self, project_id: str) -> Dict[str, str]:
        titles: Dict[str, str] = {}

        def walk(nodes):
            for node in nodes or []:
                number = self._folder_number(node.get('name', ''))
                if number:
                    titles[number] = self._folder_title(node.get('name', ''))
                walk(node.get('folders'))

        walk(self.get_folder_tree(project_id))
        return titles

    def list_folder_test_cases(self, project_id: str, folder_name: str) -> List[Dict]:
        escaped = str(folder_name).replace('"', '\\"')
        rql = quote(f'folder in ["{escaped}"]')
        endpoint = (
            f"/api/testcase/__search?projectId={project_id}"
            f"&page=0&size=1000&rql={rql}"
        )
        result = self._make_request('GET', endpoint)
        return result.get('content', []) if isinstance(result, dict) else []

    def create_test_case(self, project_id: str, folder_id: int, name: str) -> int:
        result = self._make_request(
            'POST',
            f'/api/tree-folder/{project_id}/test-case?folderId={folder_id}',
            {"name": name},
        )
        case_id = (result or {}).get('id')
        if case_id is None:
            raise TestOpsAPIError(
                "TestOps не вернул id созданного тест-кейса",
                response_text=json.dumps(result, ensure_ascii=False)[:1000],
            )
        logger.info(f"Создан тест-кейс TestOps #{case_id} в папке {folder_id}")
        return int(case_id)

    def update_test_case(self, test_case_id: int, name: str) -> Dict:
        try:
            return self._make_request(
                'PATCH', f'/api/testcase/{test_case_id}',
                {"id": int(test_case_id), "name": name},
            )
        except TestOpsAPIError as e:
            if e.status_code in (403, 404):
                raise TestOpsCaseGoneError(
                    f"Тест-кейс #{test_case_id} в TestOps не найден "
                    f"(возможно, удалён) либо недоступен для этого токена",
                    status_code=e.status_code,
                    response_text=e.response_text,
                ) from e
            raise

    @staticmethod
    def _step_body_json(text: str) -> Dict[str, Any]:
        lines = [line for line in str(text or '').split('\n')]
        content = []
        for line in lines:
            line = line.strip()
            if line:
                content.append({
                    "type": "paragraph",
                    "content": [{"type": "text", "text": line}],
                })
            else:
                content.append({"type": "paragraph"})
        if not content:
            content = [{"type": "paragraph"}]
        return {"type": "doc", "content": content}

    def add_step(self, test_case_id: int, text: str, after_id: Optional[int] = None) -> int:
        query = "?withExpectedResult=false"
        if after_id is not None:
            query = f"?afterId={after_id}&withExpectedResult=false"
        result = self._make_request(
            'POST', f'/api/testcase/step{query}',
            {"bodyJson": self._step_body_json(text), "testCaseId": int(test_case_id)},
        )
        step_id = (result or {}).get('createdStepId')
        if step_id is None:
            raise TestOpsAPIError("TestOps не вернул id созданного шага")
        return int(step_id)

    def delete_step(self, step_id: int) -> None:
        self._make_request('DELETE', f'/api/testcase/step/{step_id}')

    def add_shared_step(self, test_case_id: int, shared_step_id: int, after_id: Optional[int] = None) -> int:
        query = "?withExpectedResult=false"
        if after_id is not None:
            query = f"?afterId={after_id}&withExpectedResult=false"
        result = self._make_request(
            'POST', f'/api/testcase/step{query}',
            {"sharedStepId": int(shared_step_id), "testCaseId": int(test_case_id)},
        )
        step_id = (result or {}).get('createdStepId')
        if step_id is None:
            raise TestOpsAPIError("TestOps не вернул id созданного шага (shared step)")
        return int(step_id)

    def clear_scenario(self, test_case_id: int) -> None:
        result = self._make_request('GET', f'/api/testcase/{test_case_id}/step')
        for step_id in ((result or {}).get('root') or {}).get('children') or []:
            try:
                self.delete_step(int(step_id))
            except Exception as e:
                logger.warning(f"Не удалось удалить шаг {step_id} кейса {test_case_id}: {e}")

    def upload_attachment(
        self, test_case_id: int, filename: str, content: str,
        content_type: str = 'text/plain',
    ) -> int:
        url = f"{self.base_url}/api/testcase/attachment?testCaseId={test_case_id}"
        response = requests.post(
            url,
            headers={'Authorization': f'Api-Token {self.api_key}', 'Accept': 'application/json'},
            files={'file': (filename, content.encode('utf-8'), content_type)},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise TestOpsAPIError(
                f"{response.status_code} при загрузке вложения «{filename}»",
                status_code=response.status_code,
                response_text=response.text[:2000],
            )
        data = response.json()
        if isinstance(data, list):
            data = data[0] if data else {}
        attachment_id = (data or {}).get('id')
        if attachment_id is None:
            raise TestOpsAPIError("TestOps не вернул id загруженного вложения")
        return int(attachment_id)

    def add_expected_result_step(
        self, test_case_id: int, expected_result: str, after_id: Optional[int] = None,
    ) -> int:
        attachment_id = self.upload_attachment(
            test_case_id, EXPECTED_RESULT_ATTACHMENT_NAME, expected_result,
        )
        query = "?withExpectedResult=false"
        if after_id is not None:
            query = f"?afterId={after_id}&withExpectedResult=false"
        result = self._make_request(
            'POST', f'/api/testcase/step{query}',
            {"attachmentId": attachment_id, "testCaseId": int(test_case_id)},
        )
        step_id = (result or {}).get('createdStepId')
        return int(step_id) if step_id is not None else attachment_id

    def write_scenario(
        self, test_case_id: int, steps: List[Any], expected_result: str = "",
    ) -> None:
        """Перезаписывает сценарий: шаги по порядку, ожидаемый результат — последним.

        steps — список элементов: {"type": "text", "text"} -> обычный шаг,
        {"type": "shared", "shared_step_testops_id"} -> общий шаг на своей позиции.
        """
        self.clear_scenario(test_case_id)
        last_step_id: Optional[int] = None
        for step in steps or []:
            if isinstance(step, str):
                step = {"type": "text", "text": step}
            if not isinstance(step, dict):
                continue
            if step.get("type") == "shared":
                shared_id = step.get("shared_step_testops_id")
                if shared_id is None:
                    logger.warning(f"Общий шаг без id в кейсе {test_case_id} пропущен при экспорте")
                    continue
                last_step_id = self.add_shared_step(test_case_id, int(shared_id), after_id=last_step_id)
                continue
            text = str(step.get("text") or "")
            if not text.strip():
                continue
            last_step_id = self.add_step(test_case_id, text, after_id=last_step_id)
        if str(expected_result or '').strip():
            self.add_expected_result_step(
                test_case_id, expected_result.strip(), after_id=last_step_id,
            )

    def get_project_info(self) -> Dict:
        return self._make_request('GET', '/api/project')

    def get_project_by_id(self, project_id: str) -> Dict:
        return self._make_request('GET', f'/api/project/{project_id}')

    def test_connection(self) -> bool:
        try:
            self.get_project_info()
            logger.info("Соединение с TestOps установлено")
            return True
        except Exception as e:
            logger.error(f"Ошибка подключения к TestOps: {e}")
            return False
