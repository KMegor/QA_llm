import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

class StepParserMixin:
    def _ensure_shared_step_names_cached(self, project_id: str) -> None:
        if project_id in self._shared_step_cache_projects:
            return
        try:
            for shared_step in self.get_all_shared_steps(project_id):
                sid = shared_step.get("id")
                name = shared_step.get("name")
                if sid is not None and name:
                    self._shared_step_name_cache[int(sid)] = name
            self._shared_step_cache_projects.add(project_id)
        except Exception as e:
            logger.warning(
                f"⚠️ Не удалось загрузить список shared steps проекта {project_id} "
                f"для подстановки имён внутри тест-кейсов: {e}"
            )

    def _extract_step_text(
        self,
        step_data: Dict[str, Any],
        attachments_map: Dict[str, Any],
        is_shared_step: bool = False
    ) -> List[str]:
        result_steps: List[str] = []

        body = self._normalize_text(step_data.get("body", ""))
        if body:
            result_steps.append(body)

        attachment_id = step_data.get("attachmentId")
        if attachment_id is not None:
            att_id = str(attachment_id)
            att_info = attachments_map.get(att_id, {}) if attachments_map else {}
            att_name = self._normalize_text(att_info.get("name", ""))

            try:
                text = self.download_attachment_text(int(att_id), is_shared_step=is_shared_step)
                text = self._normalize_text(text)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка загрузки вложения {att_id}: {e}")
                text = ""

            if text and att_name:
                result_steps.append(f"{att_name}: {text}")
            elif text:
                result_steps.append(text)
            elif att_name:
                result_steps.append(att_name)

        return result_steps

    def _walk_step_tree(
        self,
        step_id: int,
        steps_map: Dict[str, Any],
        attachments_map: Dict[str, Any],
        is_shared_step_context: bool = False,
        visited: Optional[set] = None,
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Упорядоченный список элементов шагов.

        Обычный шаг -> {"type": "text", "text": ...}; шаг со sharedStepId ->
        {"type": "shared", "shared_step_testops_id", "name", "text"} на своей позиции.
        """
        if visited is None:
            visited = set()

        result_steps: List[Dict[str, Any]] = []
        step_key = str(step_id)

        if step_key in visited:
            logger.warning(f"⚠️ Обнаружен цикл в шагах: {step_key}")
            return result_steps

        visited.add(step_key)

        step_data = steps_map.get(step_key)
        if not step_data:
            logger.warning(f"⚠️ Шаг {step_id} не найден")
            return result_steps

        shared_step_id = step_data.get("sharedStepId")
        if shared_step_id is not None:
            logger.info(f"🔄 Разворачиваем shared step {shared_step_id}")
            known_name = None
            if project_id:
                self._ensure_shared_step_names_cached(project_id)
                known_name = self._shared_step_name_cache.get(int(shared_step_id))
            shared_lines = self.get_shared_step_steps(int(shared_step_id), known_name=known_name)
            result_steps.append({
                "type": "shared",
                "shared_step_testops_id": int(shared_step_id),
                "name": self._normalize_text(known_name or ""),
                "text": "\n".join(shared_lines),
            })
            return result_steps

        for text in self._extract_step_text(
            step_data=step_data,
            attachments_map=attachments_map,
            is_shared_step=is_shared_step_context
        ):
            result_steps.append({"type": "text", "text": text})

        children = step_data.get("children") or []
        for child_id in children:
            result_steps.extend(
                self._walk_step_tree(
                    step_id=child_id,
                    steps_map=steps_map,
                    attachments_map=attachments_map,
                    is_shared_step_context=is_shared_step_context,
                    visited=visited.copy(),
                    project_id=project_id,
                )
            )

        return result_steps

    def get_test_case_steps(self, test_case_id: int, project_id: Optional[str] = None) -> tuple:
        """(шаги — список элементов text/shared, ожидаемый результат)."""
        try:
            endpoint = f"/api/rs/testcase/{test_case_id}/step"
            logger.info(f"📡 Запрос шагов: {endpoint}")
            result = self._make_request('GET', endpoint)

            steps: List[Dict[str, Any]] = []
            expected_result = ""

            root_step_ids = result.get("root", {}).get("children", [])
            scenario_steps = result.get("scenarioSteps", {}) or {}
            attachments = result.get("attachments", {}) or {}

            logger.info(f"📊 Найдено корневых шагов: {len(root_step_ids)}")
            logger.info(f"📊 Найдено scenario steps: {len(scenario_steps)}")

            for step_id in root_step_ids:
                steps.extend(self._walk_step_tree(
                    step_id=step_id,
                    steps_map=scenario_steps,
                    attachments_map=attachments,
                    is_shared_step_context=False,
                    project_id=project_id,
                ))

            if not expected_result:
                for att_id, att_info in attachments.items():
                    att_name = self._normalize_text(att_info.get("name", ""))
                    if "Ожидаемый результат" in att_name or "Expected" in att_name:
                        expected_result = self.download_attachment_text(int(att_id), is_shared_step=False)
                        expected_result = self._normalize_text(expected_result)
                        if expected_result:
                            break

            if steps:
                logger.info(f"✅ Получены шаги для кейса {test_case_id}: {len(steps)} шагов")
                for i, step in enumerate(steps, 1):
                    label = "[shared] " if step["type"] == "shared" else ""
                    logger.info(f"  Шаг {i}: {label}{step['text'][:50]}...")
            else:
                logger.info(f"ℹ️ Шаги не найдены для кейса {test_case_id}")

            return steps, expected_result

        except Exception as e:
            logger.error(f"Ошибка получения шагов для кейса {test_case_id}: {e}")
            return [], ""

    def download_shared_step_attachment(self, attachment_id: int) -> str:
        return self.download_attachment_text(attachment_id, is_shared_step=True)

    _STEP_ENUM_ITEM_RE = re.compile(r'^\d+[.)]\s*(.+)$')

    def _strip_enum_prefix(self, text: str) -> str:
        match = self._STEP_ENUM_ITEM_RE.match(text)
        return match.group(1).strip() if match else text

    def _group_lines(self, lines: List[str]) -> List[Dict[str, Any]]:
        lines = [l for l in lines if l]
        if len(lines) < 2:
            return [{"type": "text", "content": l} for l in lines]

        first_line = lines[0]
        colon_pos = first_line.rfind(':')
        if colon_pos == -1:
            return [{"type": "text", "content": l} for l in lines]

        header = first_line[:colon_pos + 1].strip()
        glued_first_item = first_line[colon_pos + 1:].strip()

        items: List[str] = []
        if glued_first_item:
            items.append(self._strip_enum_prefix(glued_first_item))

        for line in lines[1:]:
            items.append(self._strip_enum_prefix(line))

        if not items:
            return [{"type": "text", "content": l} for l in lines]

        return [{"type": "group", "header": header, "items": items}]

    def _normalize_shared_step_items(self, raw_steps: List[str]) -> List[Dict[str, Any]]:
        if not raw_steps:
            return []

        if len(raw_steps) == 1 and "\n" in raw_steps[0]:
            lines = [l.strip() for l in raw_steps[0].split("\n")]
            return self._group_lines(lines)

        if len(raw_steps) > 1:
            return self._group_lines([s.strip() for s in raw_steps])

        return [{"type": "text", "content": raw_steps[0]}]

    def _render_shared_step_item(self, item: Dict[str, Any], shared_step_name: str) -> str:
        label = f"[Shared step] {shared_step_name}:" if shared_step_name else "[Shared step]:"
        if item["type"] == "group":
            lines = [label]
            for field in item["items"]:
                lines.append(f"   {field}")
            return "\n".join(lines)
        return f"{label} {item['content']}" if shared_step_name else f"[Shared step] {item['content']}"

    def get_shared_step_steps(self, shared_step_id: int, known_name: Optional[str] = None) -> List[str]:
        try:
            endpoint = f"/api/rs/sharedstep/{shared_step_id}/step"
            logger.info(f"📡 Запрос шагов shared step: {endpoint}")
            result = self._make_request('GET', endpoint)

            root_step_ids = result.get("root", {}).get("children", []) or []
            steps_map = result.get("sharedStepScenarioSteps") or result.get("scenarioSteps") or {}
            attachments_map = result.get("sharedStepAttachments") or result.get("attachments") or {}

            shared_step_name = self._normalize_text(known_name or "")
            if not shared_step_name:
                shared_info = result.get("sharedStep") or result.get("shared_step") or {}
                if isinstance(shared_info, dict):
                    shared_step_name = self._normalize_text(
                        shared_info.get("name") or shared_info.get("body") or ""
                    )
                if not shared_step_name:
                    logger.warning(
                        f"⚠️ Имя shared step {shared_step_id} не найдено ни в known_name, "
                        f"ни в ответе {endpoint} — заголовок в markdown будет пустым"
                    )

            logger.info(f"📊 Найдено шагов в shared step: {len(root_step_ids)}")

            inner_steps = []
            for step_id in root_step_ids:
                inner_steps.extend(
                    element["text"]
                    for element in self._walk_step_tree(
                        step_id=step_id,
                        steps_map=steps_map,
                        attachments_map=attachments_map,
                        is_shared_step_context=True
                    )
                    if element.get("text")
                )

            normalized = self._normalize_shared_step_items(inner_steps)
            steps = [self._render_shared_step_item(item, shared_step_name) for item in normalized]

            if steps:
                logger.info(f"✅ Получены шаги shared step {shared_step_id}: {len(steps)} шагов")
                for i, step in enumerate(steps, 1):
                    logger.info(f"  Шаг {i}: {step[:50]}...")
            else:
                logger.info(f"ℹ️ Шаги не найдены для shared step {shared_step_id}")

            return steps

        except Exception as e:
            logger.error(f"Ошибка получения шагов shared step {shared_step_id}: {e}")
            return []

    def get_all_shared_steps(self, project_id: str, max_steps: Optional[int] = None) -> List[Dict]:
        all_steps = []
        page = 0
        page_size = 100

        logger.info(f"📋 Загрузка shared steps из проекта {project_id}")

        while True:
            endpoint = f"/api/rs/sharedstep?projectId={project_id}&page={page}&size={page_size}"
            result = self._make_request('GET', endpoint)

            content = result.get('content', [])
            if not content:
                break

            all_steps.extend(content)
            logger.info(f"📊 Загружено {len(all_steps)} shared steps...")

            page += 1

            if max_steps and len(all_steps) >= max_steps:
                all_steps = all_steps[:max_steps]
                break

            total_pages = result.get('totalPages', 0)
            if page >= total_pages:
                break

        logger.info(f"✅ Всего загружено {len(all_steps)} shared steps")
        return all_steps
