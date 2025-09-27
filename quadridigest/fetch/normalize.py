# quadridigest/fetch/normalize.py
"""
Нормализация текста для QadriDigest.

Функции:
- clean_text(text: str) -> CleanItem:
    * удаляет сигнатуры каналов (рекламные «подписаться», t.me/@... строки в хвосте);
    * очищает «хвосты» ссылок (utm_*, fbclid, gclid, yclid, mc_cid, mc_eid, ref и пр.);
    * нормализует кавычки и пробелы;
    * (опционально) определяет язык и записывает в CleanItem.lang ('ru', 'en', 'und').

Модуль не требует внешних зависимостей.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import re


# --- Data model ----------------------------------------------------------------

@dataclass
class CleanItem:
    text: str
    lang: str = "und"  # 'ru' | 'en' | 'und'


# --- Public API ----------------------------------------------------------------

def clean_text(raw: str) -> CleanItem:
    """
    Основная функция чистки текста.
    """
    if raw is None:
        return CleanItem(text="", lang="und")

    text = raw

    # 1) Разбиваем на строки для детекции «подписных» хвостов каналов
    lines = [l.strip() for l in text.splitlines()]

    # Удаляем хвостовые строки с промо/сигнатурами каналов.
    # Эвристика: строка содержит t.me или @channel и при этом одно из слов: подпис, subscribe, канал, источник, source.
    promo_pattern = re.compile(r'(?i)\b(подпис|subscribe|канал|источник|source)\b')
    def is_promo_line(line: str) -> bool:
        return (('t.me/' in line or re.search(r'@\w{3,}', line))
                and bool(promo_pattern.search(line)))

    # Также часто встречается «Подробнее: <url>» в конце — можно оставить URL, но без UTM.
    more_pattern = re.compile(r'(?i)^\s*(подробнее|читать полностью|more)\s*[:\-–]\s*(?P<url>https?://\S+)\s*$')

    cleaned_lines = []
    for i, line in enumerate(lines):
        # Пропустим пустые дубляжи
        if not line:
            cleaned_lines.append(line)
            continue

        # Если это промо-хвост — выкидываем
        if is_promo_line(line):
            continue

        m = more_pattern.match(line)
        if m:
            # Сохраним строку как "Подробнее: <clean_url>"
            url = _strip_tracking_from_url(m.group('url'))
            cleaned_lines.append(f"Подробнее: {url}")
            continue

        # Очистим UTM у URL внутри строки
        line = _clean_urls_in_text(line)
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # 2) Удаляем одинокие хвостовые строки с голыми ссылками на t.me/@...,
    #    если предыдущая строка уже содержит полную новость.
    text = _drop_trailing_channel_plugs(text)

    # 3) Нормализация кавычек и тире/пробелов
    text = _normalize_quotes(text)
    text = _normalize_whitespace(text)

    # 4) Базовое определение языка
    lang = _detect_lang(text)

    return CleanItem(text=text, lang=lang)


# --- Helpers -------------------------------------------------------------------

TRACKING_PARAMS = {
    # общие
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'utm_referrer', 'ref', 'ref_src', 'ref_url',
    # рекламные метки
    'fbclid', 'gclid', 'yclid', 'mc_cid', 'mc_eid', 'igshid', 'ttclid',
    # маркетинг
    'pk_campaign', 'pk_source', 'pk_medium',
}

URL_RE = re.compile(r'(https?://[^\s)]+)')


def _protect_urls(s: str):
    """Replace URLs with placeholders to avoid punctuation/space normalization inside them."""
    urls = []
    def repl(m):
        urls.append(m.group(1))
        return f"__URL_PLACEHOLDER_{len(urls)-1}__"
    protected = URL_RE.sub(repl, s)
    return protected, urls

def _restore_urls(s: str, urls):
    for i, u in enumerate(urls):
        s = s.replace(f"__URL_PLACEHOLDER_{i}__", u)
    return s


def _strip_tracking_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        # Почистим query
        q = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS and not k.lower().startswith('utm_')]
        new_query = urlencode(q, doseq=True)
        # Обрезаем якоря, если это трекинг от телеги и т.п.
        new_fragment = parsed.fragment
        # Собираем назад
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, new_fragment)).rstrip('?/&')
    except Exception:
        return url

def _clean_urls_in_text(s: str) -> str:
    # Заменим каждую найденную ссылку её очищенной версией
    def repl(m: re.Match) -> str:
        return _strip_tracking_from_url(m.group(1))
    return URL_RE.sub(repl, s)

def _drop_trailing_channel_plugs(text: str) -> str:
    lines = [l.rstrip() for l in text.splitlines()]
    # Удаляем с конца 1-2 строки, если это исключительно t.me/@... без смысла
    def is_channel_ref(s: str) -> bool:
        s_stripped = s.strip()
        if not s_stripped:
            return True  # пустые хвосты
        if re.fullmatch(r'(?:https?://)?t\.me/[^\s]+', s_stripped, flags=re.I):
            return True
        if re.fullmatch(r'@\w{3,}', s_stripped):
            return True
        return False

    while lines and is_channel_ref(lines[-1]):
        lines.pop()

    # Удалим ведущие/двойные пустые
    while lines and not lines[0].strip():
        lines.pop(0)

    # Схлопнем лишние пустые строки в середине
    compact = []
    prev_empty = False
    for l in lines:
        if l.strip():
            compact.append(l)
            prev_empty = False
        else:
            if not prev_empty:
                compact.append("")
            prev_empty = True
    return "\n".join(compact).strip()

def _normalize_quotes(s: str) -> str:
    # Приводим «ёлочки» и типографские кавычки к ASCII
    _s, urls = _protect_urls(s)
    replacements = {
        '“': '"', '”': '"', '„': '"', '‟': '"', '″': '"', '‹': '"', '›': '"',
        '«': '"', '»': '"', '’': "'", '‘': "'", '‚': "'", '′': "'",
        # частые неподходящие пробелы
        '\u00A0': ' ',  # NBSP
        '\u2009': ' ',  # thin space
        '\u202F': ' ',  # narrow no-break space
    }
    for a, b in replacements.items():
        _s = _s.replace(a, b)

    # Заменим длинное тире на короткое с пробелами нормальными
    _s = _s.replace('—', '—')  # оставим длинное тире, ниже нормализуем пробелы вокруг
    # Уберём пробел перед знаками пунктуации
    _s = re.sub(r'\s+([,.:;!?])', r'\1', _s)
    # Гарантируем единственный пробел после пунктуации при необходимости
    _s = re.sub(r'([,;:!?])([^\s])', r'\1 \2', _s)
    # Пробелы вокруг тире/длинного тире
    _s = re.sub(r'\s*[-–—]\s*', ' — ', _s)
    # Двойные пробелы → один
    _s = re.sub(r'[ \t]{2,}', ' ', _s)
    # Restore URLs
    return _restore_urls(_s, urls)

def _normalize_whitespace(s: str) -> str:
    # Уберём висячие пробелы построчно
    s = "\n".join(line.strip() for line in s.splitlines())
    # Схлопнем более двух переводов строк
    s = re.sub(r'\n{3,}', '\n\n', s)
    # Схлопнем повторяющиеся пробелы/табуляции
    s = re.sub(r'[ \t]{2,}', ' ', s)
    return s.strip()

def _detect_lang(s: str) -> str:
    # Простая эвристика: >30% символов кириллицы → 'ru'; >30% латиницы и <10% кириллицы → 'en'
    # Игнорируем URL при подсчёте.
    s_wo_urls = URL_RE.sub(' ', s)
    letters = re.findall(r'[A-Za-zА-Яа-яЁё]', s_wo_urls)
    if not letters:
        return 'und'
    total = len(letters)
    cyr = sum(1 for ch in letters if 'А' <= ch <= 'я' or ch in 'Ёё')
    lat = sum(1 for ch in letters if ('A' <= ch <= 'Z') or ('a' <= ch <= 'z'))
    if cyr / total >= 0.3:
        return 'ru'
    if lat / total >= 0.3 and cyr / total <= 0.1:
        return 'en'
    return 'und'


"""
Doctest examples:

>>> from normalize import clean_text
>>> clean_text("Смотри: https://example.com?a=1&utm_source=tg").text
'Смотри: https://example.com?a=1'
>>> clean_text("Новость\nПодписывайся: t.me/abc").text
'Новость'
>>> clean_text("«Кавычки»,—пунктуация?OK!").text
'"Кавычки", — пунктуация? OK!'
>>> clean_text("12345 --- https://example.com").lang in ("und","en","ru")
True
"""


if __name__ == "__main__":
    import argparse, doctest
    ap = argparse.ArgumentParser(description="Self-test/doctest for normalize.py")
    ap.add_argument("--selftest", action="store_true", help="Run internal self-tests (doctest + smoke checks)")
    args = ap.parse_args()
    if args.selftest:
        failures, _ = doctest.testmod(optionflags=doctest.ELLIPSIS)
        from normalize import clean_text
        s = "«Привет», — сказал он. Подробнее: https://site.ru?a=1&utm_source=tg"
        item = clean_text(s)
        assert '"Привет", — сказал он. Подробнее: https://site.ru?a=1' in item.text
        print("SELFTEST OK" if failures == 0 else f"SELFTEST FAIL: {failures}")
    else:
        print("Usage: python normalize.py --selftest")
