import re
import logging
from typing import Any, Dict, List, Optional

from utils.params_convert import params_from_overview, params_to_text
from utils.steps_convert import STEP_SHARED, STEP_TEXT, normalize_steps, steps_to_text

logger = logging.getLogger(__name__)

def extract_test_case_fields(
    case: Dict,
    steps: Optional[List[Any]] = None,
    expected_result: Optional[str] = None,
) -> Dict[str, Any]:
    """Поля кейса TestOps. steps — упорядоченный список элементов (text / shared).

    case — ответ /api/testcase/{id}/overview (или /api/testcase/{id}).
    expected_result — текст вложения «Ожидаемый результат» из сценария.

    expected_result берётся как раньше: вложение шага, затем разбор описания.
    Блок «Ожидаемый результат» карточки (expectedResult) и «Комбинации параметров»
    возвращаются отдельно — это скрытые поля только для поиска.
    """
    title = case.get('name') or 'Без названия'
    description = case.get('description', '') or ''
    expected = (expected_result or '').strip()

    step_items: List[Dict[str, Any]] = []

    if steps:
        logger.info(f"Используем шаги из api/rs/testcase/{case.get('id')}/step: {len(steps)}")
        for step in normalize_steps(steps):
            if step['type'] == STEP_TEXT:
                clean_step = step['text']
                lower_step = clean_step.lower()
                if lower_step.startswith("ожидаемый результат:") or lower_step.startswith("[ожидаемый результат]:"):
                    if not expected:
                        expected = clean_step.split(":", 1)[1].strip() if ":" in clean_step else clean_step
                    continue
            step_items.append(step)

        if not expected:
            for pattern in (
                r'Ожидаемый результат:?\s*(.+?)(?:\n|$)',
                r'Expected result:?\s*(.+?)(?:\n|$)',
                r'Ожидается:?\s*(.+?)(?:\n|$)',
                r'Expected:?\s*(.+?)(?:\n|$)',
            ):
                match = re.search(pattern, description, re.IGNORECASE)
                if match:
                    expected = match.group(1).strip()
                    break

    else:
        logger.info("ℹ Шаги не переданы, пробуем извлечь из полей кейса")
        steps_lines: List[str] = []
        description_html = case.get('descriptionHtml', '')

        if description_html:
            text = re.sub(r'<[^>]+>', ' ', description_html)
            text = re.sub(r'\s+', ' ', text).strip()

            step_pattern = r'(\d+)[\.\)]\s*([^\.\d][^.\n]*?)(?=\s*\d+[\.\)]|$)'
            for _, step_text in re.findall(step_pattern, text, re.DOTALL):
                step_text = step_text.strip()
                if step_text and 'ожидаемый результат' not in step_text.lower():
                    steps_lines.append(step_text)

        if not steps_lines and description:
            for line in description.split('\n'):
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith('-') or line.startswith('*')):
                    if line[0].isdigit() and '. ' in line:
                        line = line.split('. ', 1)[1]
                    steps_lines.append(line)
            if not steps_lines:
                steps_lines.append(description)

        if not expected and description:
            for pattern in (
                r'Ожидаемый результат:?\s*(.+?)(?:\n|$)',
                r'Expected result:?\s*(.+?)(?:\n|$)',
                r'Ожидается:?\s*(.+?)(?:\n|$)',
                r'Expected:?\s*(.+?)(?:\n|$)',
                r'Т Ожидаемый результат\s*(.+?)(?:\n|$)',
            ):
                match = re.search(pattern, description, re.IGNORECASE)
                if match:
                    expected = match.group(1).strip()
                    break

        step_items = normalize_steps(steps_lines)

    logger.info(f"Итоговое количество шагов: {len(step_items)}")
    logger.info(f"Ожидаемый результат: {expected[:100] if expected else 'Не найден'}")

    status = case.get('status', {})
    if isinstance(status, dict):
        status = status.get('name', 'DRAFT')

    raw_tags = case.get('tags', [])
    if not isinstance(raw_tags, list):
        raw_tags = [raw_tags] if raw_tags else []
    # TestOps отдаёт теги объектами {"id", "name"}
    tags = [
        str(tag.get('name') or '').strip() if isinstance(tag, dict) else str(tag).strip()
        for tag in raw_tags
    ]
    tags = [t for t in tags if t]

    workflow = case.get('workflow') or ''
    if isinstance(workflow, dict):
        workflow = workflow.get('name') or ''

    return {
        'title': title,
        'description': description,
        'steps': step_items,
        'expected_result': expected,
        'tags': tags,
        'status': status or '',
        'workflow': str(workflow)[:128],
        # Скрытые поля: только для поиска, пользователю не показываются и не меняются.
        'expected_result_block': (case.get('expectedResult') or '').strip(),
        'parameters': params_from_overview(case),
    }

def render_example_markdown(row: Dict[str, Any]) -> str:
    tags = row.get('tags') or []
    tags_text = ', '.join(tags) if tags else 'Нет'
    testops_id = row.get('testops_id')
    title = row.get('title') or 'Без названия'
    heading = f"# Тест-кейс {testops_id}: {title}" if testops_id else f"# Тест-кейс: {title}"

    return (
        f"{heading}\n\n"
        f"**Описание:** {row.get('description') or ''}\n\n"
        f"**Шаги:**\n{steps_to_text(row.get('steps')) or 'Шаги не указаны'}\n\n"
        f"**Ожидаемый результат:** {row.get('expected_result') or 'Не указан'}\n\n"
        f"**Теги:** {tags_text}\n\n"
        f"**Статус:** {row.get('status') or ''}\n\n"
        "---\n"
    )

def hidden_search_text(row: Dict[str, Any]) -> str:
    """Скрытые поля кейса для поиска: блок «Ожидаемый результат» и «Комбинации параметров»."""
    parts = []
    block = (row.get('expected_result_block') or '').strip()
    if block:
        parts.append(f"**Ожидаемый результат (блок карточки):** {block}")
    params_text = params_to_text(row.get('parameters'))
    if params_text:
        parts.append(f"**Комбинации параметров:**\n{params_text}")
    return "\n\n".join(parts)


def render_example_search_text(row: Dict[str, Any]) -> str:
    """Текст кейса для поиска (эмбеддинг, реранк): видимая часть плюс скрытые поля.
    Пользователю и в контекст генерации идёт render_example_markdown — без скрытых полей."""
    visible = render_example_markdown(row).strip()
    hidden = hidden_search_text(row)
    return f"{visible}\n\n{hidden}" if hidden else visible


def convert_shared_step_to_markdown(shared_step: Dict, index: int, steps: List[str] = None) -> str:
    if not steps:
        return ""
    return "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
