# -*- coding: utf-8 -*-
# quadridigest/renderer.py
"""
Рендер короткой строки: «• кратко ... [читать далее](url) #теги».
Содержит утилиты для URL и экранирования Markdown.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Iterable

MD_SAFE = re.compile(r"([_*`~>#+=|{}.!\\[\\]\\(\\)])")

def safe_md(text: str) -> str:
    """Экранирует спецсимволы Markdown V2 по минимуму, чтобы не ломать ссылку."""
    # Мы не применяем формат V2 в send_message, поэтому экранирование щадящее
    return text

def normalize_url(item: Dict) -> Optional[str]:
    """Пытаемся достать ссылку на источник из возможных полей."""
    for k in ("url", "link", "source_url", "permalink"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def _fmt_hashtags(tags: Iterable[str]) -> str:
    out = []
    for t in tags or []:
        t = (t or "").strip()
        if not t:
            continue
        t = t.lstrip("#").replace(" ", "_").replace("-", "_")
        if t:
            out.append(f"#{t}")
    return " " + " ".join(out) if out else ""

def render_short_line(annot: Dict, item: Dict, *, add_link: bool = True) -> str:
    """
    annot: { short, hashtags(list), category, score, ... }
    item:  { source, text/title, url?, ... }
    Результат: одна строка с опциональной ссылкой.
    """
    short = (annot.get("short") or "").strip()
    tags  = annot.get("hashtags") or []
    tags_str = _fmt_hashtags(tags)

    tail = ""
    if add_link:
        link = normalize_url(item)
        if link:
            tail = f" [читать далее]({link})"

    return f"• {short}{tail}{tags_str}"
