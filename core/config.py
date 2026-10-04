"""Настройки приложения.

Все параметры — константы этого модуля: внешних файлов настроек и переменных
окружения приложение не читает. Модуль намеренно не импортирует тяжёлые библиотеки
(torch, transformers и т. п.): app.py импортирует его раньше них, чтобы успеть
выставить офлайн-режим HuggingFace.
"""

# ===================== Работа без интернета =====================

# False — только для первичной загрузки моделей на машине с интернетом
HF_OFFLINE = True

# ===================== База данных и поиск =====================

DATABASE_URL = "postgresql://qa_admin:qa_admin_password@localhost:5432/qa_assistant"

OPENSEARCH_URL = "http://localhost:9200"
OPENSEARCH_INDEX_PREFIX = "knowledge"

# ===================== LLM (Ollama) =====================

OLLAMA_URL = "http://localhost:11434"

# Qwen3.8 27B, 4-битное квантование. Сверить тег с `ollama list`
OLLAMA_MODEL = "qwen3.8:27b-q4_K_M"

OLLAMA_USE_JSON_SCHEMA = True

DEFAULT_MODEL = OLLAMA_MODEL
MAX_IMAGES = 5

OLLAMA_THINK_BY_MODE = {
    "chat": False,
    "generate": False,
    "extend": False,
    "update": False,
    "describe_image": False,
}

# Описание скриншотов перед генерацией: текст идёт отдельным поисковым запросом в RAG.
SCREENSHOT_DESCRIPTION_ENABLED = True
SCREENSHOT_DESCRIPTION_MAX_TOKENS = 1024
# Сколько символов описания (всех скриншотов вместе) использовать как поисковый запрос
SCREENSHOT_QUERY_MAX_CHARS = 2000

OLLAMA_THINK_DEFAULT = False


def get_think_for_mode(mode: str) -> bool:
    return OLLAMA_THINK_BY_MODE.get(mode, OLLAMA_THINK_DEFAULT)

# ===================== Эмбеддинги и реранкер =====================

EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-4B"
# Должна совпадать с размерностью колонки embeddings.embedding (vector(2560) в db/init.sql)
EMBEDDING_DIMENSION = 2560
EMBEDDING_BATCH_SIZE = 4

RERANKER_ENABLED = True
RERANKER_MODEL_NAME = "Qwen/Qwen3-Reranker-4B"
RERANKER_LOGIT_TEMPERATURE = 5.0
RERANKER_BATCH_SIZE = 4

# ===================== Параметры поиска =====================

SEARCH_CANDIDATE_POOL = 50
SEARCH_TOP_K = 25

SEARCH_MIN_RERANK_SCORE = 0.3

SEARCH_PAGE_SIZE = 50

SEARCH_CASCADE_SUBSET_LIMIT = 2000

SEARCH_CASCADE_RERANK = True

PHRASE_POSITION_GAP = 1000
PHRASE_MAX_GAPS = 300

GENERATE_REUSE_MIN_SCORE = 0.9

# Уровень логов поиска (логгер rag.search): DEBUG, INFO, WARNING, ...
SEARCH_LOG_LEVEL = "INFO"

# ===================== Авторизация =====================

AUTH_COOKIE_NAME = "qa_session_token"
