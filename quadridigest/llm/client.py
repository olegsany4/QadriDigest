
from typing import List
from quadridigest.types import CleanItem, LLMAnnotation, FullText, Topic
from quadridigest.llm.cache import SimpleLLMCache
from quadridigest.utils import short_fp

class LLMClient:
    """
    Мок-клиент LLM: генерирует детерминированные поля из текста.
    В проде замени реализацией вызова внешней модели, сохранив интерфейсы.
    """
    def __init__(self, data_dir: str = "./data", prompt_version: str = "V2"):
        self.cache = SimpleLLMCache(data_dir=data_dir)
        self.prompt_version = prompt_version

    def summarize(self, clean: CleanItem) -> LLMAnnotation:
        prompt = f"SUMMARIZE::{clean.text}"
        cached = self.cache.get(prompt)
        if cached:
            return LLMAnnotation(**cached)

        # Примитивная эвристика
        topic: Topic = "trading" if ("S&P" in clean.text or "ОФЗ" in clean.text) else "politics"
        headline = clean.text.split(".")[0][:110]
        summary = clean.text[:300]
        fp = short_fp(clean.text, size=16)

        payload = {
            "version": self.prompt_version,
            "topic": topic,
            "headline": headline,
            "summary": summary,
            "fingerprint": fp,
            "evidence": [],
        }
        self.cache.set(prompt, payload)
        return LLMAnnotation(**payload)

    def full(self, clean: CleanItem, evidence: List[str] | None = None) -> FullText:
        prompt = f"FULL::{clean.text}"
        cached = self.cache.get(prompt)
        if cached:
            return FullText(**cached)

        ft = clean.text if len(clean.text) < 800 else clean.text[:790] + "…"
        payload = {"full_text": ft}
        self.cache.set(prompt, payload)
        return FullText(**payload)

    def classify(self, clean: CleanItem) -> Topic:
        return self.summarize(clean).topic
