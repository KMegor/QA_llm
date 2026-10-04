
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from opensearchpy import NotFoundError, OpenSearch, helpers as os_helpers

from core.config import (
    OPENSEARCH_URL,
    OPENSEARCH_INDEX_PREFIX,
    PHRASE_MAX_GAPS,
    PHRASE_POSITION_GAP,
)
from .lemmatizer import lemmatize

logger = logging.getLogger(__name__)

_INDEX_NAME = f"{OPENSEARCH_INDEX_PREFIX}_fts_v3"

_FOLDER_PATH_BOOST = 2.5
_MINIMUM_SHOULD_MATCH = "30%"

SECTION_LABELS = {
    'title': 'Название',
    'description': 'Описание',
    'steps': 'Шаги',
    'expected_result': 'Ожидаемый результат',
    'expected_result_block': 'Ожидаемый результат',
    'parameters': 'Комбинации параметров',
    'tags': 'Теги',
    'status': 'Статус',
    'folder': 'Папка',
}

_SECTION_ORDER = (
    'title', 'description', 'steps', 'expected_result', 'expected_result_block',
    'parameters', 'tags', 'status', 'folder',
)

_SECTION_FIELDS = (
    'title', 'description', 'steps', 'expected_result', 'expected_result_block',
    'parameters', 'folder',
)

_client: Optional[OpenSearch] = None

def _get_client() -> OpenSearch:
    global _client
    if _client is None:
        _client = OpenSearch(hosts=[OPENSEARCH_URL], timeout=30)
    return _client

def _text_field(analyzer: str, position_gap: Optional[int] = None) -> Dict[str, Any]:
    field: Dict[str, Any] = {"type": "text", "analyzer": analyzer}
    if position_gap is not None:
        field["position_increment_gap"] = position_gap
    return field

_index_ready = False

def ensure_index() -> None:
    global _index_ready
    if _index_ready:
        return
    client = _get_client()
    if client.indices.exists(index=_INDEX_NAME):
        _add_missing_section_fields(client)
        _index_ready = True
        return

    properties: Dict[str, Any] = {
        "project_id": {"type": "keyword"},
        "category": {"type": "keyword"},
        "doc_id": {"type": "keyword"},
        "filename": {"type": "keyword"},
        "source": {"type": "keyword"},
        "x": {"type": "integer"},
        "z": {"type": "integer"},

        "testops_id": {"type": "integer"},
        "tags": {"type": "keyword"},
        "status": {"type": "keyword"},

        "body_exact": _text_field("exact_ci", PHRASE_POSITION_GAP),
        "body_lemma": _text_field("lemma_ws", PHRASE_POSITION_GAP),
    }
    for name in _SECTION_FIELDS:
        properties[f"{name}_exact"] = _text_field("exact_ci")
        properties[f"{name}_lemma"] = _text_field("lemma_ws")

    client.indices.create(
        index=_INDEX_NAME,
        body={
            "settings": {
                "similarity": {"default": {"type": "BM25"}},
                "analysis": {
                    "analyzer": {

                        "exact_ci": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase"],
                        },

                        "lemma_ws": {
                            "type": "custom",
                            "tokenizer": "whitespace",
                            "filter": ["lowercase"],
                        },
                    }
                },
            },
            "mappings": {"properties": properties},
        },
    )
    _index_ready = True

def _add_missing_section_fields(client: OpenSearch) -> None:
    """Индекс создан до появления новой секции (например, parameters): дописываем её поля
    в маппинг с нужными анализаторами. Иначе OpenSearch создал бы их сам при первой
    записи — со стандартным анализатором, и точный / лемматизированный поиск по ним
    работал бы иначе, чем по остальным секциям."""
    mapping = client.indices.get_mapping(index=_INDEX_NAME)
    existing = (mapping.get(_INDEX_NAME) or next(iter(mapping.values()), {})).get('mappings', {}).get('properties', {})
    missing: Dict[str, Any] = {}
    for name in _SECTION_FIELDS:
        if f"{name}_exact" not in existing:
            missing[f"{name}_exact"] = _text_field("exact_ci")
        if f"{name}_lemma" not in existing:
            missing[f"{name}_lemma"] = _text_field("lemma_ws")
    if missing:
        client.indices.put_mapping(index=_INDEX_NAME, body={"properties": missing})
        logger.info(f"В маппинг OpenSearch добавлены поля: {', '.join(sorted(missing))}")

def count(project_id: str) -> int:
    client = _get_client()
    ensure_index()
    resp = client.count(index=_INDEX_NAME, body={"query": {"term": {"project_id": project_id}}})
    return int(resp.get("count", 0))

def reset(project_id: str) -> None:
    client = _get_client()
    ensure_index()
    client.delete_by_query(
        index=_INDEX_NAME,
        body={"query": {"term": {"project_id": project_id}}},
        conflicts="proceed",
    )

def build_document_fields(
    category: str, content: str, folder_path: str = '', sections: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    content = content or ''
    fields: Dict[str, Any] = {"tags": [], "status": ""}
    body_exact: List[str] = []
    body_lemma: List[str] = []

    def add(key: str, text: str) -> None:
        text = (text or '').strip()
        if not text:
            return
        label = SECTION_LABELS.get(key, '')
        labelled = f"{label} {text}" if label else text
        body_exact.append(labelled)
        body_lemma.append(lemmatize(labelled))
        if key in _SECTION_FIELDS:
            fields[f"{key}_exact"] = text
            fields[f"{key}_lemma"] = lemmatize(text)

    structured = category == 'examples' and sections is not None

    if structured:
        for key in _SECTION_ORDER:
            if key == 'folder':
                continue
            add(key, sections.get(key, ''))
        if sections.get('tags'):
            fields["tags"] = [t.strip() for t in sections['tags'].split(',') if t.strip()]
        fields["status"] = sections.get('status', '')
    else:
        add('_raw', content)

    add('folder', folder_path)

    fields["body_exact"] = body_exact
    fields["body_lemma"] = body_lemma
    fields["structured"] = structured
    return fields

def bulk_index(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    if not rows:
        return {"indexed": 0, "unstructured": 0, "no_expected": 0}

    ensure_index()

    stats = {"indexed": 0, "unstructured": 0, "no_expected": 0}
    prepared = []

    for row in rows:
        content = row.get("content", "")
        folder_path = row.get("folder_path") or ""
        fields = build_document_fields(row["category"], content, folder_path, sections=row.get("sections"))

        if row["category"] == 'examples':
            if not fields.pop("structured", False):
                stats["unstructured"] += 1
            elif not fields.get("expected_result_exact"):
                stats["no_expected"] += 1
        else:
            fields.pop("structured", None)

        source = {
            "project_id": row["project_id"],
            "category": row["category"],
            "doc_id": row["doc_id"],
            "filename": row.get("filename", ""),
            "source": row.get("source", ""),
            "x": row.get("x"),
            "z": row.get("z"),
            "testops_id": row.get("testops_id"),
        }
        source.update(fields)

        prepared.append({
            "_op_type": "index",
            "_index": _INDEX_NAME,
            "_id": f"{row['project_id']}::{row['category']}::{row['doc_id']}",
            "_source": source,
        })

    success, _ = os_helpers.bulk(_get_client(), prepared)
    stats["indexed"] = success
    return stats

def _filters(project_id: str, category: str, x: Optional[int], z: Optional[int]) -> List[Dict[str, Any]]:
    filters: List[Dict[str, Any]] = [
        {"term": {"project_id": project_id}},
        {"term": {"category": category}},
    ]
    if x is not None:
        filters.append({"term": {"x": x}})
    if z is not None:
        filters.append({"term": {"z": z}})
    return filters

def _phrase_clause(field: str, parts: Sequence[str]) -> Dict[str, Any]:
    if len(parts) == 1:
        return {"match_phrase": {field: {"query": parts[0]}}}

    return {
        "intervals": {
            field: {
                "all_of": {
                    "ordered": True,
                    "max_gaps": PHRASE_MAX_GAPS,
                    "intervals": [
                        {"match": {"query": part, "ordered": True}} for part in parts
                    ],
                }
            }
        }
    }

def phrase_search(
    project_id: str,
    category: str,
    phrases: Sequence[Sequence[str]],
    use_lemma: bool = False,
    x: Optional[int] = None,
    z: Optional[int] = None,
    size: int = 50,
    from_: int = 0,
    exclude_doc_ids: Optional[Sequence[str]] = None,
    ids_only: bool = False,
) -> Dict[str, Any]:
    if not phrases:
        return {"total": 0, "rows": [], "took": 0}

    ensure_index()
    field = "body_lemma" if use_lemma else "body_exact"

    bool_query: Dict[str, Any] = {
        "filter": _filters(project_id, category, x, z),
        "must": [_phrase_clause(field, parts) for parts in phrases],
    }
    if exclude_doc_ids:
        bool_query["must_not"] = [{"terms": {"doc_id": list(exclude_doc_ids)}}]

    body: Dict[str, Any] = {
        "query": {"bool": bool_query},
        "size": size,
        "from": from_,
        "track_total_hits": True,
    }
    if ids_only:
        body["_source"] = ["doc_id"]

    resp = _get_client().search(index=_INDEX_NAME, body=body)
    return _unpack(resp)

def free_text_search(
    project_id: str,
    category: str,
    free_text_lemmatized: str,
    top_k: int = 20,
    x: Optional[int] = None,
    z: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if not free_text_lemmatized:
        return []
    ensure_index()

    bool_query = {
        "filter": _filters(project_id, category, x, z),
        "should": [{
            "multi_match": {
                "query": free_text_lemmatized,
                "fields": ["body_lemma", f"folder_lemma^{_FOLDER_PATH_BOOST}"],
                "type": "most_fields",
                "minimum_should_match": _MINIMUM_SHOULD_MATCH,
            }
        }],
        "minimum_should_match": 1,
    }

    resp = _get_client().search(
        index=_INDEX_NAME,
        body={"query": {"bool": bool_query}, "size": top_k},
    )
    return _unpack(resp)["rows"]

def _unpack(resp: Dict[str, Any]) -> Dict[str, Any]:
    hits = resp.get("hits", {})
    total = hits.get("total", {})
    total_value = total.get("value", 0) if isinstance(total, dict) else int(total or 0)

    rows = []
    for hit in hits.get("hits", []):
        src = hit.get("_source", {})
        rows.append({
            "doc_id": src.get("doc_id"),
            "filename": src.get("filename", ""),
            "x": src.get("x"),
            "z": src.get("z"),
            "testops_id": src.get("testops_id"),
            "score": hit.get("_score", 0.0),
        })

    return {"total": int(total_value), "rows": rows, "took": int(resp.get("took", 0))}

def _doc_key(project_id: str, category: str, doc_id: str) -> str:
    return f"{project_id}::{category}::{doc_id}"

def delete_doc(project_id: str, category: str, doc_id: str) -> bool:
    ensure_index()
    try:
        _get_client().delete(index=_INDEX_NAME, id=_doc_key(project_id, category, str(doc_id)))
        return True
    except NotFoundError:
        return False

def delete_docs(project_id: str, category: str, doc_ids: Sequence[str]) -> int:
    doc_ids = [str(d) for d in doc_ids or []]
    if not doc_ids:
        return 0
    ensure_index()
    actions = [
        {"_op_type": "delete", "_index": _INDEX_NAME, "_id": _doc_key(project_id, category, d)}
        for d in doc_ids
    ]
    success, _ = os_helpers.bulk(_get_client(), actions, raise_on_error=False)
    return success

def list_doc_ids(project_id: str, category: str) -> Set[str]:
    """Все doc_id проекта в категории (для сверки с PostgreSQL)."""
    ensure_index()
    ids: Set[str] = set()
    for hit in os_helpers.scan(
        _get_client(),
        index=_INDEX_NAME,
        query={"query": {"bool": {"filter": [
            {"term": {"project_id": project_id}},
            {"term": {"category": category}},
        ]}}},
        _source=["doc_id"],
    ):
        doc_id = (hit.get("_source") or {}).get("doc_id")
        if doc_id:
            ids.add(str(doc_id))
    return ids

def cleanup_stale(project_id: str, category: str, current_ids: Iterable[str]) -> Dict[str, Any]:
    """Удаляет из OpenSearch документы, которых больше нет в БД.

    Возвращает число удалённых и id из БД, которых нет в OpenSearch.
    """
    current = {str(i) for i in current_ids}
    indexed = list_doc_ids(project_id, category)
    stale = sorted(indexed - current)
    deleted = delete_docs(project_id, category, stale) if stale else 0
    return {"deleted": deleted, "missing": current - indexed}
