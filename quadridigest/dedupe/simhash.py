# -*- coding: utf-8 -*-
"""
QadriDigest — dedupe.simhash
Этап 4) Дедуп: быстрый отпечаток и нормализация текста.

Функции:
- normalize_text: агрессивная нормализация текста (для fuzzy- и simhash-сходства)
- blake64_hex: быстрый криптографический отпечаток для предварительного отсечения
- simhash64_int / simhash64_hex: 64-битный SimHash для семантической близости
- make_fingerprint: комбинированный отпечаток "b64:s64" (для уникального индекса)

Зависимости: только stdlib. Никаких внешних библиотек.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable, Tuple

WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+", re.U)


def _strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+|t\.me/\S+|telegram\.me/\S+", " ", text, flags=re.I)


def _strip_usernames(text: str) -> str:
    return re.sub(r"@\w+", " ", text)


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(text: str) -> str:
    """
    Агрессивная нормализация:
    - NFC -> lower
    - удаление ссылок/юзернеймов
    - выкидывание всего, что не буквы/цифры (оставляем слова)
    - схлопывание пробелов
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = t.lower()
    t = _strip_urls(t)
    t = _strip_usernames(t)
    # Оставляем только слова, разделяя пробелами
    tokens = WORD_RE.findall(t)
    t = " ".join(tokens)
    t = _collapse_spaces(t)
    return t


def blake64_hex(text: str) -> str:
    """
    Быстрый криптографический отпечаток: первые 8 байт BLAKE2b (64 бита).
    Возвращаем как hex из 16 символов.
    """
    h = hashlib.blake2b(text.encode("utf-8"), digest_size=8)
    return h.hexdigest()  # 16 hex chars = 64 bits


def _features(tokens: Iterable[str]) -> Iterable[Tuple[str, int]]:
    """Фичи для SimHash: униграммы + биграммы, вес = 1."""
    toks = list(tokens)
    for t in toks:
        if t:
            yield (t, 1)
    for i in range(len(toks) - 1):
        yield (toks[i] + "_" + toks[i + 1], 1)


def _hash64(s: str) -> int:
    """64-битный hash (blake2b/8) -> int."""
    return int(hashlib.blake2b(s.encode("utf-8"), digest_size=8).hexdigest(), 16)


def simhash64_int(text: str) -> int:
    """
    Простой SimHash 64 бит на основе уни/биграмм нормализованного текста.
    """
    norm = normalize_text(text)
    if not norm:
        return 0
    tokens = norm.split()
    vec = [0] * 64
    for f, w in _features(tokens):
        h = _hash64(f)
        for i in range(64):
            bit = 1 if (h >> i) & 1 else -1
            vec[i] += (w if bit == 1 else -w)
    # Сборка финального хеша
    value = 0
    for i in range(64):
        if vec[i] >= 0:
            value |= (1 << i)
    return value


def simhash64_hex(text: str) -> str:
    return f"{simhash64_int(text):016x}"


def make_fingerprint(text: str) -> str:
    """
    Комбинированный отпечаток для уникального индекса и быстрого отсева:
    'b64:s64', где:
      - b64 = blake64_hex(normalize_text(text))
      - s64 = simhash64_hex(text)
    Такой формат стабилен и компактен.
    """
    norm = normalize_text(text)
    b = blake64_hex(norm)
    s = simhash64_hex(norm)  # simhash тоже на нормализованном
    return f"{b}:{s}"


if __name__ == "__main__":
    # Простейший прогон
    samples = [
        "⚡️Срочно: Рынок вырос на 2%! https://t.me/example",
        "Рынок вырос на два процента — срочно! t.me/abc",
        "Совсем другая новость про нефть."
    ]
    for t in samples:
        print(t, "->", normalize_text(t), make_fingerprint(t))
