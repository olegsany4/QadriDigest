
from typing import Dict
from quadridigest.types import Topic
from quadridigest.dedupe.simhash import simhash_fingerprint

class ClusterService:
    def __init__(self):
        # Простейший in-memory индекс; замени на БД/Repository
        self._index: Dict[str, Topic] = {}

    def fingerprint(self, text: str) -> str:
        return simhash_fingerprint(text)

    def find_or_create(self, fp: str, topic: Topic) -> str:
        if fp not in self._index:
            self._index[fp] = topic
        return fp

    def is_duplicate(self, fp: str) -> bool:
        return fp in self._index
