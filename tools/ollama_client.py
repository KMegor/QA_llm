import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
import json5

from core.config import OLLAMA_MODEL, OLLAMA_URL, OLLAMA_USE_JSON_SCHEMA, get_think_for_mode

logger = logging.getLogger(__name__)

OLLAMA_CHAT_URL = f"{OLLAMA_URL}/api/chat"

def build_checklist_json_schema(mode: str = "generate") -> Dict[str, Any]:
    properties: Dict[str, Any] = {
        "title": {"type": "string"},
        "steps": {"type": "string"},
        "expected_result": {"type": "string"},
        "uses_shared_step": {"type": ["string", "null"]},
        "sources": {"type": "array", "items": {"type": "string"}},
    }
    required = ["title", "steps", "expected_result"]

    if mode == "update":
        properties["refs"] = {"type": "array", "items": {"type": "integer"}}

        required.append("refs")

    return {
        "type": "object",
        "properties": {
            "test_cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        },
        "required": ["test_cases"],
    }

def _looks_like_valid_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped.startswith("```"):
        return False
    if not stripped.startswith("{"):
        return False
    try:
        json5.loads(stripped)
        return True
    except Exception:
        return False

def _call_ollama_chat(
    messages: List[Dict[str, str]],
    num_predict: int,
    num_ctx: int,
    schema: Optional[Dict[str, Any]] = None,
    use_schema: bool = True,
    use_json_mode: bool = True,
    mode: str = "generate",
) -> Tuple[str, Dict[str, Any]]:
    think = get_think_for_mode(mode)
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": {
            "top_p": 0.65,
            "temperature": 0.15,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
        }
    }
    if use_schema and schema:
        payload["format"] = schema
    elif use_json_mode:
        payload["format"] = "json"

    with httpx.Client(timeout=3600.0) as client:
        try:
            response = client.post(OLLAMA_CHAT_URL, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if use_schema:
                logger.error(
                    f"Ollama отклонила JSON-схему в 'format' ({e.response.status_code}). "
                    f"Проверьте версию Ollama (structured outputs требуют 0.5+). "
                    f"Откат на format='json' — качество ответа будет ниже."
                )
                return _call_ollama_chat(
                    messages, num_predict, num_ctx, schema,
                    use_schema=False, use_json_mode=True, mode=mode,
                )
            if use_json_mode:
                logger.error(
                    f"Ollama отклонила format='json' ({e.response.status_code}). "
                    f"Откат на запрос без параметра format."
                )
                return _call_ollama_chat(
                    messages, num_predict, num_ctx, schema,
                    use_schema=False, use_json_mode=False, mode=mode,
                )
            raise

        data = response.json()
        message = data.get("message", {}) or {}
        content = (message.get("content") or "").strip()
        thinking = (message.get("thinking") or "").strip()
        used_thinking_fallback = False

        if not content and thinking:
            if not think:
                logger.warning(
                    "message.content пуст, но message.thinking непуст -- "
                    "похоже на баг Ollama #14716 (актуально при наличии картинок). "
                    "Использую thinking как содержимое ответа."
                )
                content = thinking
                used_thinking_fallback = True
            else:
                logger.error(
                    "message.content пуст при включённых рассуждениях "
                    f"(режим {mode}). Размышления получены ({len(thinking)} символов), "
                    "но ответа нет -- вероятно, весь бюджет num_predict ушёл в "
                    "thinking. Увеличьте num_predict или выключите рассуждения "
                    "для этого режима в core/config.py."
                )

        metrics = {
            "mode": mode,
            "think_enabled": think,
            "content_len_chars": len(content),
            "thinking_len_chars": len(thinking),
            "used_thinking_fallback": used_thinking_fallback,
            "num_predict_requested": num_predict,
            "num_ctx_requested": num_ctx,
            "eval_count_tokens": data.get("eval_count"),
            "prompt_eval_count_tokens": data.get("prompt_eval_count"),
            "total_duration_sec": round(data.get("total_duration", 0) / 1e9, 1) if data.get("total_duration") else None,
            "used_schema": use_schema,
            "used_json_mode": use_json_mode,
        }
        return content, metrics

def generate_checklist_json(
    system_message: str,
    user_message: str,
    mode: str = "generate",
    max_attempts: int = 2,
    images: Optional[List[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    user_msg: Dict[str, Any] = {"role": "user", "content": user_message}
    if images:
        user_msg["images"] = images

    messages = [
        {"role": "system", "content": system_message},
        user_msg,
    ]
    num_predict = 32768
    num_ctx = 32768
    schema = build_checklist_json_schema(mode)
    logger.info(f"num_predict={num_predict}, num_ctx={num_ctx}, mode={mode}")

    last_content = ""
    last_metrics: Dict[str, Any] = {}
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            content, metrics = _call_ollama_chat(
                messages, num_predict, num_ctx, schema,
                use_schema=OLLAMA_USE_JSON_SCHEMA,
                mode=mode,
            )
        except Exception as e:
            logger.error(f"Ошибка запроса к Ollama (попытка {attempt}/{max_attempts}): {e}")
            last_error = e
            content, metrics = "", {}

        if content:
            last_content = content
        if metrics:
            last_metrics = {**metrics, "attempt": attempt, "attempts_total": max_attempts}

        if content and _looks_like_valid_json(content):
            return last_content, last_metrics

        if attempt < max_attempts:
            logger.warning(
                f"Ответ не похож на чистый JSON (попытка {attempt}/{max_attempts}), "
                f"просим модель исправить именно этот ответ"
            )
            messages.append({"role": "assistant", "content": content or "(пустой ответ)"})
            messages.append({
                "role": "user",
                "content": (
                    "Твой предыдущий ответ не является чистым валидным JSON. "
                    "Верни ТОЛЬКО валидный JSON, начиная с символа { и заканчивая "
                    "соответствующим }. Без markdown-разметки (без ```), без пояснений "
                    "до или после JSON."
                )
            })

    if not last_content:
        raise RuntimeError(
            "Модель не вернула ответ после всех попыток"
            + (f": {last_error}" if last_error else "")
        )

    return last_content, last_metrics

def chat_completion(
    system_message: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.3,
    num_predict: int = 4096,
    num_ctx: int = 32768,
    images: Optional[List[str]] = None,
    mode: str = "chat",
) -> Dict[str, Any]:
    payload_messages = [{"role": "system", "content": system_message}]
    payload_messages.extend(messages)

    if images and payload_messages:
        last = dict(payload_messages[-1])
        if last.get("role") == "user":
            last["images"] = images
            payload_messages[-1] = last

    payload = {
        "model": OLLAMA_MODEL,
        "messages": payload_messages,
        "stream": False,
        "think": get_think_for_mode(mode),
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
        },
    }

    with httpx.Client(timeout=600.0) as client:
        response = client.post(OLLAMA_CHAT_URL, json=payload)
        response.raise_for_status()
        data = response.json()

    message = data.get("message", {}) or {}
    content = (message.get("content") or "").strip()
    if not content:
        thinking = (message.get("thinking") or "").strip()
        if thinking:
            logger.warning("message.content пуст, использую message.thinking (баг Ollama #14716)")
            content = thinking

    return {"content": content, "raw": data}

def build_chat_payload(
    system_message: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.3,
    num_predict: int = 4096,
    num_ctx: int = 32768,
    images: Optional[List[str]] = None,
    stream: bool = True,
    mode: str = "chat",
) -> Dict[str, Any]:
    payload_messages = [{"role": "system", "content": system_message}]
    payload_messages.extend(messages)

    if images and payload_messages:
        last = dict(payload_messages[-1])
        if last.get("role") == "user":
            last["images"] = images
            payload_messages[-1] = last

    return {
        "model": OLLAMA_MODEL,
        "messages": payload_messages,
        "stream": stream,
        "think": get_think_for_mode(mode),
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
        },
    }
