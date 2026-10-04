import logging
import uuid
from typing import Any, Dict, List, Optional

from db import client as db

logger = logging.getLogger(__name__)

DEFAULT_STATUS = 'Готово к автоматизации'


def testops_row_to_checklist_item(
    row: Dict[str, Any], score: Optional[float] = None, match_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Кейс из testops_test_cases -> элемент чек-листа чата (origin = search)."""
    testops_id = row.get('testops_id')
    case_num = row.get('case_num')
    return {
        'id': str(uuid.uuid4()),
        'title': row.get('title') or '',
        'description': row.get('description') or '',
        'steps': row.get('steps') or [],
        'expected_result': row.get('expected_result') or '',
        'tags': list(row.get('tags') or []),
        'workflow': row.get('workflow') or '',
        'status': row.get('status') or DEFAULT_STATUS,
        'case_num': case_num,

        'selected': False,
        'sources': [],
        'origin': 'search',
        'source': f"{case_num}.md" if case_num else None,
        'doc_id': str(row['test_case_id']),
        'testops_id': testops_id,
        'score': score,
        'match_type': match_type,

        'edited_since_search': False,

        'needs_export': testops_id is None,
    }


def search_results_to_checklist_items(
    results: List[Dict[str, Any]], project_id: str,
) -> List[Dict[str, Any]]:
    """Результаты поиска -> элементы чек-листа. Кейсы, которых уже нет в БД, пропускаются."""
    ids = [r.get('doc_id') for r in results if r.get('doc_id')]
    rows = db.get_testops_test_cases_by_ids(project_id, ids)
    items = []
    for result in results:
        row = rows.get(str(result.get('doc_id')))
        if not row:
            logger.warning(
                f"testops_test_cases: запись {result.get('doc_id')} не найдена для результата "
                f"поиска (testops_id={result.get('testops_id')}) — пропущена."
            )
            continue
        items.append(testops_row_to_checklist_item(
            row, score=result.get('score'), match_type=result.get('match_type'),
        ))
    return items


def search_result_to_checklist_item(result: Dict[str, Any], project_id: str) -> Optional[Dict[str, Any]]:
    items = search_results_to_checklist_items([result], project_id)
    return items[0] if items else None
