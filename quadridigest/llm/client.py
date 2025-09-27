
"""
quadridigest/llm/client.py — Реальный LLM-клиент для QuadriDigest (Этап 5, финал)
Поддержка провайдеров: OpenAI, Gemini, Ollama (локальный).

СОХРАНЕННЫЕ ИНТЕРФЕЙСЫ:
  - summarize(clean) -> LLMAnnotation (строгий JSON)
  - full(clean, evidence) -> FullText

Что внутри:
- Pydantic-валидация ответа (строгий JSON), 1 ретрай при невалидном JSON.
- SimpleLLMCache по ключу входа (по хешу текста), опционально TTL (LLM_CACHE_TTL_SEC).
- Совместимость с проектом:
    * FullText ожидает поле full_text (а не body). Если пришло body — переименуем.
    * summarize может вернуть лишние поля (version/evidence) — игнорируются fallback-моделью.
    * summarize/full принимают любой объект clean (str/pydantic/dataclass/любой), текст берётся из
      популярных атрибутов (text/clean/content/body/full_text/raw) или сериализацией в JSON;
      ключ кэша строится от blake2b текста (digest_size=16).
- Управление через окружение или .env:
    PUBLISH_MODE=llm
    LLM_ENABLED=true
    LLM_PROVIDER=openai|gemini|ollama
    LLM_MODEL=... (напр. gpt-4o-mini | gemini-2.0-flash | llama3.1:8b)
    OPENAI_API_KEY=...
    GEMINI_API_KEY=...
    OLLAMA_BASE_URL=http://localhost:11434
    LLM_CACHE_TTL_SEC=3600

Back-compat: конструктор принимает data_dir и prompt_version (игнорируются, чтобы не ломать старый вызов).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .prompts import build_summarize_prompt, build_full_prompt
from .cache import SimpleLLMCache

# ===== Pydantic-модели =====
try:
    # Если в проекте есть собственные типы — используем их
    from quadridigest.types import LLMAnnotation as _LLMAnnotation, FullText as _FullText  # type: ignore
    from pydantic import BaseModel, Field, ValidationError
    class LLMAnnotation(_LLMAnnotation):  # type: ignore
        pass
    class FullText(_FullText):  # type: ignore
        pass
except Exception:
    from pydantic import BaseModel, Field, ValidationError

    class LLMAnnotation(BaseModel):
        # В проекте annotate может иметь version/evidence — игнорируем их в fallback
        topic: str = Field(..., description="policy | trading | personal_finance | infosec")
        headline: str = Field(..., description="Короткий фактологичный заголовок")
        summary: str = Field(..., description="2–4 предложения, факты и последствия")
        fingerprint: str = Field(..., pattern=r"^[a-f0-9]{8,64}$", description="Стабильный hex-отпечаток")

        model_config = {"extra": "ignore"}  # игнорировать version/evidence и т.д.

    class FullText(BaseModel):
        headline: str
        full_text: str  # проект ожидает это поле
        highlights: List[str] = []
        evidence: List[str] = []

        model_config = {"extra": "ignore"}

# ===== Конфигурация и кэш =====
log = logging.getLogger("QuadriDigest.LLM")

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return default
    return v in {"1","true","yes","on"}

CACHE_TTL = int(os.getenv("LLM_CACHE_TTL_SEC", "0") or "0")
_cache = SimpleLLMCache(default_ttl=CACHE_TTL if CACHE_TTL > 0 else None)

def _is_enabled() -> bool:
    return (os.getenv("PUBLISH_MODE", "").lower() == "llm") and _env_bool("LLM_ENABLED", False)

# ===== Провайдеры =====

class _ProviderBase:
    def __init__(self, model: Optional[str] = None):
        self.model = model

    def complete_json(self, prompt: str) -> str:
        """Вернуть СТРОГИЙ JSON (строка) от модели."""
        raise NotImplementedError

class _OpenAIProvider(_ProviderBase):
    def __init__(self, model: Optional[str] = None):
        super().__init__(model)
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for LLM_PROVIDER=openai")
        self._url = "https://api.openai.com/v1/chat/completions"

    def complete_json(self, prompt: str) -> str:
        import urllib.request, ssl  # type: ignore
        model = self.model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a careful JSON generator. Respond with JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=90) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        return parsed["choices"][0]["message"]["content"]

class _GeminiProvider(_ProviderBase):
    def __init__(self, model: Optional[str] = None):
        super().__init__(model)
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for LLM_PROVIDER=gemini")

    def complete_json(self, prompt: str) -> str:
        import urllib.request, ssl  # type: ignore
        model = (self.model or os.getenv("LLM_MODEL", "gemini-2.0-flash")).replace("gemini:", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=90) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        try:
            candidates = parsed["candidates"]
            parts = candidates[0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except Exception:
            return json.dumps(parsed, ensure_ascii=False)

class _OllamaProvider(_ProviderBase):
    def __init__(self, model: Optional[str] = None):
        super().__init__(model)
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    def complete_json(self, prompt: str) -> str:
        import urllib.request  # type: ignore
        url = f"{self.base_url}/api/generate"
        model = (self.model or os.getenv("LLM_MODEL", "llama3.1:8b")).replace("ollama:", "")
        payload = {
            "model": model,
            "prompt": "Respond with JSON only.\n" + prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=120) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        return parsed["response"]

# ===== Вспомогательные =====

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

def _extract_json(text: str) -> str:
    """Из ответа LLM достать первый JSON-объект { ... } (или бросить исключение)."""
    m = _JSON_RE.search(text.strip())
    if not m:
        raise ValueError("LLM did not return JSON")
    return m.group(0)

def _normalize_fingerprint(s: str) -> str:
    s = s.strip().lower()
    if re.fullmatch(r"[a-f0-9]{8,64}", s):
        return s
    # fallback: локальный blake2b(first 16 hex) от текста
    import hashlib
    return hashlib.blake2b(s.encode("utf-8"), digest_size=16).hexdigest()

def _coerce_text_and_cache_key(clean) -> tuple[str, str]:
    """
    Возвращает (text, stable_key_part).
    text — строка для модели; stable_key_part — короткий hex-хеш для ключа кэша.
    Поддерживает str, pydantic-модели, dataclass, и любые объекты с атрибутами text/clean/content/body/full_text/raw.
    """
    import hashlib, json as _json
    # 1) str — сразу
    if isinstance(clean, str):
        text = clean
    else:
        # 2) популярные атрибуты
        text = None
        for attr in ("text", "clean", "content", "body", "full_text", "raw"):
            if hasattr(clean, attr):
                v = getattr(clean, attr)
                if isinstance(v, str) and v.strip():
                    text = v
                    break
        # 3) сериализация, если всё ещё None
        if text is None:
            if hasattr(clean, "model_dump"):
                text = _json.dumps(clean.model_dump(), ensure_ascii=False)
            elif hasattr(clean, "dict"):
                text = _json.dumps(clean.dict(), ensure_ascii=False)
            elif hasattr(clean, "__dict__"):
                text = _json.dumps(clean.__dict__, ensure_ascii=False)
            else:
                text = str(clean)
    text = text.strip()
    fp = hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()
    return text, fp

def _validate_or_retry(make_call, build_prompt, parse_model, retry_hint: str) -> Any:
    # Один ретрай при невалидном JSON/валидации
    raw = make_call(build_prompt())
    try:
        payload = json.loads(_extract_json(raw))
        return parse_model(payload)
    except Exception as e1:
        log.warning("LLM JSON invalid, retrying once: %s", e1)
        raw2 = make_call(build_prompt(extra_instruction=retry_hint))
        payload2 = json.loads(_extract_json(raw2))
        return parse_model(payload2)

# ===== Публичный клиент =====

@dataclass
class LLMClient:
    provider: str = os.getenv("LLM_PROVIDER", "ollama").lower()
    model: Optional[str] = os.getenv("LLM_MODEL") or None
    # Back-compat: параметры, которые может передавать main.py
    data_dir: Optional[str] = None
    prompt_version: Optional[str] = None

    def __post_init__(self):
        prov = self.provider
        if prov == "openai":
            self._impl = _OpenAIProvider(self.model)
        elif prov == "gemini":
            self._impl = _GeminiProvider(self.model)
        elif prov == "ollama":
            self._impl = _OllamaProvider(self.model)
        else:
            raise RuntimeError(f"Unknown LLM_PROVIDER={prov}")

    # ---- summarize ----
    def summarize(self, clean) -> LLMAnnotation:
        if not _is_enabled():
            raise RuntimeError("LLM is disabled. Set PUBLISH_MODE=llm and LLM_ENABLED=true")

        clean_text, clean_key = _coerce_text_and_cache_key(clean)
        cache_key = ("summarize", self.provider, self.model or "", clean_key)
        cached = _cache.get(cache_key)
        if cached is not None:
            return LLMAnnotation.model_validate(cached)

        def _call(prompt: str) -> str:
            return self._impl.complete_json(prompt)

        def _build(extra_instruction: Optional[str] = None) -> str:
            return build_summarize_prompt(clean_text, extra_instruction=extra_instruction)

        def _parse(payload: Dict[str, Any]) -> LLMAnnotation:
            # Пост-обработка fingerprint
            if "fingerprint" in payload:
                payload["fingerprint"] = _normalize_fingerprint(str(payload["fingerprint"]))
            obj = LLMAnnotation.model_validate(payload)
            return obj

        obj = _validate_or_retry(_call, _build, _parse, retry_hint="Ответь ТОЛЬКО валидным JSON без пояснений.")
        _cache.set(cache_key, obj.model_dump())
        return obj

    # ---- full ----
    def full(self, clean, evidence: Optional[List[str]] = None) -> FullText:
        if not _is_enabled():
            raise RuntimeError("LLM is disabled. Set PUBLISH_MODE=llm and LLM_ENABLED=true")

        clean_text, clean_key = _coerce_text_and_cache_key(clean)
        ev_key = tuple(evidence or [])
        cache_key = ("full", self.provider, self.model or "", clean_key, ev_key)
        cached = _cache.get(cache_key)
        if cached is not None:
            return FullText.model_validate(cached)

        def _call(prompt: str) -> str:
            return self._impl.complete_json(prompt)

        def _build(extra_instruction: Optional[str] = None) -> str:
            return build_full_prompt(clean_text, evidence=evidence, extra_instruction=extra_instruction)

        def _parse(payload: Dict[str, Any]) -> FullText:
            # Совместимость: body -> full_text
            if "full_text" not in payload and "body" in payload:
                payload["full_text"] = payload.pop("body")
            obj = FullText.model_validate(payload)
            return obj

        obj = _validate_or_retry(_call, _build, _parse, retry_hint="Верни только JSON объекта без комментариев.")
        _cache.set(cache_key, obj.model_dump())
        return obj
