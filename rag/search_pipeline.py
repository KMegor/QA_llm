
import logging
from typing import Any, Dict, List, Optional, Sequence

from core.config import (
    SEARCH_CANDIDATE_POOL,
    SEARCH_CASCADE_RERANK,
    SEARCH_CASCADE_SUBSET_LIMIT,
    SEARCH_MIN_RERANK_SCORE,
    SEARCH_PAGE_SIZE,
    SEARCH_TOP_K,
)
from . import sparse_store
from .errors import SearchRerankUnavailableError
from .query_parser import (
    MODE_CASCADE,
    MODE_DENSE,
    MODE_EMPTY,
    MODE_PHRASE,
    SearchQuery,
    parse_search_query,
)
from .reranker import RERANK_SCORE_SCALE, rerank
from .search_trace import SearchTrace, score_stats
from .texts import CATEGORY_EXAMPLES, attach_texts, fetch_texts

logger = logging.getLogger(__name__)

MATCH_EXACT = 'exact'
MATCH_LEMMA = 'lemma'

_CATEGORY = CATEGORY_EXAMPLES

FILTER_EMPTY_NOTICE = (
    "В выбранном разделе / виде тестов подходящих кейсов нет — попробуйте снять фильтр"
)

def _from_vector(item: Dict[str, Any], match_type: Optional[str]) -> Dict[str, Any]:
    metadata = item.get('metadata') or {}
    return {
        'content': item.get('document', ''),
        'source': metadata.get('filename', 'example.md'),
        'doc_id': item.get('id'),

        'testops_id': metadata.get('testops_id'),
        'score': item.get('score'),
        'score_scale': item.get('score_scale'),
        'match_type': match_type,
    }

def _from_row(row: Dict[str, Any], match_type: Optional[str]) -> Dict[str, Any]:
    return {
        'content': row.get('document', ''),
        'source': (row.get('metadata') or {}).get('filename') or row.get('filename', 'example.md'),
        'doc_id': row.get('doc_id'),
        'testops_id': row.get('testops_id'),
        'score': None,
        'score_scale': None,
        'match_type': match_type,
    }

def _with_texts(project_id: str, rows: List[Dict[str, Any]], id_key: str) -> List[Dict[str, Any]]:
    """Подтягивает тексты кейсов из PostgreSQL; записи, которых там нет, выкидываются."""
    return attach_texts(project_id, _CATEGORY, rows, id_key=id_key)

def _public(results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    public = []
    for item in results:
        score = item.get('score')
        if item.get('score_scale') != RERANK_SCORE_SCALE:
            score = None
        public.append({
            'content': item['content'],
            'source': item['source'],
            'doc_id': item['doc_id'],
            'testops_id': item.get('testops_id'),
            'score': round(float(score), 3) if score is not None else None,
            'match_type': item.get('match_type'),
        })
    return public

def _run_dense(
    store, project_id: str, query: SearchQuery, x, z, trace: SearchTrace,
    min_score: float = SEARCH_MIN_RERANK_SCORE, pool: int = SEARCH_CANDIDATE_POOL,
) -> List[Dict[str, Any]]:
    # Фильтр — первый шаг: подмножество задаётся SQL-условием section_x / test_type_z,
    # векторный поиск и реранк идут только внутри него.
    with trace.stage('filter') as rec:
        subset_size = store.count(x=x, z=z)
        rec['subset'] = subset_size
    trace.set(subset=subset_size)

    if subset_size == 0:
        trace.set(dense=0, rerank=0, after_floor=0)
        return []

    with trace.stage('dense') as rec:
        candidates = store.query(query.free_text, top_k=pool, x=x, z=z)
        rec['top'] = [c['id'] for c in candidates[:5]]
        candidates = _with_texts(project_id, candidates, id_key='id')
        rec['candidates'] = len(candidates)
    trace.set(dense=len(candidates))

    if not candidates:
        trace.set(rerank=0, after_floor=0)
        return []

    ranked = _rerank(query.free_text, candidates, trace)
    trace.set(rerank=len(ranked))

    if ranked and ranked[0].get('score_scale') != RERANK_SCORE_SCALE:
        raise SearchRerankUnavailableError(
            "Поиск по базе не может быть выполнен: реранкер не применился, "
            "и оценка релевантности не рассчитана. Проверьте, что "
            "RERANKER_ENABLED в core/config.py не выставлен в False и что модель "
            "реранкера доступна (подробности в логах сервера)."
        )

    with trace.stage('floor') as rec:
        kept, dropped_scores = [], []
        for item in ranked:
            score = float(item.get('score') or 0.0)
            if score >= min_score:
                kept.append(item)
            else:
                dropped_scores.append(score)
        rec['threshold'] = min_score
        rec['dropped'] = len(dropped_scores)

        rec['best_dropped'] = round(max(dropped_scores), 3) if dropped_scores else None
    trace.set(after_floor=len(kept))

    return [_from_vector(item, None) for item in kept]

def _rerank(query_text: str, candidates: List[Dict[str, Any]], trace: SearchTrace) -> List[Dict[str, Any]]:
    with trace.stage('rerank') as rec:
        ranked = rerank(query_text, candidates, top_k=SEARCH_TOP_K, text_key='search_document')
        rec['pairs'] = len(candidates)

        rec['chars'] = sum(len(c.get('search_document') or c.get('document') or '') for c in candidates)
        rec['scores'] = score_stats([float(r.get('score') or 0) for r in ranked])
    return ranked

def _phrase_variants(query: SearchQuery, use_lemma: bool) -> List[List[str]]:
    key = 'parts_lemmatized' if use_lemma else 'parts'
    return [list(getattr(phrase, key)) for phrase in query.phrases]

def _run_phrase(
    store, project_id: str, query: SearchQuery, x, z,
    offset: int, limit: int, trace: SearchTrace,
) -> Dict[str, Any]:
    need = offset + limit

    with trace.stage('sparse_exact') as rec:
        exact = sparse_store.phrase_search(
            project_id, _CATEGORY, _phrase_variants(query, use_lemma=False),
            use_lemma=False, x=x, z=z, size=need, from_=0,
        )
        rec['total'] = exact['total']
        rec['took'] = exact['took']
        rec['top'] = [(r['doc_id'], round(r['score'], 2)) for r in exact['rows'][:5]]
    trace.set(sparse_exact=exact['total'])

    rows = [dict(r, match_type=MATCH_EXACT) for r in exact['rows']]
    total = exact['total']
    lemma_total = 0

    if len(rows) < need and query.lemma_searchable:
        with trace.stage('sparse_lemma') as rec:
            lemma = sparse_store.phrase_search(
                project_id, _CATEGORY, _phrase_variants(query, use_lemma=True),
                use_lemma=True, x=x, z=z, size=need - len(rows), from_=0,
                exclude_doc_ids=[r['doc_id'] for r in rows],
            )
            rec['total'] = lemma['total']
            rec['took'] = lemma['took']
        lemma_total = lemma['total']
        total += lemma_total
        rows.extend(dict(r, match_type=MATCH_LEMMA) for r in lemma['rows'])
        trace.set(sparse_lemma=lemma_total)

    page = [
        _from_row(r, r['match_type'])
        for r in _with_texts(project_id, rows[offset:offset + limit], id_key='doc_id')
    ]
    trace.set(returned=len(page))

    return {
        'results': page,
        'total_found': total,
        'exact_count': exact['total'],
        'lemma_count': lemma_total,
        'has_more': total > offset + len(page),
    }

def _collect_ids(project_id: str, query: SearchQuery, use_lemma: bool, x, z, exclude=None) -> Dict[str, Any]:
    resp = sparse_store.phrase_search(
        project_id, _CATEGORY, _phrase_variants(query, use_lemma=use_lemma),
        use_lemma=use_lemma, x=x, z=z,
        size=SEARCH_CASCADE_SUBSET_LIMIT, from_=0,
        exclude_doc_ids=exclude, ids_only=True,
    )
    return {
        'ids': [r['doc_id'] for r in resp['rows'] if r['doc_id']],
        'total': resp['total'],
        'took': resp['took'],
        'capped': resp['total'] > SEARCH_CASCADE_SUBSET_LIMIT,
    }

def _rank_group(
    store, project_id: str, query: SearchQuery, doc_ids: List[str],
    match_type: str, trace: SearchTrace,
) -> List[Dict[str, Any]]:
    if not doc_ids:
        return []

    # doc_ids уже отфильтрованы по x/z в OpenSearch; dense-шаг идёт внутри этого подмножества.
    present = set(fetch_texts(project_id, _CATEGORY, doc_ids, warn_missing=True))
    present_ids = [d for d in doc_ids if d in present]

    embedded = store.embedded_ids(present_ids)
    not_embedded = [d for d in present_ids if d not in embedded]
    if not_embedded:
        logger.warning(
            f"{len(not_embedded)} кейсов найдены по фразе, но ещё не проиндексированы "
            f"в векторном хранилище (проект {project_id}) — нажмите «Индексация»."
        )
        trace.note('not_embedded', count=len(not_embedded), sample=not_embedded[:5])

    with trace.stage(f'dense_{match_type}') as rec:
        candidates = store.query(query.free_text, top_k=SEARCH_CANDIDATE_POOL, doc_ids=[d for d in present_ids if d in embedded])
        candidates = _with_texts(project_id, candidates, id_key='id')
        rec['subset'] = len(present_ids)
        rec['candidates'] = len(candidates)

    if SEARCH_CASCADE_RERANK and len(candidates) > SEARCH_TOP_K:
        ranked = _rerank(query.free_text, candidates, trace)
    else:
        ranked = candidates[:SEARCH_TOP_K]
        trace.note(f'rerank_{match_type}_skipped', candidates=len(candidates))

    return [_from_vector(item, match_type) for item in ranked]

def _run_cascade(store, project_id: str, query: SearchQuery, x, z, trace: SearchTrace) -> Dict[str, Any]:
    with trace.stage('sparse_exact') as rec:
        exact = _collect_ids(project_id, query, use_lemma=False, x=x, z=z)
        rec['total'] = exact['total']
        rec['took'] = exact['took']
        rec['capped'] = exact['capped']
    trace.set(sparse_exact=exact['total'])

    lemma = {'ids': [], 'total': 0, 'capped': False}
    if len(exact['ids']) < SEARCH_CANDIDATE_POOL and query.lemma_searchable:
        with trace.stage('sparse_lemma') as rec:
            lemma = _collect_ids(
                project_id, query, use_lemma=True, x=x, z=z, exclude=exact['ids'],
            )
            rec['total'] = lemma['total']
            rec['took'] = lemma['took']
        trace.set(sparse_lemma=lemma['total'])

    if not exact['ids'] and not lemma['ids']:
        return {'results': [], 'total_found': 0, 'exact_count': 0, 'lemma_count': 0, 'has_more': False}

    results = _rank_group(store, project_id, query, exact['ids'], MATCH_EXACT, trace)
    results += _rank_group(store, project_id, query, lemma['ids'], MATCH_LEMMA, trace)

    trace.set(returned=len(results))

    return {
        'results': results,
        'total_found': exact['total'] + lemma['total'],
        'exact_count': exact['total'],
        'lemma_count': lemma['total'],

        'has_more': False,
    }

def search_test_cases(
    store,
    project_id: str,
    query: str,
    x: Optional[int] = None,
    z: Optional[int] = None,
    offset: int = 0,
    limit: Optional[int] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    limit = limit or SEARCH_PAGE_SIZE
    offset = max(0, offset)

    trace = SearchTrace(query=query, project_id=project_id, category=_CATEGORY)

    with trace.stage('parse') as rec:
        parsed = parse_search_query(query)
        rec.update({
            'mode': parsed.mode,
            'phrases': [list(p.parts) for p in parsed.phrases],
            'free_text': parsed.free_text,
            'free_lemma': parsed.free_text_lemmatized,
            'unpaired_quote': parsed.dropped_unpaired_quote,
            'filter': {'x': x, 'z': z},
        })
    trace.set(mode=parsed.mode, filter=f"x={x},z={z}" if (x is not None or z is not None) else None)

    notices = []
    if parsed.dropped_unpaired_quote:
        notices.append(
            "В запросе нашлась непарная кавычка — она отброшена, "
            "поэтому точный поиск по ней не выполнялся."
        )

    payload: Dict[str, Any] = {
        'mode': parsed.mode,
        'search_id': trace.search_id,
        'phrases': [p.raw for p in parsed.phrases],
        'free_text': parsed.free_text,
        'filter': {'x': x, 'z': z},
        'offset': offset,
        'limit': limit,
        'exact_count': 0,
        'lemma_count': 0,
        'has_more': False,
    }

    try:
        if parsed.mode == MODE_EMPTY:
            payload.update({'results': [], 'total_found': 0, 'notices': notices})
            trace.set(returned=0)
            return payload

        has_filter = x is not None or z is not None

        if parsed.mode == MODE_DENSE:
            results = _run_dense(store, project_id, parsed, x, z, trace)
            if has_filter and not results:
                notices.append(FILTER_EMPTY_NOTICE)
            page = results[offset:offset + limit]
            trace.set(returned=len(page))
            payload.update({
                'results': _public(page),
                'total_found': len(results),

                'has_more': False,
                'notices': notices,
            })
            return payload

        branch = (
            _run_phrase(store, project_id, parsed, x, z, offset, limit, trace)
            if parsed.mode == MODE_PHRASE
            else _run_cascade(store, project_id, parsed, x, z, trace)
        )

        if branch['total_found'] == 0 and has_filter:
            notices.append(FILTER_EMPTY_NOTICE)
        elif branch['total_found'] == 0:
            notices.append(
                "Закавыченная фраза в базе не встречается — ни буквально, "
                "ни по словоформам. Поиск по остальным словам не выполнялся: "
                "кавычки означают обязательное совпадение."
            )

        payload.update({
            'results': _public(branch['results']),
            'total_found': branch['total_found'],
            'exact_count': branch['exact_count'],
            'lemma_count': branch['lemma_count'],
            'has_more': branch['has_more'],
            'notices': notices,
        })
        return payload
    finally:
        trace.set(returned=trace.counters.get('returned', 0))
        trace.finish()
        if debug:
            payload['trace'] = trace.to_dict()

def find_existing_for_generation(
    store,
    project_id: str,
    query: str,
    x: Optional[int] = None,
    z: Optional[int] = None,
    min_score: float = SEARCH_MIN_RERANK_SCORE,
    max_count: Optional[int] = None,
    pool: int = SEARCH_CANDIDATE_POOL,
) -> List[Dict[str, Any]]:
    query = (query or '').strip()
    if not query or min_score <= 0:
        return []

    trace = SearchTrace(query=query, project_id=project_id, category=_CATEGORY)
    trace.set(mode='generate_reuse', filter=f"x={x},z={z}" if (x is not None or z is not None) else None)

    pseudo_query = SearchQuery(
        raw=query, mode=MODE_DENSE, phrases=(),
        free_text=query, free_text_lemmatized='',
    )

    try:
        results = _run_dense(store, project_id, pseudo_query, x, z, trace, min_score=min_score, pool=pool)
    finally:
        trace.set(returned=trace.counters.get('after_floor', 0))
        trace.finish()

    if max_count:
        results = results[:max_count]

    return _public(results)
