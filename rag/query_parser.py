
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .lemmatizer import lemmatize

MODE_DENSE = 'dense'
MODE_PHRASE = 'phrase'
MODE_CASCADE = 'cascade'
MODE_EMPTY = 'empty'

_QUOTE_CHARS = '«»“”„‟〝〞＂'
_QUOTE_TRANSLATION = str.maketrans({ch: '"' for ch in _QUOTE_CHARS})

_PHRASE_RE = re.compile(r'"([^"]*)"')

_GAP_RE = re.compile(r'\s*(?:\.\s*){3,}\s*|\s*…\s*')

@dataclass(frozen=True)
class Phrase:
    raw: str
    parts: Tuple[str, ...]
    parts_lemmatized: Tuple[str, ...]

    @property
    def has_gaps(self) -> bool:
        return len(self.parts) > 1

@dataclass(frozen=True)
class SearchQuery:
    raw: str
    mode: str
    phrases: Tuple[Phrase, ...]
    free_text: str
    free_text_lemmatized: str

    dropped_unpaired_quote: bool = False

    @property
    def has_phrases(self) -> bool:
        return bool(self.phrases)

    @property
    def lemma_searchable(self) -> bool:
        return all(p.parts_lemmatized for p in self.phrases)

    def describe(self) -> str:
        parts = [f"mode={self.mode}"]
        if self.phrases:
            rendered = ' + '.join(' … '.join(p.parts) for p in self.phrases)
            parts.append(f"phrases=[{rendered}]")
        if self.free_text:
            parts.append(f"free='{self.free_text}'")
        if self.dropped_unpaired_quote:
            parts.append("unpaired_quote=dropped")
        return ' '.join(parts)

def _normalize_quotes(query: str) -> Tuple[str, bool]:
    normalized = query.translate(_QUOTE_TRANSLATION)

    dropped = False
    if normalized.count('"') % 2 == 1:
        cut = normalized.rfind('"')
        normalized = normalized[:cut] + normalized[cut + 1:]
        dropped = True

    return re.sub(r'\s+', ' ', normalized).strip(), dropped

def _build_phrase(raw: str) -> Optional[Phrase]:
    parts = tuple(p.strip() for p in _GAP_RE.split(raw) if p.strip())
    if not parts:
        return None

    lemmatized = tuple(lemmatize(p) for p in parts)

    if not all(lemmatized):
        lemmatized = ()

    return Phrase(raw=raw.strip(), parts=parts, parts_lemmatized=lemmatized)

def parse_search_query(query: str) -> SearchQuery:
    raw = (query or '').strip()
    if not raw:
        return SearchQuery(
            raw='', mode=MODE_EMPTY, phrases=(), free_text='', free_text_lemmatized='',
        )

    normalized, dropped = _normalize_quotes(raw)

    phrases = tuple(
        phrase for phrase in (_build_phrase(m) for m in _PHRASE_RE.findall(normalized))
        if phrase is not None
    )

    free_text = re.sub(r'\s+', ' ', _PHRASE_RE.sub(' ', normalized)).strip()

    if not phrases:
        free_text = free_text or normalized.replace('"', ' ').strip()
        mode = MODE_DENSE if free_text else MODE_EMPTY
    elif not free_text:
        mode = MODE_PHRASE
    else:
        mode = MODE_CASCADE

    return SearchQuery(
        raw=raw,
        mode=mode,
        phrases=phrases,
        free_text=free_text,
        free_text_lemmatized=lemmatize(free_text) if free_text else '',
        dropped_unpaired_quote=dropped,
    )

@dataclass(frozen=True)
class QueryVariants:
    dense: str
    free_text_lemmatized: str

def build_query_variants(query: str) -> QueryVariants:
    query = (query or '').strip()
    return QueryVariants(dense=query, free_text_lemmatized=lemmatize(query))

def phrases_in(query: str) -> List[str]:
    return [p.raw for p in parse_search_query(query).phrases]
