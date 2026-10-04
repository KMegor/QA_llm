import math
from typing import Any, Dict, List

from core.config import (
    RERANKER_BATCH_SIZE,
    RERANKER_ENABLED,
    RERANKER_LOGIT_TEMPERATURE,
    RERANKER_MODEL_NAME,
)
from .model_errors import is_missing_model_error, missing_model_error

_reranker_cache = {}

RERANK_SCORE_SCALE = 'rerank'

def _get_reranker():
    if RERANKER_MODEL_NAME not in _reranker_cache:
        from sentence_transformers import CrossEncoder
        try:
            _reranker_cache[RERANKER_MODEL_NAME] = CrossEncoder(RERANKER_MODEL_NAME)
        except Exception as e:
            if is_missing_model_error(e):
                raise missing_model_error(RERANKER_MODEL_NAME) from e
            raise
    return _reranker_cache[RERANKER_MODEL_NAME]

def _calibrate(raw_score: float) -> float:
    return 1.0 / (1.0 + math.exp(-raw_score / RERANKER_LOGIT_TEMPERATURE))

def rerank(query: str, items: List[Dict[str, Any]], top_k: int, text_key: str = 'document') -> List[Dict[str, Any]]:
    if not RERANKER_ENABLED or not items:
        return items[:top_k]

    model = _get_reranker()
    pairs = [(query, item.get(text_key) or item.get('content') or '') for item in items]
    raw_scores = model.predict(pairs, batch_size=RERANKER_BATCH_SIZE)

    scores = [_calibrate(float(s)) for s in raw_scores]

    ranked = sorted(zip(items, scores), key=lambda pair: pair[1], reverse=True)[:top_k]

    result = []
    for item, score in ranked:
        item = dict(item)
        item['score'] = score
        item['score_scale'] = RERANK_SCORE_SCALE
        result.append(item)
    return result
