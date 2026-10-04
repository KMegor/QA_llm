import re
from functools import lru_cache
from typing import List

import pymorphy3

_morph = pymorphy3.MorphAnalyzer()

_WORD_RE = re.compile(r'[0-9A-Za-zА-Яа-яЁё]+', re.UNICODE)

@lru_cache(maxsize=100_000)
def _lemma_of(word: str) -> str:
    return _morph.parse(word)[0].normal_form

def lemmatize(text: str) -> str:
    if not text:
        return ''
    words = _WORD_RE.findall(text)
    return ' '.join(_lemma_of(w) for w in words)

def lemmatize_words(text: str) -> List[str]:
    if not text:
        return []
    words = _WORD_RE.findall(text)
    return [_lemma_of(w) for w in words]
