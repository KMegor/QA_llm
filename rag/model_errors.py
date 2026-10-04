"""Понятная ошибка, когда модели HuggingFace нет в локальном кэше (офлайн-режим)."""


def is_missing_model_error(error: BaseException) -> bool:
    if isinstance(error, OSError):
        return True
    try:
        from huggingface_hub.errors import LocalEntryNotFoundError
    except Exception:
        try:
            from huggingface_hub.utils import LocalEntryNotFoundError
        except Exception:
            LocalEntryNotFoundError = None
    return LocalEntryNotFoundError is not None and isinstance(error, LocalEntryNotFoundError)


def missing_model_error(model_name: str) -> RuntimeError:
    return RuntimeError(
        f"Модель {model_name} не найдена в локальном кэше HuggingFace. "
        f"Для первичной загрузки выставьте HF_OFFLINE = False в core/config.py "
        f"и запустите приложение с доступом в интернет"
    )
