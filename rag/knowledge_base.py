import hashlib
import logging
import threading
from typing import Any, Dict, List, Optional

import numpy as np

from sentence_transformers import SentenceTransformer

from db import client as db
from utils.markdown_convert import render_example_markdown, render_example_search_text
from utils.params_convert import params_to_text
from utils.steps_convert import steps_to_text
from .vector_store import (
    KIND_DOCUMENT,
    KIND_SHARED_STEP,
    KIND_TEST_CASE,
    VectorStore,
    check_embedding_dimension,
)
from .hybrid_search import hybrid_query
from .errors import SearchRerankUnavailableError
from .model_errors import is_missing_model_error, missing_model_error
from . import search_pipeline
from . import sparse_store
from .texts import (
    CATEGORY_DOCS,
    CATEGORY_EXAMPLES,
    CATEGORY_SHARED,
    example_filename,
    folder_breadcrumb,
    shared_step_filename,
)
from core.config import (
    OLLAMA_MODEL,
    EMBEDDING_MODEL_NAME,
    SEARCH_MIN_RERANK_SCORE,
    SEARCH_PAGE_SIZE,
    GENERATE_REUSE_MIN_SCORE,
)

__all__ = ['KnowledgeBase', 'SearchRerankUnavailableError']

CATEGORY_QUERY_INSTRUCTIONS = {
    "docs": "Instruct: Given a search query, retrieve relevant project documentation and requirements\nQuery: ",
    "examples": "Instruct: Given a search query, retrieve relevant QA test case describing steps and expected result\nQuery: ",
    "shared_steps": "Instruct: Given a search query, retrieve relevant reusable test step blocks\nQuery: ",
}

logger = logging.getLogger(__name__)

_embedding_model_cache: Dict[str, SentenceTransformer] = {}


def _get_shared_embedding_model(model_name: str) -> SentenceTransformer:
    if model_name not in _embedding_model_cache:
        logger.info(f"⏳ Загрузка embedding-модели {model_name} (первая загрузка в этом процессе)...")
        try:
            model = SentenceTransformer(model_name)
        except Exception as e:
            if is_missing_model_error(e):
                raise missing_model_error(model_name) from e
            raise
        check_embedding_dimension(model)
        _embedding_model_cache[model_name] = model
    return _embedding_model_cache[model_name]


def content_hash(text: str) -> str:
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


class KnowledgeBase:
    """База знаний проекта. Создание объекта индексацию не запускает."""

    DOCS_MIN_RELATIVE_SCORE = 0.3
    EXAMPLES_MIN_RELATIVE_SCORE = 0.5

    SEARCH_MIN_RERANK_SCORE = SEARCH_MIN_RERANK_SCORE
    SEARCH_PAGE_SIZE = SEARCH_PAGE_SIZE

    def __init__(
        self,
        project_id: str,
        model_name: Optional[str] = None,
        embedding_model_name: str = EMBEDDING_MODEL_NAME,
    ):
        self.project_id = project_id
        self.model_name = model_name or OLLAMA_MODEL
        # Одна индексация кейсов/общих шагов проекта за раз: фоновая после синхронизации,
        # кнопка «Индексация», точечная после экспорта не пишут одно и то же параллельно.
        self._index_lock = threading.RLock()

        shared_embedding_model = _get_shared_embedding_model(embedding_model_name)

        self.doc_store = VectorStore(
            project_id, KIND_DOCUMENT, shared_embedding_model,
            query_instruction=CATEGORY_QUERY_INSTRUCTIONS["docs"],
            model_name=embedding_model_name,
        )
        self.example_store = VectorStore(
            project_id, KIND_TEST_CASE, shared_embedding_model,
            query_instruction=CATEGORY_QUERY_INSTRUCTIONS["examples"],
            model_name=embedding_model_name,
        )
        self.shared_store = VectorStore(
            project_id, KIND_SHARED_STEP, shared_embedding_model,
            query_instruction=CATEGORY_QUERY_INSTRUCTIONS["shared_steps"],
            model_name=embedding_model_name,
        )

    # ===================== Подготовка записей к индексации =====================

    def _example_payload(self, row: Dict[str, Any], folder_titles: Dict[str, str]) -> Optional[tuple]:
        content = render_example_markdown(row).strip()
        if not content:
            return None
        # Эмбеддинг — по тексту со скрытыми полями (блок ожидаемого результата,
        # комбинации параметров): они участвуют в поиске, но пользователю не показываются.
        search_text = render_example_search_text(row)
        case_num = row.get('case_num') or ''
        folder_path = folder_breadcrumb(case_num, folder_titles)
        embed_text = f"{folder_path}. {search_text}" if folder_path else search_text
        filename = example_filename(row)
        fts_row = {
            "project_id": self.project_id,
            "category": CATEGORY_EXAMPLES,
            "doc_id": str(row['test_case_id']),
            "filename": filename,
            "source": f"{CATEGORY_EXAMPLES}/{filename}",
            "x": row.get('section_x'),
            "z": row.get('test_type_z'),
            "testops_id": row.get('testops_id'),
            "content": content,
            "folder_path": folder_path,
            "sections": {
                'title': row.get('title') or '',
                'description': row.get('description') or '',
                'steps': steps_to_text(row.get('steps')),
                'expected_result': row.get('expected_result') or '',
                'expected_result_block': row.get('expected_result_block') or '',
                'parameters': params_to_text(row.get('parameters')),
                'tags': ', '.join(row.get('tags') or []),
                'status': row.get('status') or '',
            },
        }
        return str(row['test_case_id']), embed_text, fts_row

    def _shared_payload(self, row: Dict[str, Any]) -> Optional[tuple]:
        content = (row.get('content') or '').strip()
        if not content:
            return None
        filename = shared_step_filename(row)
        fts_row = {
            "project_id": self.project_id,
            "category": CATEGORY_SHARED,
            "doc_id": str(row['shared_step_id']),
            "filename": filename,
            "source": f"{CATEGORY_SHARED}/{filename}",
            "x": None,
            "z": None,
            "testops_id": row.get('testops_id'),
            "content": content,
            "folder_path": '',
            "sections": None,
        }
        return str(row['shared_step_id']), content, fts_row

    def _document_payload(self, row: Dict[str, Any]) -> Optional[tuple]:
        content = (row.get('extracted_text') or '').strip()
        if not content:
            return None
        filename = row.get('filename') or ''
        fts_row = {
            "project_id": self.project_id,
            "category": CATEGORY_DOCS,
            "doc_id": str(row['document_id']),
            "filename": filename,
            "source": f"{CATEGORY_DOCS}/{filename}",
            "x": None,
            "z": None,
            "testops_id": None,
            "content": content,
            "folder_path": '',
            "sections": None,
        }
        return str(row['document_id']), content, fts_row

    # ===================== Индексация =====================

    def _index_payloads(
        self, category: str, store: VectorStore, payloads: List[tuple], force_rebuild: bool,
    ) -> Dict[str, Any]:
        """Инкрементальная индексация: пропускает записи с тем же content_hash и моделью."""
        existing = {} if force_rebuild else store.existing_hashes()

        to_index = []
        skipped = 0
        for record_id, embed_text, fts_row in payloads:
            h = content_hash(embed_text)
            if not force_rebuild and existing.get(record_id) == h:
                skipped += 1
                continue
            to_index.append((record_id, embed_text, h, fts_row))

        indexed = 0
        if to_index:
            logger.info(f"Индексация {len(to_index)} записей в категории {category} (без изменений: {skipped})")
            vectors = store.encode_texts([t[1] for t in to_index])
            store.upsert([t[0] for t in to_index], [t[2] for t in to_index], vectors)
            fts_stats = sparse_store.bulk_index([t[3] for t in to_index])
            indexed = len(to_index)
            logger.info(f"Проиндексировано {indexed} записей в {category}, полнотекстовый индекс: {fts_stats['indexed']}")
            if fts_stats['unstructured']:
                logger.warning(
                    f"{fts_stats['unstructured']} записей в {category} не разобрались "
                    f"по секциям и проиндексированы одним куском: поиск по секциям "
                    f"для них работать не будет"
                )
            if fts_stats['no_expected']:
                logger.warning(
                    f"У {fts_stats['no_expected']} кейсов в {category} пустой ожидаемый "
                    f"результат: запросы по нему этих кейсов не найдут"
                )
        else:
            logger.info(f"Категория {category}: изменений нет, пропущено {skipped}")

        # Уборка OpenSearch: удалить то, чего нет в БД; дописать то, чего в индексе нет
        # (эмбеддинг актуален, но документ пропал из OpenSearch — например, после сброса тома).
        current_ids = [p[0] for p in payloads]
        cleanup = sparse_store.cleanup_stale(self.project_id, category, current_ids)
        reindexed_ids = {t[0] for t in to_index}
        restore = [p[2] for p in payloads if p[0] in cleanup['missing'] and p[0] not in reindexed_ids]
        if restore:
            sparse_store.bulk_index(restore)
            logger.info(f"В OpenSearch восстановлено {len(restore)} записей категории {category}")
        if cleanup['deleted']:
            logger.info(f"Из OpenSearch удалено {cleanup['deleted']} устаревших записей категории {category}")

        return {'indexed': indexed, 'skipped': skipped, 'deleted': cleanup['deleted']}

    def load_documents(self, force_rebuild: bool = False) -> Dict[str, Any]:
        """Инкрементальная индексация проекта. force_rebuild=True — полная перестройка без учёта хэшей."""
        if force_rebuild:
            logger.info(f"Полная перестройка индексов проекта {self.project_id}")

        testops = self.index_testops(force_rebuild)
        examples, shared = testops['examples'], testops['shared_steps']

        docs = self._reindex_documents(force_rebuild)

        categories = {'examples': examples, 'shared_steps': shared, 'docs': docs}
        return {
            'indexed': sum(c['indexed'] for c in categories.values()),
            'skipped': sum(c['skipped'] for c in categories.values()),
            'deleted': sum(c['deleted'] for c in categories.values()),
            'failed': docs.get('failed', 0),
            'categories': categories,
        }

    def index_testops(self, force_rebuild: bool = False) -> Dict[str, Any]:
        """Индексация кейсов и общих шагов TestOps. Эмбеддинги пересчитываются только для
        записей с изменившимся текстом (content_hash); удалённые убираются из OpenSearch.
        Вызывается в фоне после синхронизации и из «Индексации»."""
        with self._index_lock:
            folder_titles = db.get_testops_folders(self.project_id)

            example_payloads = [
                p for p in (self._example_payload(r, folder_titles) for r in db.list_testops_test_cases(self.project_id))
                if p is not None
            ]
            examples = self._index_payloads(CATEGORY_EXAMPLES, self.example_store, example_payloads, force_rebuild)

            shared_payloads = [
                p for p in (self._shared_payload(r) for r in db.list_testops_shared_steps(self.project_id))
                if p is not None
            ]
            shared = self._index_payloads(CATEGORY_SHARED, self.shared_store, shared_payloads, force_rebuild)
        return {'examples': examples, 'shared_steps': shared}

    def _reindex_documents(self, force_rebuild: bool) -> Dict[str, Any]:
        """Документы с изменившимся хэшем или моделью. pending/indexing обрабатывает фоновая очередь."""
        rows = db.list_documents_for_indexing(self.project_id)
        existing = {} if force_rebuild else self.doc_store.existing_hashes()

        indexed = skipped = failed = 0
        restore = []
        for row in rows:
            if row['index_status'] in ('pending', 'indexing'):
                continue
            payload = self._document_payload(row)
            if payload is None:
                continue
            record_id, embed_text, fts_row = payload
            if (
                not force_rebuild
                and row['index_status'] == 'indexed'
                and existing.get(record_id) == content_hash(embed_text)
            ):
                skipped += 1
                restore.append(fts_row)
                continue
            if self.index_document(record_id):
                indexed += 1
            else:
                failed += 1

        current_ids = [str(r['document_id']) for r in rows]
        cleanup = sparse_store.cleanup_stale(self.project_id, CATEGORY_DOCS, current_ids)
        missing = [r for r in restore if r['doc_id'] in cleanup['missing']]
        if missing:
            sparse_store.bulk_index(missing)
            logger.info(f"В OpenSearch восстановлено {len(missing)} документов")

        return {'indexed': indexed, 'skipped': skipped, 'deleted': cleanup['deleted'], 'failed': failed}

    def index_document(self, document_id: str) -> bool:
        """Индексация одного документа: эмбеддинг всего текста + OpenSearch. Статусы — в documents."""
        row = db.get_document(self.project_id, document_id, with_text=True)
        if not row:
            logger.warning(f"Документ {document_id} не найден — индексация пропущена (вероятно, удалён)")
            return False

        db.set_document_status(document_id, 'indexing')
        try:
            payload = self._document_payload(row)
            if payload is None:
                raise ValueError("Из документа не извлечён текст — индексировать нечего")
            record_id, embed_text, fts_row = payload
            vectors = self.doc_store.encode_texts([embed_text])
            self.doc_store.upsert([record_id], [content_hash(embed_text)], vectors)
            sparse_store.bulk_index([fts_row])
            db.set_document_status(document_id, 'indexed')
            logger.info(f"Документ «{row.get('filename')}» проиндексирован")
            return True
        except Exception as e:
            logger.error(f"Ошибка индексации документа «{row.get('filename')}»: {e}", exc_info=True)
            for cleanup in (
                lambda: self.doc_store.delete(document_id),
                lambda: sparse_store.delete_doc(self.project_id, CATEGORY_DOCS, document_id),
            ):
                try:
                    cleanup()
                except Exception as cleanup_error:
                    logger.warning(f"Не удалось убрать частично записанный индекс документа {document_id}: {cleanup_error}")
            try:
                db.set_document_status(document_id, 'failed', error=str(e) or e.__class__.__name__)
            except Exception as status_error:
                logger.error(f"Не удалось записать статус failed документу {document_id}: {status_error}")
            return False

    def index_single_test_case(self, test_case_id: str) -> bool:
        """Индексация одного кейса TestOps (после экспорта)."""
        with self._index_lock:
            row = db.get_testops_test_case(self.project_id, test_case_id)
            if not row:
                return False
            payload = self._example_payload(row, db.get_testops_folders(self.project_id))
            if payload is None:
                return False
            record_id, embed_text, fts_row = payload
            vectors = self.example_store.encode_texts([embed_text])
            self.example_store.upsert([record_id], [content_hash(embed_text)], vectors)
            sparse_store.bulk_index([fts_row])
            return True

    # ===================== Поиск =====================

    @staticmethod
    def _filter_by_relative_score(
        results: List[Dict[str, Any]], min_relative_score: float = 0.5
    ) -> List[Dict[str, Any]]:
        if not results:
            return results
        best_score = max(r.get('score', 0) for r in results)
        if best_score <= 0:
            return results
        threshold = best_score * min_relative_score
        filtered = [r for r in results if r.get('score', 0) >= threshold]
        return filtered if filtered else results[:1]

    def _deduplicate_examples(
        self, items: List[Dict[str, str]], top_k: int, similarity_threshold: float = 0.92
    ) -> List[Dict[str, str]]:
        if len(items) <= 1:
            return items[:top_k]

        texts = [it['content'] for it in items]
        embs = self.example_store.encode_texts(texts).astype(np.float32)

        kept_indices: List[int] = []
        kept_embs: List[np.ndarray] = []
        for i in range(len(items)):
            if len(kept_indices) >= top_k:
                break
            is_duplicate = any(
                float(np.dot(embs[i], ke)) >= similarity_threshold for ke in kept_embs
            )
            if not is_duplicate:
                kept_indices.append(i)
                kept_embs.append(embs[i])

        return [items[i] for i in kept_indices]

    def get_docs(self, query: str = None, top_k: int = 20) -> List[Dict[str, str]]:
        if not query:
            return []
        results = hybrid_query(self.doc_store, self.project_id, CATEGORY_DOCS, query, top_k=top_k)
        results = self._filter_by_relative_score(results, min_relative_score=self.DOCS_MIN_RELATIVE_SCORE)
        return [
            {
                'content': res['document'],
                'source': res['metadata'].get('filename') or 'неизвестный файл',
            }
            for res in results
        ]

    def get_examples(self, query: str = None, top_k: int = 10) -> List[Dict[str, str]]:
        if query:
            return self.get_filtered_examples(query, top_k)
        examples = []
        for row in db.list_testops_test_cases(self.project_id)[:10]:
            content = render_example_markdown(row).strip()
            if content:
                examples.append({'content': content, 'source': example_filename(row)})
        return examples

    def get_shared_steps(self, query: str = None, top_k: int = 3) -> List[Dict[str, Any]]:
        if query:
            results = hybrid_query(self.shared_store, self.project_id, CATEGORY_SHARED, query, top_k=top_k)
            results = self._filter_by_relative_score(results)
            return [
                {
                    'content': res['document'],
                    'source': res['metadata'].get('filename') or 'shared_step',
                    'name': res['metadata'].get('name') or '',
                    'testops_id': res['metadata'].get('testops_id'),
                }
                for res in results
            ]
        shared_steps = []
        for row in db.list_testops_shared_steps(self.project_id)[:10]:
            content = row.get('content') or ''
            if content.strip():
                shared_steps.append({
                    'content': content,
                    'source': shared_step_filename(row),
                    'name': row.get('name') or '',
                    'testops_id': row.get('testops_id'),
                })
        return shared_steps

    def get_filtered_examples(self, query: str, top_k: int = 10,
                              x: Optional[int] = None, z: Optional[int] = None) -> List[Dict[str, str]]:
        """RAG-примеры: фильтр x/z сужает подмножество в обоих каналах поиска до ранжирования."""
        if not query:
            return []
        overfetch = min(top_k * 2, 40)
        results = hybrid_query(
            self.example_store, self.project_id, CATEGORY_EXAMPLES, query,
            top_k=overfetch, x=x, z=z,
        )
        results = self._filter_by_relative_score(results, min_relative_score=self.EXAMPLES_MIN_RELATIVE_SCORE)
        candidates = [
            {'content': r['document'], 'source': r['metadata'].get('filename') or 'example.md'}
            for r in results
        ]
        return self._deduplicate_examples(candidates, top_k)

    def search_test_cases(
        self,
        query: str,
        x: Optional[int] = None,
        z: Optional[int] = None,
        offset: int = 0,
        limit: Optional[int] = None,
        debug: bool = False,
    ) -> Dict[str, Any]:
        return search_pipeline.search_test_cases(
            self.example_store, self.project_id, query,
            x=x, z=z, offset=offset, limit=limit, debug=debug,
        )

    def find_existing_test_cases(
        self,
        query: str,
        x: Optional[int] = None,
        z: Optional[int] = None,
        max_count: Optional[int] = None,
        min_score: Optional[float] = None,
        extra_queries: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Похожие кейсы по запросу и доп. запросам (описание скриншотов): у кейса — лучший балл."""
        threshold = GENERATE_REUSE_MIN_SCORE if min_score is None else min_score
        best: Dict[str, Dict[str, Any]] = {}
        for q in self._queries(query, extra_queries):
            for item in search_pipeline.find_existing_for_generation(
                self.example_store, self.project_id, q,
                x=x, z=z, min_score=threshold,
            ):
                key = item.get('doc_id')
                if key and (key not in best or (item.get('score') or 0) > (best[key].get('score') or 0)):
                    best[key] = item
        results = sorted(best.values(), key=lambda r: r.get('score') or 0, reverse=True)
        return results[:max_count] if max_count else results

    @staticmethod
    def _queries(query: str, extra_queries: Optional[List[str]]) -> List[str]:
        queries = []
        for q in [query, *(extra_queries or [])]:
            q = (q or '').strip()
            if q and q not in queries:
                queries.append(q)
        return queries

    @staticmethod
    def _fuse(result_lists: List[List[Dict[str, Any]]], top_k: int, k: int = 60) -> List[Dict[str, Any]]:
        """Reciprocal Rank Fusion нескольких выдач; одинаковые записи (по тексту) склеиваются."""
        if len(result_lists) == 1:
            return result_lists[0][:top_k]
        scores: Dict[str, float] = {}
        items: Dict[str, Dict[str, Any]] = {}
        for results in result_lists:
            for rank, item in enumerate(results):
                key = item.get('content') or ''
                scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
                items.setdefault(key, item)
        ordered = sorted(scores, key=scores.get, reverse=True)
        return [items[key] for key in ordered[:top_k]]

    def get_rag_context(
        self,
        query: str,
        top_k_docs: int = 3,
        top_k_examples: int = 5,
        top_k_shared: int = 3,
        x: Optional[int] = None,
        z: Optional[int] = None,
        extra_queries: Optional[List[str]] = None,
    ) -> Dict[str, List[Dict[str, str]]]:
        """Контекст для генерации. extra_queries (описание скриншотов) ищутся отдельно
        и сливаются с выдачей по основному запросу — длинный склеенный запрос размыл бы поиск."""
        queries = self._queries(query, extra_queries)
        if not queries:
            return {'docs': [], 'examples': [], 'shared_steps': []}
        return {
            'docs': self._fuse([self.get_docs(q, top_k_docs) for q in queries], top_k_docs),
            'examples': self._fuse(
                [self.get_filtered_examples(q, top_k_examples, x, z) for q in queries], top_k_examples,
            ),
            'shared_steps': self._fuse([self.get_shared_steps(q, top_k_shared) for q in queries], top_k_shared),
        }

    def get_project_info(self) -> Optional[str]:
        return db.find_project_info_text(self.project_id)

    def get_project_info_dict(self) -> Dict[str, Any]:
        categories = {
            'docs': self.doc_store.count(),
            'examples': self.example_store.count(),
            'shared_steps': self.shared_store.count(),
        }
        return {
            'project_id': self.project_id,
            'documents_count': sum(categories.values()),
            'categories': categories,
        }
