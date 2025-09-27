
import os, json, hashlib
from typing import Optional

class SimpleLLMCache:
    def __init__(self, data_dir: str = "./data"):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "llm_cache.json")
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({}, f)
        with open(self.path, "r", encoding="utf-8") as f:
            self._db = json.load(f)

    def _key(self, prompt: str) -> str:
        return hashlib.blake2b(prompt.encode("utf-8"), digest_size=16).hexdigest()

    def get(self, prompt: str) -> Optional[dict]:
        return self._db.get(self._key(prompt))

    def set(self, prompt: str, value: dict):
        self._db[self._key(prompt)] = value
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._db, f, ensure_ascii=False, indent=2)
