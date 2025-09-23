# quadridigest/llm_client.py
import os, json, time, sqlite3, httpx
from typing import List, Dict

class LLMClient:
    def __init__(self):
        self.enabled = os.getenv("LLM_ENABLED","false").lower()=="true"
        self.host = os.getenv("OLLAMA_HOST","http://127.0.0.1:11434").rstrip("/")
        self.model = os.getenv("LLM_MODEL","llama3.1:8b")
        self.temp = float(os.getenv("LLM_TEMPERATURE","0.2"))
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS","800"))
        self.cache_db = os.getenv("LLM_CACHE_DB","llm_cache.sqlite3")
        self.ttl_days = int(os.getenv("LLM_CACHE_TTL_DAYS","7"))
        self.trace_enabled = os.getenv("LLM_TRACE_ENABLED","false").lower()=="true"
        self.trace_path = os.getenv("LLM_TRACE_PATH","llm_traces.log")
        self._init_cache()

    def _init_cache(self):
        self.conn = sqlite3.connect(self.cache_db)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS cache(
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL,
            ts INTEGER NOT NULL
        )""")

    def _key(self, kind: str, payload: Dict) -> str:
        return f"{kind}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"

    def _get(self, k: str):
        row = self.conn.execute("SELECT v, ts FROM cache WHERE k=?", (k,)).fetchone()
        if not row: return None
        v, ts = row
        if (time.time() - ts) > self.ttl_days*86400:
            return None
        return json.loads(v)

    def _put(self, k: str, v: Dict):
        self.conn.execute("INSERT OR REPLACE INTO cache(k,v,ts) VALUES(?,?,?)",
                          (k, json.dumps(v, ensure_ascii=False), int(time.time())))
        self.conn.commit()

    def _trace(self, msg: str):
        if self.trace_enabled:
            with open(self.trace_path,"a",encoding="utf-8") as f:
                f.write(msg+"\n")

    async def _generate(self, prompt: str) -> str:
        if not self.enabled:
            return ""
        body = {"model": self.model, "prompt": prompt, "stream": False,
                "options": {"temperature": self.temp}}
        try:
            async with httpx.AsyncClient(timeout=60) as cli:
                r = await cli.post(f"{self.host}/api/generate", json=body)
                r.raise_for_status()
                data = r.json()
                out = data.get("response","").strip()
                self._trace(f"[GEN] {len(prompt)} -> {len(out)}")
                return out[:self.max_tokens*4]  # грубая отсечка
        except Exception as e:
            self._trace(f"[ERR] {e}")
            return ""

    # === ПУБЛИЧНЫЕ МЕТОДЫ ===

    async def annotate_news(self, items: List[Dict]) -> List[Dict]:
        """
        input: [{source, text, url, category_hint?}] 
        return: [{short, hashtags, category, score, ref_url}]
        """
        k = self._key("annotate_news", {"items":[i.get("text","")[:400] for i in items]})
        cached = self._get(k)
        if cached: return cached

        prompt = (
            "Суммируй каждый пункт одной строкой (<=140 символов), "
            "добавь 2–4 хэштега, присвой одну из категорий: Политика, Трейдинг, Инфобез, Личные финансы. "
            "Верни JSON-список объектов: short, hashtags(list), category, score(0..1).\n\n"
            f"Пункты:\n{json.dumps(items, ensure_ascii=False)}"
        )
        resp = await self._generate(prompt)
        try:
            data = json.loads(resp)
        except Exception:
            # fallback: простая обрезка
            data = [{"short": i["text"][:140].replace("\n"," "),
                     "hashtags": [], "category": i.get("category_hint") or "",
                     "score": 0.5, "ref_url": i.get("url")} for i in items]
        self._put(k, data)
        return data

    async def summarize_thread(self, long_text: str) -> str:
        k = self._key("summarize_thread", {"t": long_text[:1500]})
        cached = self._get(k)
        if cached: return cached

        prompt = ("Сделай сжатое, структурированное резюме текста с маркерами и фактурой, "
                  "без воды, 5–8 пунктов.\n\nТекст:\n"+long_text[:6000])
        resp = await self._generate(prompt)
        self._put(k, resp)
        return resp or ""
