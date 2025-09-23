# -*- coding: utf-8 -*-
# quadridigest/digest.py
"""
Сбор и подготовка постов: LLM-аннотации, антидубли, только короткий пост + ссылка.
Этот модуль не публикует сообщения сам — он возвращает подготовленные тексты.
Совместим с существующими вспомогательными модулями проекта.

Ключевые возможности:
- Аннотация новостных пунктов через LLMClient.annotate_news (фолбэк без LLM);
- Жёсткая дедупликация между прогонами (dedupe.make_hash/seen/mark);
- Рендер строго «одна строка + [читать далее](url)», без спойлеров и reply;
- Опциональная сортировка по score/приоритетам (если подключён scoring.py).

Ожидаемый формат item:
{
    "source": "rbc_news",
    "text": "Полный текст поста...",
    "title": "Если есть отдельный заголовок",
    "url": "https://t.me/rbc_news/12345",
    "category_hint": "trading"  # или politics/infosec/personal
}
"""
from __future__ import annotations

from typing import List, Dict, Sequence, Tuple, Optional

# Локальные зависимости
from .llm_client import LLMClient
from .renderer import render_short_line, normalize_url, safe_md
from .dedupe import make_hash, seen, mark

# Опционально подключаем скоринг: если файла нет — просто игнорируем.
try:
    from .scoring import score_item  # type: ignore
except Exception:  # pragma: no cover
    def score_item(_: Dict) -> float:
        return 0.5


async def annotate_items_llm(items: List[Dict]) -> List[Dict]:
    """
    Аннотируем новости LLM'ом. Если LLM выключен — делаем безопасный фолбэк.
    Возврат: список объектов с полями: short, hashtags(list), category, score.
    """
    llm = LLMClient()
    if not llm.enabled:
        # Фолбэк: короткая строка = усечённый текст/тайтл до 140 символов.
        out = []
        for i in items:
            raw = (i.get("text") or i.get("title") or "").replace("\\n", " ").strip()
            short = raw[:140]
            out.append({"short": short, "hashtags": [], "category": i.get("category_hint") or "", "score": 0.5})
        return out

    # Нормализуем вход для LLM (не отправляем лишние поля)
    slim = []
    for it in items:
        slim.append({
            "source": it.get("source") or "",
            "text": (it.get("text") or it.get("title") or "")[:2000],
            "url": normalize_url(it) or "",
            "category_hint": it.get("category_hint") or ""
        })
    return await llm.annotate_news(slim)


def _dedupe_key(it: Dict) -> str:
    """Строгий ключ для дедупликации: source + основной текст + url (если есть)."""
    basis = (it.get("text") or it.get("title") or "")
    return make_hash(it.get("source", ""), basis, it.get("url", "") or "")


def _apply_local_score(it: Dict, annot: Dict) -> float:
    """Комбинируем локальный скоринг и скор LLM (если есть)."""
    base = 0.6 * score_item(it)
    llm_s = float(annot.get("score", 0.5))
    return max(0.0, min(1.0, 0.4 * llm_s + base))


async def build_posts_short_only(items: List[Dict]) -> List[Tuple[Dict, str]]:
    """
    Готовим окончательные тексты постов. На выходе список кортежей: (item, text).
    - Дедупликация между прогонами;
    - Строго одна короткая строка + ссылка «читать далее» (если url есть);
    - Сортировка по приоритету (score).
    """
    if not items:
        return []

    # Аннотация
    ann = await annotate_items_llm(items)

    # Сбор с антидублем
    buf: List[Tuple[float, Dict, str]] = []
    for it, a in zip(items, ann):
        key = _dedupe_key(it)
        if seen(key):
            continue

        text = render_short_line(a, it, add_link=True)
        final = safe_md(text)

        score = _apply_local_score(it, a)
        buf.append((score, it, final))
        mark(key)

    # Сортируем по убыванию важности (score)
    buf.sort(key=lambda x: x[0], reverse=True)

    # Возвращаем пары (item, text)
    return [(it, txt) for _, it, txt in buf]


def group_by_category(items: List[Dict]) -> Dict[str, List[Dict]]:
    """
    На случай, если нужна перекатегоризация после аннотаций.
    Сейчас упрощённо: используем hint, если есть, иначе 'trading'.
    """
    out: Dict[str, List[Dict]] = {"politics": [], "trading": [], "infosec": [], "personal": []}
    for it in items:
        cat = (it.get("category_hint") or "").strip().lower()
        if cat not in out:
            cat = "trading"
        out[cat].append(it)
    return out
