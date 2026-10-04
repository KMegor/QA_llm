
import base64
import binascii
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

def _sniff_mime(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "image/gif"
    return "image/png"

def decode_images(images_b64: List[str]) -> List[Tuple[str, bytes]]:
    decoded: List[Tuple[str, bytes]] = []
    if not images_b64:
        return decoded

    for idx, raw_value in enumerate(images_b64):
        try:
            b64_data = raw_value.split(",", 1)[1] if raw_value.startswith("data:") else raw_value
            raw_bytes = base64.b64decode(b64_data, validate=True)
        except (binascii.Error, ValueError) as e:
            logger.warning(f"Пропускаю изображение {idx}: не удалось декодировать base64 ({e})")
            continue

        if not raw_bytes:
            continue

        mime_type = _sniff_mime(raw_bytes)
        decoded.append((mime_type, raw_bytes))

    return decoded
