"""Векторное хранилище поверх таблицы embeddings (pgvector).

Одна реализация на три вида записей: от вида зависит FK-колонка в embeddings
и таблица-источник для JOIN. Текст в embeddings не хранится.
"""
import logging
import threading
from typing import Any, Dict, List, Optional, Sequence, Set

import numpy as np

from core.config import EMBEDDING_BATCH_SIZE, EMBEDDING_DIMENSION, EMBEDDING_MODEL_NAME
from db.client import get_conn

logger = logging.getLogger(__name__)

KIND_TEST_CASE = 'test_case'
KIND_SHARED_STEP = 'shared_step'
KIND_DOCUMENT = 'document'

# вид -> (FK-колонка в embeddings, таблица-источник, PK таблицы-источника)
_KINDS = {
    KIND_TEST_CASE: ('test_case_id', 'testops_test_cases', 'test_case_id'),
    KIND_SHARED_STEP: ('shared_step_id', 'testops_shared_steps', 'shared_step_id'),
    KIND_DOCUMENT: ('document_id', 'documents', 'document_id'),
}

# Общий замок на encode: фоновая индексация документов идёт параллельно с запросами.
ENCODE_LOCK = threading.Lock()

_schema_checked = False


def _vector_literal(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in vec.tolist()) + "]"


def normalize(embeddings: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embeddings / norms


def encode_texts(model, texts: Sequence[str], batch_size: int = EMBEDDING_BATCH_SIZE) -> np.ndarray:
    with ENCODE_LOCK:
        embeddings = model.encode(list(texts), convert_to_numpy=True, batch_size=batch_size)
    return normalize(np.asarray(embeddings, dtype=np.float32))


def check_embedding_dimension(model) -> None:
    """Размерность модели должна совпадать с колонкой embeddings.embedding (vector(2560))."""
    global _schema_checked
    model_dim = model.get_sentence_embedding_dimension()
    if model_dim != EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"Размерность эмбеддингов модели {EMBEDDING_MODEL_NAME} — {model_dim}, "
            f"а колонка embeddings.embedding рассчитана на vector({EMBEDDING_DIMENSION}). "
            f"Верните прежнюю модель или поменяйте размерность в db/init.sql и "
            f"EMBEDDING_DIMENSION в core/config.py и пересоздайте БД."
        )
    if _schema_checked:
        return
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT atttypmod FROM pg_attribute
            WHERE attrelid = 'embeddings'::regclass AND attname = 'embedding'
            """
        )
        row = cur.fetchone()
    db_dim = int(row['atttypmod']) if row else -1
    if db_dim > 0 and db_dim != model_dim:
        raise RuntimeError(
            f"Колонка embeddings.embedding в БД имеет размерность vector({db_dim}), "
            f"а модель {EMBEDDING_MODEL_NAME} выдаёт {model_dim}. Пересоздайте БД "
            f"(docker compose down -v) с актуальным db/init.sql."
        )
    _schema_checked = True


class VectorStore:
    def __init__(
        self,
        project_id: str,
        kind: str,
        embedding_model,
        query_instruction: str = "",
        model_name: str = EMBEDDING_MODEL_NAME,
    ):
        if kind not in _KINDS:
            raise ValueError(f"Неизвестный вид записей: {kind}")
        self.project_id = project_id
        self.kind = kind
        self.fk_column, self.source_table, self.source_pk = _KINDS[kind]
        self.embedding_model = embedding_model
        self.query_instruction = query_instruction
        self.model_name = model_name
        self.dimension = embedding_model.get_sentence_embedding_dimension()

    # ---------- эмбеддинги ----------

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        return encode_texts(self.embedding_model, texts)

    def encode_query(self, query_text: str) -> np.ndarray:
        text = f"{self.query_instruction}{query_text}" if self.query_instruction else query_text
        return encode_texts(self.embedding_model, [text], batch_size=1)[0]

    # ---------- запись ----------

    def existing_hashes(self) -> Dict[str, str]:
        """{id записи: content_hash} для эмбеддингов текущей модели."""
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {self.fk_column} AS id, content_hash
                FROM embeddings
                WHERE project_id = %s AND model = %s AND {self.fk_column} IS NOT NULL
                """,
                (self.project_id, self.model_name),
            )
            return {str(r['id']): (r['content_hash'] or '').strip() for r in cur.fetchall()}

    def upsert(self, ids: Sequence[str], hashes: Sequence[str], vectors: np.ndarray) -> int:
        if not len(ids):
            return 0
        with get_conn() as conn, conn.cursor() as cur:
            for record_id, content_hash, vec in zip(ids, hashes, vectors):
                cur.execute(
                    f"""
                    INSERT INTO embeddings (project_id, {self.fk_column}, content_hash, model, embedding)
                    VALUES (%s, %s, %s, %s, %s::vector)
                    ON CONFLICT ({self.fk_column}) WHERE {self.fk_column} IS NOT NULL
                    DO UPDATE SET content_hash = EXCLUDED.content_hash,
                                  model = EXCLUDED.model,
                                  embedding = EXCLUDED.embedding,
                                  created_at = NOW()
                    """,
                    (self.project_id, record_id, content_hash, self.model_name, _vector_literal(vec)),
                )
        return len(ids)

    def delete(self, record_id: str) -> None:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM embeddings WHERE {self.fk_column} = %s",
                (record_id,),
            )

    # ---------- чтение ----------

    def _filters(
        self, doc_ids: Optional[Sequence[str]], x: Optional[int], z: Optional[int],
    ) -> tuple:
        conditions = ["e.project_id = %s", "e.model = %s", f"e.{self.fk_column} IS NOT NULL"]
        params: List[Any] = [self.project_id, self.model_name]
        join = ""
        if self.kind == KIND_TEST_CASE and (x is not None or z is not None):
            join = f"JOIN {self.source_table} AS s ON s.{self.source_pk} = e.{self.fk_column}"
            if x is not None:
                conditions.append("s.section_x = %s")
                params.append(int(x))
            if z is not None:
                conditions.append("s.test_type_z = %s")
                params.append(int(z))
        elif self.kind == KIND_DOCUMENT:
            # Документ участвует в поиске только после успешной индексации.
            join = f"JOIN {self.source_table} AS s ON s.{self.source_pk} = e.{self.fk_column}"
            conditions.append("s.index_status = 'indexed'")
        if doc_ids is not None:
            conditions.append(f"e.{self.fk_column} = ANY(%s::uuid[])")
            params.append([str(d) for d in doc_ids])
        return join, " AND ".join(conditions), params

    def count(self, x: Optional[int] = None, z: Optional[int] = None) -> int:
        join, where, params = self._filters(None, x, z)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM embeddings AS e {join} WHERE {where}", params)
            return int(cur.fetchone()['n'])

    def embedded_ids(self, doc_ids: Sequence[str]) -> Set[str]:
        if not doc_ids:
            return set()
        join, where, params = self._filters(doc_ids, None, None)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT e.{self.fk_column} AS id FROM embeddings AS e {join} WHERE {where}",
                params,
            )
            return {str(r['id']) for r in cur.fetchall()}

    def query(
        self,
        query_text: str,
        top_k: int = 50,
        doc_ids: Optional[Sequence[str]] = None,
        x: Optional[int] = None,
        z: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Косинусная близость запроса к записям. Фильтр x/z и список id сужают подмножество до поиска."""
        if not (query_text or '').strip() or top_k <= 0:
            return []
        if doc_ids is not None and not doc_ids:
            return []

        query_vec = _vector_literal(self.encode_query(query_text))
        join, where, params = self._filters(doc_ids, x, z)

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT e.{self.fk_column} AS id, 1 - (e.embedding <=> %s::vector) AS score
                FROM embeddings AS e {join}
                WHERE {where}
                ORDER BY e.embedding <=> %s::vector
                LIMIT %s
                """,
                [query_vec, *params, query_vec, top_k],
            )
            rows = cur.fetchall()

        return [{'id': str(r['id']), 'score': float(r['score'])} for r in rows]
