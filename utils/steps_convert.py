"""Шаги тест-кейса: упорядоченный список элементов (JSONB).

Формат элемента:
    {"type": "text", "text": "Открыть карточку дела"}
    {"type": "shared", "shared_step_testops_id": 123, "name": "Авторизация",
     "text": "1. Открыть страницу входа\\n2. Ввести логин и пароль"}

Порядок элементов = порядок шагов. У элемента "shared" поле text — развёрнутое
содержимое общего шага (для отображения, эмбеддингов и промптов).
"""
import json
import logging
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

logger = logging.getLogger(__name__)

STEP_TEXT = 'text'
STEP_SHARED = 'shared'

_NUMBERED_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)\s*[.)\-–—:](?:\s+(.*))?$')

_BULLET_RE = re.compile(r'^\s*[-*•]\s+(.*)$')

_NOISE_RE = re.compile(
    r'^\s*(?:\*\*)?(?:Шаги|Steps|Ожидаемый результат|Expected result)\s*:?(?:\*\*)?\s*$',
    re.IGNORECASE,
)

_SHARED_INDENT = '   '


def split_steps(steps_text: str) -> List[str]:
    text = (steps_text or '').strip()
    if not text:
        return []

    lines = [line for line in text.split('\n')]
    result: List[str] = []
    saw_marker = False

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip() or _NOISE_RE.match(line):
            continue

        numbered = _NUMBERED_RE.match(line)
        if numbered:
            saw_marker = True
            body = (numbered.group(2) or '').strip()
            if body:
                result.append(body)
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            saw_marker = True
            body = bullet.group(1).strip()
            if body:
                result.append(body)
            continue

        if saw_marker and result:
            result[-1] = f"{result[-1]}\n{line.strip()}"
        else:
            result.append(line.strip())

    if not result:
        logger.info("Шаги не удалось разделить, отправляю текст одним шагом")
        return [text]

    return result


def clean_expected_result(expected_text: str) -> str:
    text = (expected_text or '').strip()
    return re.sub(
        r'^\s*(?:\*\*)?(?:Ожидаемый результат|Expected result)\s*:?(?:\*\*)?\s*',
        '', text, flags=re.IGNORECASE,
    ).strip()


def _clean_text(value: Any) -> str:
    text = '' if value is None else str(value)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text.strip()


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and str(value).strip() != '' else None
    except (TypeError, ValueError):
        return None


def text_step(text: Any) -> Dict[str, Any]:
    return {'type': STEP_TEXT, 'text': _clean_text(text)}


def shared_step(shared_step_testops_id: Any, name: Any = '', text: Any = '') -> Dict[str, Any]:
    return {
        'type': STEP_SHARED,
        'shared_step_testops_id': _as_int(shared_step_testops_id),
        'name': _clean_text(name),
        'text': _clean_text(text),
    }


def normalize_step(element: Any) -> Optional[Dict[str, Any]]:
    """Один элемент к каноничному виду. None — элемент отбрасывается."""
    if element is None:
        return None
    if isinstance(element, str):
        step = text_step(element)
        return step if step['text'] else None
    if not isinstance(element, Mapping):
        logger.warning("Шаг неизвестного формата отброшен: %r", element)
        return None

    step_type = element.get('type') or STEP_TEXT
    if step_type == STEP_SHARED:
        step = shared_step(
            element.get('shared_step_testops_id'), element.get('name'), element.get('text'),
        )
        if step['shared_step_testops_id'] is None:
            logger.warning("Общий шаг без shared_step_testops_id: %r — сохранён как текстовый", element)
            fallback = step['text'] or step['name']
            return text_step(fallback) if fallback else None
        return step

    if step_type != STEP_TEXT:
        logger.warning("Неизвестный тип шага %r — сохранён как текстовый", step_type)
    step = text_step(element.get('text'))
    return step if step['text'] else None


def normalize_steps(steps: Union[None, str, List[Any]]) -> List[Dict[str, Any]]:
    """Приводит шаги к списку элементов.

    Принимает список элементов (или строк), а также JSON-строку списка
    и прежний текстовый формат «1. …\\n2. …» (разбирается через split_steps).
    """
    if steps is None:
        return []
    if isinstance(steps, str):
        stripped = steps.strip()
        if stripped.startswith('['):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return normalize_steps(parsed)
            except ValueError:
                pass
        return [text_step(s) for s in split_steps(stripped) if _clean_text(s)]
    if isinstance(steps, Mapping):
        steps = [steps]
    result = []
    for element in steps:
        step = normalize_step(element)
        if step is not None:
            result.append(step)
    return result


def _indent(text: str, prefix: str = _SHARED_INDENT) -> str:
    return '\n'.join(f"{prefix}{line}" if line.strip() else line for line in text.split('\n'))


def steps_to_text(steps: Union[None, str, List[Any]]) -> str:
    """Нумерованный текст для промптов, эмбеддингов, markdown и полнотекстового индекса."""
    lines: List[str] = []
    for number, step in enumerate(normalize_steps(steps), 1):
        if step['type'] == STEP_SHARED:
            header = f"{number}. [Общий шаг: {step['name'] or step['shared_step_testops_id']}]"
            lines.append(f"{header}\n{_indent(step['text'])}" if step['text'] else header)
        else:
            first, *rest = step['text'].split('\n')
            line = f"{number}. {first}"
            if rest:
                line += '\n' + _indent('\n'.join(rest))
            lines.append(line)
    return '\n'.join(lines)


def steps_equal(a: Any, b: Any) -> bool:
    return json.dumps(normalize_steps(a), ensure_ascii=False, sort_keys=True) == \
        json.dumps(normalize_steps(b), ensure_ascii=False, sort_keys=True)


def shared_step_ids(steps: Any) -> List[int]:
    return [
        s['shared_step_testops_id'] for s in normalize_steps(steps)
        if s['type'] == STEP_SHARED
    ]


def steps_from_llm(
    text: str,
    uses_shared_step: Optional[str],
    shared_lookup: Union[None, Mapping[str, Any], Callable[[str], Optional[Mapping[str, Any]]]],
) -> List[Dict[str, Any]]:
    """Разбор ответа LLM: строка шагов + имя использованного общего шага.

    shared_lookup по значению uses_shared_step возвращает найденный общий шаг —
    словарь с ключами testops_id, name, content — или None. Можно передать
    и готовый словарь (тогда он и есть найденный общий шаг).

    Если какой-то текстовый шаг содержит имя общего шага (без учёта регистра),
    он заменяется элементом shared. Иначе элемент shared вставляется первым.
    """
    steps = [text_step(s) for s in split_steps(text or '')]
    steps = [s for s in steps if s['text']]

    if not uses_shared_step or shared_lookup is None:
        return steps

    if callable(shared_lookup):
        found = shared_lookup(uses_shared_step)
    else:
        found = shared_lookup
    if not found or found.get('testops_id') is None:
        return steps

    element = shared_step(found.get('testops_id'), found.get('name'), found.get('content'))

    needles = [n.lower() for n in (element['name'], _clean_text(uses_shared_step)) if n]
    for index, step in enumerate(steps):
        haystack = step['text'].lower()
        if any(needle in haystack for needle in needles):
            steps[index] = element
            return steps

    return [element] + steps
