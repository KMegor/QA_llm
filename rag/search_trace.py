
import json
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger('rag.search')

class SearchTrace:
    def __init__(self, query: str, project_id: str, category: str):
        self.search_id = uuid.uuid4().hex[:8]
        self.query = query
        self.project_id = project_id
        self.category = category
        self.started_at = time.perf_counter()
        self.timings: Dict[str, float] = {}
        self.counters: Dict[str, Any] = {}
        self.stages: List[Dict[str, Any]] = []

    @contextmanager
    def stage(self, name: str, **fields: Any):
        started = time.perf_counter()
        record: Dict[str, Any] = {'stage': name}
        try:
            yield record
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            self.timings[name] = self.timings.get(name, 0.0) + elapsed
            record['ms'] = round(elapsed, 1)
            record.update(fields)
            self.stages.append(record)
            logger.debug(
                "search_stage %s",
                json.dumps({'search_id': self.search_id, **record}, ensure_ascii=False, default=str),
            )

    def set(self, **fields: Any) -> None:
        self.counters.update(fields)

    def note(self, name: str, **fields: Any) -> None:
        record = {'stage': name, **fields}
        self.stages.append(record)
        logger.debug(
            "search_stage %s",
            json.dumps({'search_id': self.search_id, **record}, ensure_ascii=False, default=str),
        )

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000

    def finish(self) -> None:
        counters = ' '.join(f"{k}={v}" for k, v in self.counters.items() if v is not None)
        timings = ' '.join(f"t_{k}={self.timings[k]:.0f}" for k in sorted(self.timings))
        logger.info(
            "search_id=%s project=%s %s | %s | t_total=%.0fms %s",
            self.search_id, self.project_id, counters,
            self._funnel(), self.total_ms, timings,
        )

    def _funnel(self) -> str:
        keys = ('subset', 'sparse_exact', 'sparse_lemma', 'dense', 'rerank', 'after_floor', 'returned')
        return ' → '.join(
            f"{k}={self.counters[k]}" for k in keys if self.counters.get(k) is not None
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'search_id': self.search_id,
            'query': self.query,
            'counters': self.counters,
            'timings': {k: round(v, 1) for k, v in self.timings.items()},
            'total_ms': round(self.total_ms, 1),
            'stages': self.stages,
        }

def score_stats(scores: List[float]) -> Optional[Dict[str, float]]:
    if not scores:
        return None
    ordered = sorted(scores)
    return {
        'min': round(ordered[0], 3),
        'median': round(ordered[len(ordered) // 2], 3),
        'max': round(ordered[-1], 3),
    }
