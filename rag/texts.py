"""Тексты записей базы знаний.

Текст хранится только в исходных таблицах PostgreSQL: в embeddings и OpenSearch
его копии нет. При выдаче результатов текст подтягивается отсюда одним
запросом по списку id.
"""
import logging
import re
from typing import Any, Dict, Optional, Sequence

from db import client as db
from utils.markdown_convert import render_example_markdown, render_example_search_text

logger = logging.getLogger(__name__)

CATEGORY_DOCS = 'docs'
CATEGORY_EXAMPLES = 'examples'
CATEGORY_SHARED = 'shared_steps'

_VERSION_RE = re.compile(r'^\d+(?:\.\d+)*$')


def folder_breadcrumb(case_num: str, folder_titles: Dict[str, str]) -> str:
    if not folder_titles or not _VERSION_RE.match(case_num or ''):
        return ''
    parts = case_num.split('.')
    prefixes = ['.'.join(parts[:i]) for i in range(1, len(parts) + 1)]
    names = [folder_titles[p] for p in prefixes if p in folder_titles]
    return ' → '.join(names)


def example_filename(row: Dict[str, Any]) -> str:
    case_num = row.get('case_num')
    return f"{case_num}.md" if case_num else f"{row.get('id') or row.get('test_case_id')}.md"


def shared_step_filename(row: Dict[str, Any]) -> str:
    return row.get('name') or f"shared_step_{row.get('testops_id')}"


def example_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'id': str(row['test_case_id']),
        # text — то, что видит пользователь и модель генерации;
        # search_text — плюс скрытые поля (блок ожидаемого результата, комбинации параметров).
        'text': render_example_markdown(row).strip(),
        'search_text': render_example_search_text(row),
        'filename': example_filename(row),
        'testops_id': row.get('testops_id'),
        'x': row.get('section_x'),
        'z': row.get('test_type_z'),
    }


def shared_step_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'id': str(row['shared_step_id']),
        'text': (row.get('content') or '').strip(),
        'filename': shared_step_filename(row),
        'name': row.get('name') or '',
        'testops_id': row.get('testops_id'),
    }


def document_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'id': str(row['document_id']),
        'text': (row.get('extracted_text') or '').strip(),
        'filename': row.get('filename') or 'документ',
        'testops_id': None,
    }


def fetch_texts(
    project_id: str, category: str, ids: Sequence[str], warn_missing: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """{id: {'id', 'text', 'filename', 'testops_id', ...}} для указанных записей.

    Записи, которых нет в PostgreSQL, пропускаются (с предупреждением в лог).
    Документы отдаются только в статусе indexed.
    """
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}

    if category == CATEGORY_EXAMPLES:
        rows = db.get_testops_test_cases_by_ids(project_id, ids)
        items = {i: example_item(r) for i, r in rows.items()}
    elif category == CATEGORY_SHARED:
        rows = db.get_testops_shared_steps_by_ids(project_id, ids)
        items = {i: shared_step_item(r) for i, r in rows.items()}
    elif category == CATEGORY_DOCS:
        rows = db.get_documents_texts(project_id, ids, only_indexed=True)
        items = {i: document_item(r) for i, r in rows.items()}
        if warn_missing:
            missing = [i for i in ids if i not in items]
            if missing:
                # Документ может быть в БД, но ещё не проиндексирован — это не рассинхрон.
                present = db.existing_document_ids(project_id, missing)
                missing = [i for i in missing if i not in present]
                if missing:
                    logger.warning(
                        f"Документов нет в PostgreSQL, но они найдены в индексе "
                        f"(проект {project_id}): {len(missing)}, например {missing[:3]}. Пропущены."
                    )
        return items
    else:
        raise ValueError(f"Неизвестная категория {category}")

    if warn_missing:
        missing = [i for i in ids if i not in items]
        if missing:
            logger.warning(
                f"Записей категории {category} нет в PostgreSQL, но они найдены в индексе "
                f"(проект {project_id}): {len(missing)}, например {missing[:3]}. Пропущены — "
                f"индекс будет вычищен при следующей индексации."
            )
    return items


def attach_texts(
    project_id: str, category: str, items: Sequence[Dict[str, Any]], id_key: str = 'id',
) -> list:
    """Добавляет к результатам поиска 'document' (текст) и 'metadata'; без текста — выкидывает."""
    texts = fetch_texts(project_id, category, [it.get(id_key) for it in items])
    result = []
    for it in items:
        found: Optional[Dict[str, Any]] = texts.get(str(it.get(id_key)))
        if not found or not found['text']:
            continue
        enriched = dict(it)
        enriched['document'] = found['text']
        # Реранк идёт по тексту со скрытыми полями; наружу отдаётся только document.
        enriched['search_document'] = found.get('search_text') or found['text']
        enriched['metadata'] = {
            'filename': found['filename'],
            'testops_id': found.get('testops_id'),
            'category': category,
            'name': found.get('name'),
        }
        result.append(enriched)
    return result
