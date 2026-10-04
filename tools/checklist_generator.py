import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple

import json5

from tools.ollama_client import generate_checklist_json
from tools.prompts.checklist_system_message import Z_TYPE_LABELS, build_system_message
from utils.steps_convert import (
    STEP_SHARED,
    normalize_steps,
    steps_equal,
    steps_from_llm,
    steps_to_text,
)

logger = logging.getLogger(__name__)

DEFAULT_STATUS = "Готово к автоматизации"

class ChecklistGenerationError(Exception):
    pass

class GenerateChecklistTool:
    def __init__(self, project_id: str = None, knowledge_base=None):
        self.project_id = project_id
        self.knowledge_base = knowledge_base

    def generate(
        self,
        tech_spec: str,
        mode: str = "generate",
        current_checklist: Optional[List[Dict]] = None,
        base_tech_spec: str = "",
        x: Optional[int] = None,
        z: Optional[int] = None,
        images: Optional[List[str]] = None,
        max_count: Optional[int] = None,
        allow_new: bool = True,
        editable_ids: Optional[Set[str]] = None,
        screenshot_query: str = "",
    ) -> Dict[str, Any]:
        """screenshot_query — текстовое описание скриншотов: отдельный поисковый запрос в RAG."""
        current_checklist = current_checklist or []

        if not tech_spec:
            raise ChecklistGenerationError("Не указано техническое задание")

        try:
            max_count = int(max_count) if max_count is not None else None
        except (TypeError, ValueError):
            logger.warning(f"Нечисловое значение count из UI: {max_count!r}, игнорирую")
            max_count = None
        if max_count is not None and max_count < 1:
            max_count = None

        logger.info(f"Режим: {mode}")
        logger.info(f"   ТЗ: {tech_spec[:100]}...")
        if max_count:
            logger.info(f"   Верхняя граница: не более {max_count} тест-кейсов")
        if x is not None:
            logger.info(f"   Фильтр по X={x}")
        if z is not None:
            logger.info(f"   Фильтр по Z={z}")
        if images:
            logger.info(f"   Прикреплено скриншотов: {len(images)} (единый мультимодальный вызов)")

        system_message = build_system_message(
            mode=mode,
            has_images=bool(images),
            z=z,
            max_count=max_count,
            allow_new=allow_new,
        )

        log_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        user_message, source_ids, shared_id_by_name = self._build_user_message(
            tech_spec, mode, current_checklist, x, z,
            log_timestamp=log_timestamp,
            base_tech_spec=base_tech_spec,
            system_message=system_message,
            editable_ids=editable_ids,
            screenshot_query=screenshot_query,
        )

        logger.info("Отправка запроса в Ollama (structured output)...")
        full_response, gen_metrics = generate_checklist_json(
            system_message, user_message, mode=mode, images=images
        )
        logger.info(f"Получен ответ от модели ({len(full_response)} символов)")

        result = self._parse_response(full_response, mode, source_ids, shared_id_by_name)

        actual_count = len(result.get("test_cases", []))
        if max_count and actual_count > max_count:
            logger.warning(
                f"Модель вернула {actual_count} тест-кейсов при верхней границе {max_count}"
            )

        self._append_metrics_log(log_timestamp, gen_metrics, max_count, actual_count)

        if mode == "update" and current_checklist:
            result = self._merge_with_existing(
                result, current_checklist,
                allow_new=allow_new, editable_ids=editable_ids,
            )
        elif mode == "extend":
            result = self._append_new(result, current_checklist)
        else:
            for case in result.get("test_cases", []):
                case["id"] = str(uuid.uuid4())

        logger.info(f"Сгенерировано {len(result.get('test_cases', []))} тест-кейсов")
        return result

    def _build_user_message(
        self,
        tech_spec: str,
        mode: str,
        current_checklist: List[Dict],
        x: Optional[int],
        z: Optional[int],
        log_timestamp: Optional[str] = None,
        base_tech_spec: str = "",
        system_message: str = "",
        editable_ids: Optional[Set[str]] = None,
        screenshot_query: str = "",
    ) -> Tuple[str, Set[str], Dict[str, Dict[str, Any]]]:
        if screenshot_query:
            logger.info(f"   Контекст подбирается также по описанию скриншотов ({len(screenshot_query)} символов)")
        context = self.knowledge_base.get_rag_context(
            query=tech_spec,
            top_k_docs=5,
            top_k_examples=10,
            top_k_shared=3,
            x=x,
            z=z,
            extra_queries=[screenshot_query] if screenshot_query else None,
        ) if self.knowledge_base else {'docs': [], 'examples': [], 'shared_steps': []}

        source_ids: Set[str] = set()
        # нормализованное имя / содержимое общего шага -> {testops_id, name, content}
        shared_id_by_name: Dict[str, Dict[str, Any]] = {}
        context_text = ""

        if context.get('docs'):
            context_text += "=== ДОКУМЕНТАЦИЯ ПРОЕКТА ===\n"
            for i, item in enumerate(context['docs'], 1):
                marker = f"D{i}"
                source_ids.add(marker)
                context_text += f"[{marker}] {item['content']}\n(Источник: {item['source']})\n\n"
            context_text += "\n"

        if context.get('shared_steps'):
            context_text += "=== SHARED STEPS (готовые шаблоны шагов) ===\n"
            for i, item in enumerate(context['shared_steps'], 1):
                marker = f"S{i}"
                source_ids.add(marker)
                testops_id = item.get('testops_id')
                if testops_id is not None:
                    shared = {
                        'testops_id': int(testops_id),
                        'name': item.get('name') or '',
                        'content': item['content'],
                    }
                    shared_id_by_name[self._normalize(item['content'])] = shared
                    shared_id_by_name[self._normalize(item['source'])] = shared
                    if item.get('name'):
                        shared_id_by_name[self._normalize(item['name'])] = shared
                context_text += f"[{marker}] {item['content']}\n(Источник: {item['source']})\n\n"
            context_text += "\n"

        if context.get('examples'):
            context_text += "=== ПРИМЕРЫ ТЕСТ-КЕЙСОВ (структура и стиль) ===\n"
            for i, item in enumerate(context['examples'], 1):
                marker = f"E{i}"
                source_ids.add(marker)
                context_text += f"[{marker}] {item['content']}\n(Источник: {item['source']})\n\n"
            context_text += "\n"

        # Общие шаги, уже стоящие в текущих кейсах (update / extend): модель видит их
        # в шагах как «[Общий шаг: …]» и может сослаться на них в uses_shared_step.
        for case in current_checklist or []:
            for step in normalize_steps(case.get('steps')):
                if step['type'] == STEP_SHARED and step.get('name'):
                    shared_id_by_name.setdefault(self._normalize(step['name']), {
                        'testops_id': step['shared_step_testops_id'],
                        'name': step['name'],
                        'content': step['text'],
                    })

        if not context_text:
            context_text = "(Контекст не загружен. Генерируй тест-кейсы на основе общего понимания QA.)\n\n"

        if mode == 'extend':
            # В «Дополнить» модель скриншотов не видит: описания исходных скриншотов
            # идут в запрос текстом (и отдельным поисковым запросом в RAG — выше).
            message = self._build_extend_message(
                tech_spec, base_tech_spec, context_text, current_checklist,
                screenshot_description=screenshot_query,
            )
        elif mode == 'update':
            message = self._build_update_message(tech_spec, context_text, current_checklist, editable_ids)
        else:
            message = self._build_generate_message(tech_spec, context_text)

        self._log_context(
            log_timestamp, tech_spec, mode, x, z, message, current_checklist, system_message,
            screenshot_query=screenshot_query,
        )
        return message, source_ids, shared_id_by_name

    @staticmethod
    def _build_generate_message(tech_spec: str, context_text: str) -> str:
        return f"""{context_text}ТЕХНИЧЕСКОЕ ЗАДАНИЕ:
{tech_spec}"""

    @staticmethod
    def _build_extend_message(
        tech_spec: str, base_tech_spec: str, context_text: str, current_checklist: List[Dict],
        screenshot_description: str = "",
    ) -> str:
        base_spec_block = (
            f"ИСХОДНОЕ ТЗ (по нему был сгенерирован текущий чек-лист):\n{base_tech_spec}\n\n"
            if base_tech_spec else ""
        )
        if screenshot_description:
            base_spec_block += (
                "ОПИСАНИЕ СКРИНШОТОВ, ПРИЛОЖЕННЫХ К ИСХОДНОМУ ТЗ:\n"
                f"{screenshot_description}\n\n"
            )
        existing = [
            {
                'title': item.get('title'),
                'steps': steps_to_text(item.get('steps')),
                'expected_result': item.get('expected_result'),
            }
            for item in current_checklist
        ]
        return f"""{context_text}{base_spec_block}ТРЕБОВАНИЯ ПОЛЬЗОВАТЕЛЯ К НОВЫМ ТЕСТ-КЕЙСАМ:
{tech_spec}

УЖЕ СУЩЕСТВУЮЩИЕ ТЕСТ-КЕЙСЫ ({len(existing)} шт., даны как контекст — что уже покрыто):
{json.dumps(existing, ensure_ascii=False, indent=2)}"""

    @staticmethod
    def _build_update_message(
        tech_spec: str, context_text: str, current_checklist: List[Dict],
        editable_ids: Optional[Set[str]] = None,
    ) -> str:
        prepared = []
        for index, item in enumerate(current_checklist, start=1):
            item_id = str(item.get('id'))
            editable = editable_ids is None or item_id in editable_ids
            prepared.append({
                'ref': index,

                'editable': editable,
                'title': item.get('title'),
                'steps': steps_to_text(item.get('steps')),
                'expected_result': item.get('expected_result'),
            })
        editable_count = sum(1 for item in prepared if item['editable'])

        return f"""{context_text}НОВЫЕ ТРЕБОВАНИЯ:
{tech_spec}

ТЕКУЩИЙ ЧЕК-ЛИСТ:
{json.dumps(prepared, ensure_ascii=False, indent=2)}

Доступных для правки ("editable": true) тест-кейсов сейчас: {editable_count}."""

    def _log_context(
        self, log_timestamp, tech_spec, mode, x, z, message, current_checklist, system_message,
        screenshot_query: str = "",
    ) -> None:
        try:
            log_dir = Path("./rag_logs")
            log_dir.mkdir(exist_ok=True)
            timestamp = log_timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
            project_part = self.project_id or "unknown"
            log_file = log_dir / f"context_{project_part}_{timestamp}.txt"

            with open(log_file, 'w', encoding='utf-8') as f:
                f.write("=== ЗАПРОС (TECH_SPEC) ===\n")
                f.write(tech_spec + "\n\n")
                if screenshot_query:
                    f.write("=== ОПИСАНИЕ СКРИНШОТОВ (доп. поисковый запрос) ===\n")
                    f.write(screenshot_query + "\n\n")
                f.write("=== РЕЖИМ ===\n")
                f.write(mode + "\n")
                if x is not None:
                    f.write(f"=== ФИЛЬТР X ===\n{x}\n")
                if z is not None:
                    f.write(f"=== ФИЛЬТР Z ===\n{z}\n")
                type_label = Z_TYPE_LABELS.get(z) if z is not None else None
                if type_label:
                    f.write(f"=== ОГРАНИЧЕНИЕ ПО ТИПУ (z={z}) ===\n{type_label}\n")

                f.write("\n=== ИТОГОВЫЙ SYSTEM-ПРОМПТ ===\n")
                f.write(system_message)
                f.write("\n\n=== ИТОГОВЫЙ USER-ПРОМПТ (то, что реально отправлено в модель) ===\n")
                f.write(message)

                if current_checklist:
                    f.write("\n\n=== ТЕКУЩИЙ ЧЕК-ЛИСТ (для UPDATE / EXTEND) ===\n")
                    f.write(json.dumps(current_checklist, ensure_ascii=False, indent=2))

            logger.info(f"Контекст RAG сохранён в {log_file}")
        except Exception as e:
            logger.warning(f"Не удалось сохранить контекст RAG: {e}")

    def _append_metrics_log(
        self, log_timestamp: str, metrics: Dict[str, Any],
        max_count: Optional[int], actual_count: int,
    ) -> None:
        try:
            log_dir = Path("./rag_logs")
            log_dir.mkdir(exist_ok=True)
            project_part = self.project_id or "unknown"
            log_file = log_dir / f"context_{project_part}_{log_timestamp}.txt"

            with open(log_file, 'a', encoding='utf-8') as f:
                f.write("\n=== САНИТИ-МЕТРИКИ ОТВЕТА МОДЕЛИ ===\n")
                for key, value in metrics.items():
                    f.write(f"{key}: {value}\n")
                f.write(f"max_count: {max_count}\n")
                f.write(f"actual_count: {actual_count}\n")
                if max_count and actual_count > max_count:
                    f.write("actual_count > max_count -- модель нарушила верхнюю границу\n")
                if metrics.get("used_thinking_fallback"):
                    f.write("Сработал fallback на баг Ollama #14716 (content пуст, взят thinking)\n")
                if metrics.get("used_schema") is False:
                    f.write("JSON-схема НЕ применялась -- проверьте версию Ollama\n")
                num_predict = metrics.get("num_predict_requested")
                eval_count = metrics.get("eval_count_tokens")
                if num_predict and eval_count:
                    ratio = round(eval_count / num_predict, 2)
                    f.write(f"eval_count/num_predict: {ratio}\n")
                    if ratio >= 0.95:
                        f.write("Ответ почти упёрся в num_predict -- возможна обрезка, увеличьте бюджет\n")

            logger.info(f"Санити-метрики дописаны в {log_file}")
        except Exception as e:
            logger.warning(f"Не удалось записать санити-метрики: {e}")

    @staticmethod
    def _normalize(text: Optional[str]) -> str:
        return re.sub(r'\s+', ' ', (text or '')).strip().lower()

    def _parse_response(
        self,
        response: str,
        mode: str,
        source_ids: Set[str],
        shared_id_by_name: Dict[str, Dict[str, Any]],
    ) -> dict:
        original_response = response

        json_match = re.search(r'(\{.*\})', response, re.DOTALL)
        if not json_match:
            json_match = re.search(r'(\[.*\])', response, re.DOTALL)

        if not json_match:
            self._save_failed_json(original_response, "no_json_found")
            raise ChecklistGenerationError(
                "Модель вернула ответ без JSON. Подробности в rag_logs/failed_json_*.txt"
            )

        json_str = json_match.group(1)

        start = json_str.find('{')
        end = json_str.rfind('}')
        if start != -1 and end != -1:
            json_str = json_str[start:end + 1]

        json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)
        json_str = re.sub(r'//.*?$', '', json_str, flags=re.MULTILINE)
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)

        try:
            data = json5.loads(json_str)
        except Exception as e:
            logger.warning(f"Ошибка парсинга JSON5: {e}")
            try:
                data = json.loads(json_str)
            except Exception as e2:
                self._save_failed_json(original_response, json_str)
                raise ChecklistGenerationError(
                    f"Не удалось разобрать JSON из ответа модели: {e2}. "
                    f"Подробности в rag_logs/failed_json_*.txt"
                )

        if isinstance(data, list):
            raw_cases = data
        elif isinstance(data, dict) and isinstance(data.get("test_cases"), list):
            raw_cases = data["test_cases"]
        elif isinstance(data, dict) and data:
            raw_cases = [data]
        else:
            self._save_failed_json(original_response, json_str)
            raise ChecklistGenerationError("Ответ модели не содержит тест-кейсов")

        return {"test_cases": self._validate_cases(raw_cases, mode, source_ids, shared_id_by_name)}

    def _validate_cases(
        self,
        raw_cases: List[Dict],
        mode: str,
        source_ids: Set[str],
        shared_id_by_name: Dict[str, Dict[str, Any]],
    ) -> List[Dict]:
        validated = []
        dropped_sources = 0
        dropped_shared = 0

        for i, case in enumerate(raw_cases):
            if not isinstance(case, dict):
                logger.warning(f"Элемент {i} ответа не является объектом, пропускаю")
                continue

            title = (case.get("title") or "").strip()
            raw_steps = case.get("steps") or ""
            if isinstance(raw_steps, list):
                raw_steps = "\n".join(f"{n}. {s}" for n, s in enumerate(raw_steps, 1))
            steps = str(raw_steps).strip()
            expected = (case.get("expected_result") or "").strip()

            if not title or not steps or not expected:
                logger.warning(
                    f"Тест-кейс {i} неполон (title/steps/expected_result), пропускаю"
                )
                continue

            title = re.sub(r'^\s*\d+(?:\.\d+)*\.?\s+', '', title)

            shared = case.get("uses_shared_step")
            matched_shared: Optional[Dict[str, Any]] = None
            if shared and isinstance(shared, str):
                normalized = self._normalize(shared)

                matched_shared = next(
                    (
                        shared_id_by_name[known]
                        for known in shared_id_by_name
                        if known and (normalized in known or known in normalized)
                    ),
                    None,
                )
                if matched_shared is None:
                    dropped_shared += 1

            item: Dict[str, Any] = {
                "title": title,
                "steps": steps_from_llm(
                    steps, shared if matched_shared else None,
                    lambda _name: matched_shared,
                ),
                "expected_result": expected,
                "status": DEFAULT_STATUS,
            }

            sources = case.get("sources")
            if isinstance(sources, list):
                valid = [s for s in sources if isinstance(s, str) and s.strip().upper() in source_ids]
                dropped_sources += len(sources) - len(valid)
                item["sources"] = valid
            else:
                item["sources"] = []

            if mode == "update":
                item["refs"] = self._parse_refs(case)

            validated.append(item)

        if dropped_shared:
            logger.warning(
                f"Отброшено {dropped_shared} ссылок на shared step, которых не было "
                f"в переданном контексте"
            )
        if dropped_sources:
            logger.warning(
                f"Отброшено {dropped_sources} несуществующих меток источников в поле sources"
            )
        if not validated:
            raise ChecklistGenerationError("Модель не вернула ни одного корректного тест-кейса")

        return validated

    @staticmethod
    def _parse_refs(case: Dict[str, Any]) -> List[int]:
        raw = case.get("refs")
        if raw is None:
            raw = case.get("ref")
        if raw is None:
            return []
        if not isinstance(raw, (list, tuple, set)):
            raw = [raw]

        refs: List[int] = []
        for value in raw:
            if value is None:
                continue
            try:
                ref = int(value)
            except (TypeError, ValueError):
                logger.warning(f"Нечисловая метка в refs: {value!r}, пропускаю")
                continue
            if ref not in refs:
                refs.append(ref)
        return refs

    def _save_failed_json(self, original_response: str, cleaned_json: str = None):
        try:
            log_dir = Path("./rag_logs")
            log_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = log_dir / f"failed_json_{timestamp}.txt"
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("=== ОРИГИНАЛЬНЫЙ ОТВЕТ МОДЕЛИ ===\n")
                f.write(original_response + "\n\n")
                if cleaned_json is not None and cleaned_json != "no_json_found":
                    f.write("=== ПОСЛЕ ОЧИСТКИ (ПОПЫТКА ПАРСИНГА) ===\n")
                    f.write(cleaned_json + "\n")
                elif cleaned_json == "no_json_found":
                    f.write("=== ПРИЧИНА: JSON НЕ НАЙДЕН ===\n")
            logger.error(f"Неудачный JSON сохранён в {filepath}")
        except Exception as e:
            logger.warning(f"Не удалось сохранить неудачный JSON: {e}")

    def _append_new(self, new_result: dict, current_checklist: List[Dict]) -> dict:
        current_checklist = current_checklist or []

        new_items = new_result.get('test_cases', []) or []
        added_ids = []
        for item in new_items:
            item['id'] = str(uuid.uuid4())
            item['testops_id'] = None
            added_ids.append(item['id'])

        logger.info(
            f"Дополнение чек-листа: было {len(current_checklist)}, "
            f"добавлено {len(new_items)}, стало {len(current_checklist) + len(new_items)}"
        )

        return {
            'test_cases': current_checklist + new_items,
            'added_ids': added_ids,
        }

    def _merge_with_existing(
        self,
        new_result: dict,
        current_checklist: List[Dict],
        allow_new: bool = True,
        editable_ids: Optional[Set[str]] = None,
    ) -> dict:
        current_checklist = current_checklist or []
        raw_new_items = new_result.get('test_cases', []) or []

        if editable_ids is None:
            editable_ids = {str(item.get('id')) for item in current_checklist}
        else:
            editable_ids = {str(i) for i in editable_ids}

        ref_to_uuid = {
            str(index): str(item.get('id'))
            for index, item in enumerate(current_checklist, start=1)
        }
        by_id = {str(item.get('id')): item for item in current_checklist}
        position = {str(item.get('id')): i for i, item in enumerate(current_checklist)}

        claimed: Set[str] = set()
        anchored: Dict[str, List[Dict]] = {}
        brand_new: List[Dict] = []
        dropped_unknown = 0
        dropped_frozen = 0
        dropped_conflict = 0
        dropped_new = 0
        dropped_orphan = 0

        for item in raw_new_items:
            raw_refs = [str(r) for r in item.pop('refs', [])]
            refs = [ref_to_uuid[r] for r in raw_refs if r in ref_to_uuid]
            dropped_unknown += len(raw_refs) - len(refs)

            valid_refs: List[str] = []
            for ref in refs:
                if ref not in by_id:
                    dropped_unknown += 1
                elif ref not in editable_ids:
                    dropped_frozen += 1
                elif ref in claimed and len(refs) > 1:
                    dropped_conflict += 1
                else:
                    valid_refs.append(ref)

            if not valid_refs:
                if raw_refs:
                    dropped_orphan += 1
                    continue
                if allow_new:
                    brand_new.append(item)
                else:
                    dropped_new += 1
                continue

            anchor = min(valid_refs, key=lambda r: position[r])
            claimed.update(valid_refs)
            anchored.setdefault(anchor, []).append((item, valid_refs))

        result: List[Dict] = []
        deleted = 0

        for original in current_checklist:
            original_id = str(original.get('id'))

            if original_id not in editable_ids:
                result.append(original)
                continue

            produced = anchored.get(original_id)
            if produced:
                is_split = len(produced) > 1
                for offset, (item, item_refs) in enumerate(produced):
                    # Первый результат занимает место оригинала (тот же id и testops_id —
                    # при экспорте обновит исходный кейс TestOps):
                    #  - разбиение: первая часть, остальные части — новые кейсы;
                    #  - слияние: результат заменяет первый (по порядку в чек-листе) из
                    #    объединённых кейсов, остальные исходные убираются из чек-листа.
                    keeps_original = offset == 0
                    item['id'] = original.get('id') if keeps_original else str(uuid.uuid4())
                    self._carry_provenance(
                        item, original,
                        merged_refs=[by_id[r] for r in item_refs] if len(item_refs) > 1 else None,
                        is_split_piece=is_split,
                        is_new_case=not keeps_original,
                    )
                    result.append(item)
            elif original_id in claimed:
                continue
            else:
                deleted += 1

        for item in brand_new:
            item['id'] = str(uuid.uuid4())
            item['testops_id'] = None
            result.append(item)

        self._log_merge_stats(
            current_checklist, editable_ids, anchored, brand_new, result,
            deleted=deleted, dropped_unknown=dropped_unknown,
            dropped_frozen=dropped_frozen, dropped_conflict=dropped_conflict,
            dropped_new=dropped_new, dropped_orphan=dropped_orphan,
        )
        return {'test_cases': result}

    @staticmethod
    def _carry_provenance(
        item: Dict[str, Any],
        original: Dict[str, Any],
        merged_refs: Optional[List[Dict]] = None,
        is_split_piece: bool = False,
        is_new_case: bool = False,
    ) -> None:
        if is_new_case:
            # Новая часть разбиения или результат слияния: к исходному кейсу
            # базы знаний и TestOps не привязана, при экспорте будет создана.
            for field in ('selected', 'status'):
                if field in original and field not in item:
                    item[field] = original[field]
            item.update({
                'origin': 'generated', 'source': None, 'doc_id': None,
                'score': None, 'testops_id': None,
            })
        else:
            for field in ('origin', 'source', 'doc_id', 'score', 'selected', 'status'):
                if field in original and field not in item:
                    item[field] = original[field]
            item['testops_id'] = original.get('testops_id')

        content_changed = (
            any(
                (item.get(field) or '') != (original.get(field) or '')
                for field in ('title', 'expected_result')
            )
            or not steps_equal(item.get('steps'), original.get('steps'))
        )
        if is_split_piece or merged_refs or content_changed:
            item['edited_since_search'] = True
        else:
            item['edited_since_search'] = original.get('edited_since_search', False)

    @staticmethod
    def _log_merge_stats(
        current_checklist, editable_ids, anchored, brand_new, result,
        deleted, dropped_unknown, dropped_frozen, dropped_conflict, dropped_new,
        dropped_orphan,
    ) -> None:
        merged = sum(1 for items in anchored.values() if len(items) == 1)
        split = sum(len(items) for items in anchored.values() if len(items) > 1)
        split_sources = sum(1 for items in anchored.values() if len(items) > 1)

        logger.info(
            f"Обновление чек-листа: рабочий набор {len(editable_ids)} из "
            f"{len(current_checklist)}, на выходе {len(result)} тест-кейсов "
            f"(правок/объединений {merged}, разбито {split_sources} кейсов "
            f"на {split}, новых {len(brand_new)}, удалено {deleted})"
        )

        if dropped_unknown:
            logger.warning(
                f"Отброшено {dropped_unknown} ссылок на несуществующие метки -- "
                f"модель сослалась на кейс, которого не было в запросе"
            )
        if dropped_frozen:
            logger.warning(
                f"Отброшено {dropped_frozen} ссылок на кейсы вне рабочего набора -- "
                f"модель попыталась изменить то, что менять не просили"
            )
        if dropped_conflict:
            logger.warning(
                f"Отброшено {dropped_conflict} конфликтующих ссылок -- один и тот же "
                f"кейс участвовал и в объединении, и в другой операции"
            )
        if dropped_new:
            logger.warning(
                f"Отброшено {dropped_new} новых тест-кейсов: в этом режиме "
                f"создание новых запрещено, разрешены только правки существующих"
            )
        if dropped_orphan:
            logger.warning(
                f"Отброшено {dropped_orphan} тест-кейсов, все ссылки которых оказались "
                f"недействительными -- иначе они попали бы в чек-лист как новые и "
                f"продублировали бы кейсы, которые модель пыталась изменить"
            )
        if editable_ids and not result:
            logger.warning(
                "После обновления не осталось ни одного тест-кейса -- "
                "вероятно, ответ модели был разобран неверно"
            )
