# quadridigest/format/render.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

ELLIPSIS = "…"
URL_RE = re.compile(r'https?://[^\s<>"\']+')

def _first_url_from_text(*chunks: str | None) -> str | None:
    for ch in chunks:
        if not ch:
            continue
        m = URL_RE.search(ch)
        if m:
            return m.group(0)
    return None

def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc or None
    except Exception:
        return None

def _literal_newlines(s: Optional[str]) -> str:
    if not s:
        return ""
    return str(s).replace("\\n", "\n").strip()

def _normalize(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def html_escape(s: Optional[str]) -> str:
    if not s:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

def smart_truncate(text: Optional[str], max_len: int) -> str:
    if not text:
        return ""
    s = _literal_newlines(text)
    if len(s) <= max_len:
        return s
    cut = s[: max(0, max_len - 1)]
    m = re.search(r"[\s.,;:!\-]\S*$", cut)
    if m:
        cut = cut[: m.start()]
    cut = cut.rstrip(" .,:;!-")
    return (cut or s[: max_len - 1]).rstrip() + ELLIPSIS

def split_excerpt_tail(text: str, max_len: int) -> tuple[str, str]:
    s = _literal_newlines(text or "")
    if max_len <= 0 or len(s) <= max_len:
        return (s, "")
    cut = s[: max(0, max_len - 1)]
    m = re.search(r"[\s.,;:!\-]\S*$", cut)
    if m:
        cut = cut[: m.start()]
    cut = cut.rstrip(" .,:;!-")
    excerpt = (cut or s[: max_len - 1]).rstrip() + ELLIPSIS
    tail = s[len(cut):].lstrip() if cut else s[max_len - 1:].lstrip()
    return (excerpt, tail)

def soft_cap(text: str, limit: Optional[int]) -> str:
    if not limit or limit <= 0 or len(text) <= limit:
        return text
    cut = text[: max(0, limit - 1)]
    m = re.search(r"[\s.,;:!\-]\S*$", cut)
    if m:
        cut = cut[: m.start()]
    cut = cut.rstrip(" .,:;!-")
    return (cut or text[: limit - 1]).rstrip() + ELLIPSIS

_DEFAULT_TEMPLATES: Dict[str, Dict[str, object]] = {
    "short": {
        "body": "<b>{headline}</b>\n\n{summary_excerpt}{summary_spoiler}\n\n{source_block_emoji}\n\n{hashtags}",
        "link_preview": False,
    },
    "full": {
        "body": "<b>{headline}</b>\n\n{summary}\n\n{source_block_emoji}\n\n{hashtags}",
        "link_preview": False,
    },
}

def load_templates(path: Optional[str]) -> Dict[str, Dict[str, object]]:
    def _defaults() -> Dict[str, Dict[str, object]]:
        return {k: dict(v) for k, v in _DEFAULT_TEMPLATES.items()}
    if not path:
        return _defaults()
    p = str(path)
    if not os.path.exists(p):
        return _defaults()
    try:
        if yaml is None:
            return _defaults()
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        out = _defaults()
        for section in ("short", "full"):
            sec = data.get(section) or {}
            if not isinstance(sec, dict):
                continue
            body = sec.get("body") or sec.get("template")
            if isinstance(body, str):
                out[section]["body"] = body.replace("\\n", "\n")
            if "link_preview" in sec:
                out[section]["link_preview"] = bool(sec["link_preview"])
        return out
    except Exception:
        return _defaults()

_TOPIC_HASHTAGS: Dict[str, List[str]] = {
    "politics": ["#politics", "#новости"],
    "trading":  ["#trading", "#рынки"],
    "pf":       ["#личные_финансы", "#бюджет"],
    "infosec":  ["#infosec", "#ИБ"],
}

def build_hashtags(topic: Optional[str], extra: Optional[List[str]] = None) -> str:
    tags = list(_TOPIC_HASHTAGS.get((topic or "").lower(), ["#news"]))
    tags.append("#QuadriDigest")
    if extra:
        for t in extra:
            t = str(t).strip()
            if not t:
                continue
            if not t.startswith("#"):
                t = "#" + t
            tags.append(t)
    seen = set()
    ordered: List[str] = []
    for t in tags:
        if t in seen:
            continue
        seen.add(t)
        ordered.append(t)
    return " ".join(ordered)

def build_source_block(source: Optional[str], url: Optional[str]) -> str:
    parts: List[str] = []
    if source and str(source).strip():
        parts.append(f"<i>Источник: {html_escape(source.strip())}</i>")
    if url and str(url).strip():
        parts.append(f'<a href="{html_escape(url.strip())}">Подробнее</a>')
    return "\n".join(parts)

@dataclass
class RenderInput:
    topic: Optional[str]
    headline: Optional[str]
    summary: Optional[str]
    source: Optional[str] = None
    link: Optional[str] = None
    url: Optional[str] = None
    hashtags: Optional[List[str]] = None
    variant: str = "short"
    link_preview: Optional[bool] = None

@dataclass
class RenderResult:
    text: str
    link_preview: bool = False
    parse_mode: str = "html"

def render_message(payload: RenderInput, templates_path: Optional[str] = None) -> RenderResult:
    link = payload.link or payload.url
    if not link:
        link = _first_url_from_text(payload.headline, payload.summary) or ""

    headline = smart_truncate(payload.headline or "", 120)

    excerpt_len = int(os.getenv("QD_EXCERPT_LEN", "320") or "320")
    full_summary = _literal_newlines(payload.summary or "")
    excerpt, tail = split_excerpt_tail(full_summary, excerpt_len)

    soft_max = os.getenv("QD_SPOILER_SOFT_MAX")
    tail = soft_cap(tail, int(soft_max) if (soft_max and soft_max.isdigit()) else None)

    if not headline and excerpt:
        headline = smart_truncate(excerpt, 120)
    if not excerpt and headline:
        excerpt = headline

    source = payload.source or _domain_from_url(link)

    summary_excerpt = html_escape(excerpt)
    summary_spoiler = f"\n\n<tg-spoiler>{html_escape(tail)}</tg-spoiler>" if tail else ""
    summary_bc = html_escape(full_summary if not tail else excerpt)

    src_block = build_source_block(source, link)
    src_block_emoji = f"🔗 {src_block}" if src_block else ""

    ctx = {
        "headline": html_escape(headline),
        "summary_excerpt": summary_excerpt,
        "summary_spoiler": summary_spoiler,
        "summary": summary_bc,
        "source_block": src_block,
        "source_block_emoji": src_block_emoji,
        "hashtags": build_hashtags(payload.topic, payload.hashtags),
        "source": html_escape((source or "").strip() if source else ""),
        "link": html_escape(link),
    }

    if not templates_path:
        here = os.path.dirname(os.path.abspath(__file__))
        templates_path = os.path.join(here, "templates.yaml")
    tpl = load_templates(templates_path)

    section = tpl.get(payload.variant) or _DEFAULT_TEMPLATES[payload.variant]
    body = section.get("body") or _DEFAULT_TEMPLATES[payload.variant]["body"]
    text = _normalize(str(body).format(**ctx))
    link_preview = bool(section.get("link_preview", False)) if payload.link_preview is None else bool(payload.link_preview)
    return RenderResult(text=text, link_preview=link_preview, parse_mode="html")

class TemplateRenderer:
    def __init__(self, templates_path: Optional[str] = None) -> None:
        self.templates_path = templates_path

    def render_short_result(self, **kwargs: Any) -> RenderResult:
        kwargs.setdefault("variant", "short")
        if "link" not in kwargs and "url" in kwargs:
            kwargs["link"] = kwargs["url"]
        if "summary" not in kwargs and "full_text" in kwargs:
            kwargs["summary"] = kwargs["full_text"]
        return render_message(RenderInput(**kwargs), self.templates_path)  # type: ignore[arg-type]

    def render_full_result(self, **kwargs: Any) -> RenderResult:
        kwargs.setdefault("variant", "full")
        if "link" not in kwargs and "url" in kwargs:
            kwargs["link"] = kwargs["url"]
        if "summary" not in kwargs and "full_text" in kwargs:
            kwargs["summary"] = kwargs["full_text"]
        return render_message(RenderInput(**kwargs), self.templates_path)  # type: ignore[arg-type]

    def render_short(self, **kwargs: Any) -> str:
        return self.render_short_result(**kwargs).text

    def render_full(self, **kwargs: Any) -> str:
        return self.render_full_result(**kwargs).text
