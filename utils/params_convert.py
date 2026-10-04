"""«Комбинации параметров» тест-кейса TestOps.

В ответе GET /api/testcase/{id}/overview:
    "parameters": [{"name": "Логин"}, {"name": "Пароль"}]           — названия
    "examples":   [{"id": 1, "parameters": [{"name": "Логин", "value": "admin"}, ...]}, ...]
                                                                    — комбинации (строки таблицы)

Внутри проекта хранится в JSONB одного вида:
    {"names": ["Логин", "Пароль"],
     "combinations": [[{"name": "Логин", "value": "admin"}, {"name": "Пароль", "value": "123"}], ...]}
"""
from typing import Any, Dict, List

# Сколько комбинаций выводить в текст для поиска и контекста модели: у кейса их
# могут быть сотни, а текст должен оставаться читаемым и не раздувать эмбеддинг.
MAX_COMBINATIONS_IN_TEXT = 30

EMPTY_PARAMS: Dict[str, Any] = {"names": [], "combinations": []}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def params_from_overview(overview: Dict[str, Any]) -> Dict[str, Any]:
    """Параметры и комбинации из ответа /overview."""
    names: List[str] = []
    for param in overview.get("parameters") or []:
        name = _text(param.get("name") if isinstance(param, dict) else param)
        if name and name not in names:
            names.append(name)

    combinations: List[List[Dict[str, str]]] = []
    for example in overview.get("examples") or []:
        if not isinstance(example, dict):
            continue
        row = []
        for param in example.get("parameters") or []:
            if not isinstance(param, dict):
                continue
            name = _text(param.get("name"))
            if not name:
                continue
            row.append({"name": name, "value": _text(param.get("value"))})
            if name not in names:
                names.append(name)
        if row:
            combinations.append(row)

    return {"names": names, "combinations": combinations}


def normalize_params(value: Any) -> Dict[str, Any]:
    """Приводит сохранённое/присланное значение к виду {"names", "combinations"}."""
    if not isinstance(value, dict):
        return {"names": [], "combinations": []}
    names = [_text(n) for n in value.get("names") or [] if _text(n)]
    combinations = []
    for row in value.get("combinations") or []:
        if not isinstance(row, list):
            continue
        clean = [
            {"name": _text(p.get("name")), "value": _text(p.get("value"))}
            for p in row if isinstance(p, dict) and _text(p.get("name"))
        ]
        if clean:
            combinations.append(clean)
    for row in combinations:
        for p in row:
            if p["name"] not in names:
                names.append(p["name"])
    return {"names": names, "combinations": combinations}


def has_params(value: Any) -> bool:
    params = normalize_params(value)
    return bool(params["names"] or params["combinations"])


def params_to_text(value: Any, max_combinations: int = MAX_COMBINATIONS_IN_TEXT) -> str:
    """Текст для markdown-представления кейса, поиска и контекста модели."""
    params = normalize_params(value)
    if not params["names"] and not params["combinations"]:
        return ""
    lines = [f"Параметры: {', '.join(params['names'])}"]
    combinations = params["combinations"]
    for i, row in enumerate(combinations[:max_combinations], 1):
        lines.append(f"{i}. " + "; ".join(f"{p['name']} = {p['value']}" for p in row))
    if len(combinations) > max_combinations:
        lines.append(f"… и ещё {len(combinations) - max_combinations} комбинаций")
    return "\n".join(lines)
