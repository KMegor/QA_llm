from typing import Any, Dict, List, Optional

from . import sparse_store
from .query_parser import build_query_variants
from .reranker import rerank
from .texts import attach_texts
from .vector_store import VectorStore

RRF_K = 60

def _rrf_merge(rank_lists: List[List[str]], k: int = RRF_K) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for ranked_ids in rank_lists:
        for rank, doc_id in enumerate(ranked_ids, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores

def hybrid_query(
    store: VectorStore,
    project_id: str,
    category: str,
    query: str,
    top_k: int = 10,
    x: Optional[int] = None,
    z: Optional[int] = None,
    overfetch: Optional[int] = None,
    pool_size: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Dense (pgvector) + BM25 (OpenSearch) через RRF, затем реранк.

    Фильтр x/z применяется в обоих каналах до ранжирования. Тексты кандидатов
    подтягиваются из PostgreSQL одним запросом.
    """
    variants = build_query_variants(query)
    dense_query = variants.dense

    fetch_n = overfetch or max(top_k * 3, 20)

    dense_results = store.query(dense_query, top_k=fetch_n, x=x, z=z)
    sparse_results = sparse_store.free_text_search(
        project_id, category,
        free_text_lemmatized=variants.free_text_lemmatized,
        top_k=fetch_n, x=x, z=z,
    )

    dense_rank_list = [r['id'] for r in dense_results]
    sparse_rank_list = [r['doc_id'] for r in sparse_results if r.get('doc_id')]

    if not dense_rank_list and not sparse_rank_list:
        return []

    combined_scores = _rrf_merge([dense_rank_list, sparse_rank_list])
    effective_pool = pool_size or max(top_k * 3, top_k)
    ordered_ids = sorted(combined_scores.keys(), key=lambda i: combined_scores[i], reverse=True)[:effective_pool]

    candidates = attach_texts(
        project_id, category,
        [{'id': doc_id, 'score': combined_scores[doc_id]} for doc_id in ordered_ids],
    )

    return rerank(dense_query, candidates, top_k=top_k, text_key='search_document')
