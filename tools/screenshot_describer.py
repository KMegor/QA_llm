"""Текстовое описание скриншотов для подбора контекста.

Скриншот сам по себе в поиск не попадает: база знаний текстовая. Поэтому перед
генерацией мультимодальная модель отдельным вызовом описывает каждый скриншот
(экран, поля, кнопки, надписи), и это описание идёт отдельным поисковым запросом
в RAG и в проверку «в базе уже есть похожие кейсы».

Описание кэшируется в message_images.description: при повторной генерации по тому
же сообщению (например, после отказа от переиспользования) модель не вызывается.
"""
import base64
import logging
from typing import List

from core.config import (
    SCREENSHOT_DESCRIPTION_ENABLED,
    SCREENSHOT_DESCRIPTION_MAX_TOKENS,
    SCREENSHOT_QUERY_MAX_CHARS,
)
from db import client as db
from tools.ollama_client import chat_completion

logger = logging.getLogger(__name__)

DESCRIBE_SYSTEM_MESSAGE = """Ты помогаешь QA-инженеру искать существующие тест-кейсы по скриншоту.
Опиши скриншот интерфейса так, чтобы по описанию можно было найти тест-кейсы и документацию об этом экране.

Пиши по-русски, простым текстом без markdown, в таком порядке:
Экран: название экрана, раздела или формы (как оно написано в интерфейсе, если видно).
Назначение: одна фраза — что пользователь делает на этом экране.
Элементы: поля ввода, выпадающие списки, чекбоксы, переключатели, кнопки, вкладки, таблицы и их колонки — с подписями дословно, как на экране.
Сообщения: тексты ошибок, подсказок, уведомлений, если они видны.
Состояние: что заполнено, что выбрано, что неактивно — если это видно.

Только то, что видно на скриншоте. Не придумывай элементы и не пиши тест-кейсы."""

DESCRIBE_USER_MESSAGE = "Опиши этот скриншот."


def _describe_one(image_bytes: bytes) -> str:
    response = chat_completion(
        system_message=DESCRIBE_SYSTEM_MESSAGE,
        messages=[{"role": "user", "content": DESCRIBE_USER_MESSAGE}],
        temperature=0.1,
        num_predict=SCREENSHOT_DESCRIPTION_MAX_TOKENS,
        images=[base64.b64encode(image_bytes).decode("ascii")],
        mode="describe_image",
    )
    return (response.get("content") or "").strip()


def describe_message_images(message_id: int) -> List[str]:
    """Описания всех скриншотов сообщения (по порядку). Ошибки не прерывают генерацию."""
    if not SCREENSHOT_DESCRIPTION_ENABLED or not message_id:
        return []

    descriptions: List[str] = []
    for image in db.get_message_images_for_description(message_id):
        if image["description"] is not None:
            if image["description"]:
                descriptions.append(image["description"])
            continue
        try:
            text = _describe_one(image["data"])
        except Exception as e:
            # Не кэшируем: при следующей попытке модель вызовется снова.
            logger.warning(f"Не удалось описать скриншот #{image['id']}: {e}. "
                           f"Контекст будет подобран только по тексту запроса.")
            continue
        db.set_message_image_description(image["id"], text)
        logger.info(f"Скриншот #{image['id']} описан ({len(text)} символов): {text[:200]!r}")
        if text:
            descriptions.append(text)
    return descriptions


def screenshot_query(descriptions: List[str]) -> str:
    """Поисковый запрос из описаний скриншотов (обрезан до SCREENSHOT_QUERY_MAX_CHARS)."""
    text = "\n\n".join(d for d in descriptions if d).strip()
    return text[:SCREENSHOT_QUERY_MAX_CHARS]
