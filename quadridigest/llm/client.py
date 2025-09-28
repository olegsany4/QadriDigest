# quadridigest/llm/client.py
from __future__ import annotations

import hashlib
import json
import re as _re

def _qd_compact(s: str) -> str:
    if s is None:
        return ''
    s = str(s)
    s = s.replace('\u200b','').replace('\xa0',' ')
    s = _re.sub(r'[ \t\x0b\x0c\r]+', ' ', s)
    s = _re.sub(r'\n{2,}', '\n', s)
    return s.strip()
import logging
import os
import time
from enum import Enum
from typing import Any, Dict, Optional, Tuple, Union, Literal

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("QuadriDigest.LLM")

# --- Strict topic space ---
TopicLiteral = Literal["politics", "trading", "pf", "infosec"]

# ---------------- Topic normalization for routing (Stage 6) ----------------
TOPIC_ALLOW: set[str] = {"politics", "trading", "pf", "infosec"}

TOPIC_SYNONYMS: dict[str, str] = {
    "политика": "politics",
    "полит": "politics",
    "news": "politics",
    "economy": "trading",
    "markets": "trading",
    "market": "trading",
    "stocks": "trading",
    "трейдинг": "trading",
    "финансы": "pf",
    "личные финансы": "pf",
    "personal_finance": "pf",
    "personal-finance": "pf",
    "security": "infosec",
    "cyber": "infosec",
    "cybersec": "infosec",
    "кибербезопасность": "infosec",
}

SOURCE_HINTS: dict[str, str] = {
    "bcs_express": "trading",
    "rbc_quotes": "trading",
    "investfundsru": "pf",
    "banki_ru": "pf",
    "thebell_io": "politics",
    "meduzaio": "politics",
    "russia_news": "politics",
    "vazhnye_istorii": "politics",
}

def normalize_topic(raw: str | None, source: str | None = None) -> str:
    """
    Map any noisy topic into one of the allowed topics.
    - Prefer exact allow-list
    - Try separators (':', '/', '|', '\\')
    - Apply synonyms and keyword heuristics
    - Fallback by source hints
    """
    if not raw:
        return SOURCE_HINTS.get((source or "").lower(), "politics")
    t = str(raw).strip().lower()
    if t in TOPIC_ALLOW:
        return t
    for sep in (":", "|", "/", "\\"):
        if sep in t:
            cand = t.split(sep, 1)[0].strip()
            if cand in TOPIC_ALLOW:
                return cand
    if t in TOPIC_SYNONYMS:
        return TOPIC_SYNONYMS[t]
    if any(k in t for k in ("infosec", "cyber", "security", "кибер")):
        return "infosec"
    if any(k in t for k in ("pf", "personal", "личн", "финанс")):
        return "pf"
    if any(k in t for k in ("trad", "market", "stocks", "бирж")):
        return "trading"
    if any(k in t for k in ("pol", "полит", "gov", "власть")):
        return "politics"
    return SOURCE_HINTS.get((source or "").lower(), "politics")
# ---------------------------------------------------------------------------



class Topic(str, Enum):
    politics = "politics"
    trading = "trading"
    pf = "pf"
    infosec = "infosec"


def normalize_topic(value: Optional[str]) -> TopicLiteral:
    """
    Normalize any model-provided topic to one of 4 buckets.
    Includes synonyms + simple keyword heuristics.
    Default fallback: 'politics'.
    """
    if not value:
        return "politics"
    t = str(value).strip().lower()

    direct = {
        # politics & synonyms
        "politics": "politics",
        "policy": "politics",
        "geopolitics": "politics",
        "war": "politics",
        "elections": "politics",
        "ukraine": "politics",
        "kremlin": "politics",
        "government": "politics",
        "economy-policy": "politics",
        # trading & markets
        "trading": "trading",
        "markets": "trading",
        "market": "trading",
        "stocks": "trading",
        "bonds": "trading",
        "fx": "trading",
        "crypto": "trading",
        "commodities": "trading",
        "oil": "trading",
        "gas": "trading",
        "rates": "trading",
        # personal finance (pf)
        "pf": "pf",
        "personal": "pf",
        "personal finance": "pf",
        "retail finance": "pf",
        "banking": "pf",
        "loans": "pf",
        "loan": "pf",
        "mortgage": "pf",
        "deposits": "pf",
        "cards": "pf",
        # infosec
        "infosec": "infosec",
        "security": "infosec",
        "cybersecurity": "infosec",
        "cyber": "infosec",
        "kii": "infosec",
        "ics": "infosec",
        "ot": "infosec",
        "fstec": "infosec",
        "fsb": "infosec",
        "vulnerabilities": "infosec",
        "vulnerability": "infosec",
        "cve": "infosec",
        "soc": "infosec",
    }
    if t in direct:
        return direct[t]  # type: ignore[return-value]

    # keyword heuristics
    trading_kw = ("рынок", "бирж", "акци", "облигац", "доходност", "ставк", "брент", "wti", "s&p", "nasdaq", "рубл", "доллар", "валют", "ртс", "ммоex", "офз")
    pf_kw = ("кредит", "ипотек", "вклад", "кэшбэк", "карта", "льгот", "страховк", "личн", "бюджет", "семейн", "карты", "дебет", "кредитк")
    infosec_kw = ("уязвим", "cve", "хак", "взлом", "фишинг", "soc", "соиб", "кии", "асу тп", "фстэк", "иб", "шифр", "tls", "vpn", "apt", "эксплойт")
    politics_kw = ("правитель", "санкц", "выбор", "парламент", "президент", "мид", "геопол", "конфликт", "армия", "мобилиз")

    s = t
    def has_any(s: str, kws) -> bool:
        return any(k in s for k in kws)

    if has_any(s, trading_kw):
        return "trading"
    if has_any(s, pf_kw):
        return "pf"
    if has_any(s, infosec_kw):
        return "infosec"
    if has_any(s, politics_kw):
        return "politics"

    return "politics"


# --- Pydantic models ---

class LLMAnnotation(BaseModel):
    topic: TopicLiteral
    headline: str = Field(..., min_length=3, max_length=200)
    summary: str = Field(..., min_length=10, max_length=1000)
    fingerprint: str = Field(..., min_length=8, max_length=128)

class FullText(BaseModel):
    full_text: str = Field(..., min_length=10)


# --- Simple in-memory cache ---

class SimpleLLMCache:
    def __init__(self, ttl_sec: Optional[int] = None):
        self.ttl = int(ttl_sec) if ttl_sec else None
        self._store: Dict[str, Tuple[float, Any]] = {}

    def _expired(self, ts: float) -> bool:
        return self.ttl is not None and (time.time() - ts) > self.ttl

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if not item:
            return None
        ts, val = item
        if self._expired(ts):
            self._store.pop(key, None)
            return None
        return val

    def set(self, key: str, val: Any) -> None:
        self._store[key] = (time.time(), val)


def _hash_key(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def _extract_text(clean: Any) -> str:
    if isinstance(clean, str):
        return clean
    for attr in ("text", "clean", "content", "body", "full_text", "raw"):
        if hasattr(clean, attr):
            v = getattr(clean, attr)
            if isinstance(v, str) and v.strip():
                return v
    try:
        return json.dumps(clean, ensure_ascii=False)
    except Exception:
        return str(clean)


# --- LLM Client ---

class LLMClient:
    """
    Backward-compatible constructor:
      LLMClient(data_dir=None, prompt_version=None, provider=None, model=None, enabled=None, cache_ttl_sec=None, **kwargs)

    Unknown kwargs are ignored to avoid breaking callers.
    """
    def __init__(
        self,
        data_dir: Optional[str] = None,
        prompt_version: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        enabled: Optional[bool] = None,
        cache_ttl_sec: Optional[int] = None,
        **kwargs: Any,
    ):
        # Store optional meta for compatibility (may be used by callers)
        self.data_dir = data_dir
        self.prompt_version = prompt_version

        self.provider = (provider or os.getenv("LLM_PROVIDER", "ollama")).lower()
        self.model = model or os.getenv("LLM_MODEL", "llama3.1:8b")
        if enabled is None:
            enabled = os.getenv("LLM_ENABLED", "true").lower() == "true"
        self.enabled = bool(enabled)

        if cache_ttl_sec is None:
            env_ttl = os.getenv("LLM_CACHE_TTL_SEC", "") or "0"
            try:
                cache_ttl_sec = int(env_ttl)
            except ValueError:
                cache_ttl_sec = 0

        self.cache = SimpleLLMCache(ttl_sec=cache_ttl_sec if cache_ttl_sec and cache_ttl_sec > 0 else None)

        # Lazy, avoid hard SDK deps
        self._openai = None
        self._gemini = None

    # --- Public API ---
    def summarize(self, clean: Any) -> LLMAnnotation:
        text = _extract_text(clean)
        key = f"sum:{self.provider}:{self.model}:{_hash_key(text)}"
        cached = self.cache.get(key)
        if cached:
            return cached

        raw = self._call_llm_for_summary(text)
        data = self._validate_summary(raw, fallback_text=text)
        self.cache.set(key, data)
        return data

    def full(self, clean: Any, evidence: Optional[str] = None) -> FullText:
        text = _extract_text(clean)
        key = f"full:{self.provider}:{self.model}:{_hash_key(text)}"
        cached = self.cache.get(key)
        if cached:
            return cached

        raw = self._call_llm_for_full(text, evidence=evidence)
        try:
            data = FullText.model_validate_json(raw) if isinstance(raw, (str, bytes)) else FullText(**raw)
        except ValidationError:
            data = FullText(full_text=text)
        self.cache.set(key, data)
        return data

    def classify(self, text: str) -> TopicLiteral:
        return normalize_topic(text)

    # --- Validation & fallbacks ---
    def _validate_summary(self, raw: Union[str, Dict[str, Any]], fallback_text: str) -> LLMAnnotation:
        def _coerce(obj: Dict[str, Any]) -> Dict[str, Any]:
            obj = dict(obj)
            obj["topic"] = normalize_topic(obj.get("topic"))
            fp = (obj.get("fingerprint") or "").strip().lower()
            if not fp or len(fp) < 8:
                fp = hashlib.blake2b(fallback_text.encode("utf-8"), digest_size=16).hexdigest()
            obj["fingerprint"] = fp
            return obj

        try:
            if isinstance(raw, (str, bytes)):
                obj = json.loads(raw)
            else:
                obj = raw
            obj = _coerce(obj)
            return LLMAnnotation(**obj)
        except Exception as e:
            logger.warning("LLM summary invalid, retrying once: %s", e)
            try:
                raw2 = self._call_llm_for_summary(fallback_text, retry=True)
                obj2 = json.loads(raw2) if isinstance(raw2, (str, bytes)) else raw2
                obj2 = _coerce(obj2)
                return LLMAnnotation(**obj2)
            except Exception as e2:
                logger.error("LLM summary failed, fallback to heuristic: %s", e2)
                topic=normalize_topic(normalize_topic(fallback_text))
                headline = fallback_text.strip().split("\n", 1)[0][:180]
                summary = fallback_text.strip()[:800]
                fp = hashlib.blake2b(fallback_text.encode("utf-8"), digest_size=16).hexdigest()
                return LLMAnnotation(topic=topic, headline=headline, summary=summary, fingerprint=fp)

    # --- Prompts ---
    def _build_summary_prompt(self, text: str) -> str:
        return (
            "You are a news annotator. Respond with STRICT JSON only, no prose.\n"
            "Schema: {\"topic\": one of [politics,trading,pf,infosec], \"headline\": str, \"summary\": str, \"fingerprint\": hex string}.\n"
            "Rules: topic must be exactly politics|trading|pf|infosec. fingerprint should be stable hex.\n\n"
            f"TEXT:\n{text}\n\nOUTPUT JSON:"
        )

    def _build_full_prompt(self, text: str, evidence: Optional[str]) -> str:
        ev = f"\nEVIDENCE:\n{evidence}" if evidence else ""
        return (
            "You expand a short news into a readable paragraph. Respond STRICT JSON only, no prose.\n"
            "Schema: {\"full_text\": str}.\n"
            f"SHORT:\n{text}{ev}\n\nOUTPUT JSON:"
        )

    # --- Provider shims ---
    def _call_llm_for_summary(self, text: str, retry: bool = False) -> Union[str, Dict[str, Any]]:
        if not self.enabled:
            topic = normalize_topic(text)
            return {
                "topic": topic,
                "headline": text.strip().split("\n", 1)[0][:180],
                "summary": text.strip()[:800],
                "fingerprint": hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest(),
            }
        prompt = self._build_summary_prompt(text)
        return self._invoke_provider(prompt, expect_json=True)

    def _call_llm_for_full(self, text: str, evidence: Optional[str] = None) -> Union[str, Dict[str, Any]]:
        if not self.enabled:
            return {"full_text": text}
        prompt = self._build_full_prompt(text, evidence)
        return self._invoke_provider(prompt, expect_json=True)

    def _invoke_provider(self, prompt: str, expect_json: bool) -> Union[str, Dict[str, Any]]:
        provider = self.provider
        if provider == "openai":
            return self._invoke_openai(prompt, expect_json)
        if provider == "gemini":
            return self._invoke_gemini(prompt, expect_json)
        return self._invoke_ollama(prompt, expect_json)

    def _invoke_openai(self, prompt: str, expect_json: bool) -> Union[str, Dict[str, Any]]:
        try:
            import openai  # type: ignore
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"} if expect_json else None,
            )
            out = resp.choices[0].message.content or "{}"
            return out
        except Exception as e:
            logger.warning("OpenAI fallback due to error: %s", e)
            return {"full_text": prompt} if not expect_json else "{}"

    def _invoke_gemini(self, prompt: str, expect_json: bool) -> Union[str, Dict[str, Any]]:
        try:
            import google.generativeai as genai  # type: ignore
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel(self.model)
            resp = model.generate_content(prompt)
            out = resp.text or "{}"
            return out
        except Exception as e:
            logger.warning("Gemini fallback due to error: %s", e)
            return {"full_text": prompt} if not expect_json else "{}"

    def _invoke_ollama(self, prompt: str, expect_json: bool) -> Union[str, Dict[str, Any]]:
        import json as _json
        import urllib.request
        import urllib.error

        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        url = f"{base}/api/chat"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.1},
        }
        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                obj = _json.loads(raw.decode("utf-8"))
                # Ollama formats vary; try both
                msg = obj.get("message", {}).get("content") or obj.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                return msg
        except Exception as e:
            logger.warning("Ollama fallback due to error: %s", e)
            return {"full_text": prompt} if not expect_json else "{}"


def _validate_summary(self, raw, fallback_text):
    """
    Post-process model output so that headline>=3 and summary>=10 BEFORE pydantic validation.
    """
    import json as _json
    import hashlib as _hash

    def _coerce(obj, fb):
        d = dict(obj or {})
        # normalize topic if helper exists
        try:
            d["topic"] = normalize_topic(d.get("topic"))
        except Exception:
            d["topic"] = "politics"
        # compact and enforce min-lens
        fb_c = _qd_compact(fb)
        head = _qd_compact(d.get("headline", ""))
        summ = _qd_compact(d.get("summary", ""))
        if len(head) < 3:
            head = (fb_c.split("\n",1)[0] or fb_c or "News")[:180]
        if len(summ) < 10:
            summ = (fb_c if len(fb_c) >= 10 else (head + " — " + fb_c))[:800]
        # fingerprint fallback
        fp = _qd_compact(d.get("fingerprint", ""))
        if not fp or len(fp) < 8:
            fp = _hash.blake2b(fb_c.encode("utf-8"), digest_size=16).hexdigest()
        d.update({"headline": head, "summary": summ, "fingerprint": fp})
        return d

    try:
        obj = _json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        obj = _coerce(obj, fallback_text)
        return LLMAnnotation(**obj)
    except Exception as e:
        logger.warning("LLM summary invalid, retrying once: %s", e)
        try:
            raw2 = self._call_llm_for_summary(fallback_text, retry=True)
            obj2 = _json.loads(raw2) if isinstance(raw2, (str, bytes)) else raw2
            obj2 = _coerce(obj2, fallback_text)
            return LLMAnnotation(**obj2)
        except Exception as e2:
            logger.error("LLM summary failed, fallback to heuristic: %s", e2)
            fb_c = _qd_compact(fallback_text)
            head = (fb_c.split("\n",1)[0] or "News")[:180]
            if len(head) < 3: head = (head + " ...")[:180]

            summ = (fb_c if len(fb_c) >= 10 else (head + " — " + fb_c + " ..."))[:800]

            fp = _hash.blake2b(fb_c.encode('utf-8'), digest_size=16).hexdigest()

            return LLMAnnotation(topic='politics', headline=head, summary=summ, fingerprint=fp)
