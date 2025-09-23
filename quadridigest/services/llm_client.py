from __future__ import annotations
import json, os
from typing import List, Dict, Any, Tuple
import httpx
from datetime import datetime
from .prompts import SYSTEM_NEWS_ANALYST, USER_ANALYZE_TEMPLATE, SYSTEM_DIGEST_WRITER, USER_DIGEST_TEMPLATE
from .cache import make_key, get as cache_get, put as cache_put, DEFAULT_DB

def approx_token_count(text:str)->int: return max(1,int(len(text)/4))

class LLMClient:
    def __init__(self, cfg: dict):
        llm=cfg.get("llm",{})
        self._enabled=bool(llm.get("enabled",True))
        self.model=llm.get("model","llama3.1:8b")
        self.host=llm.get("host","http://127.0.0.1:11434").rstrip("/")
        self.temperature=float(llm.get("temperature",0.2))
        self.max_tokens=int(llm.get("max_tokens",800))
        self.publish_mode=llm.get("publish_mode","bullets")
        self.cache_db=llm.get("cache_db") or DEFAULT_DB
        self.cache_ttl_sec=int(float(llm.get("cache_ttl_days",7))*24*3600)
        self.trace_enabled=bool(llm.get("trace_enabled",True))
        self.trace_path=llm.get("trace_path") or os.path.join(os.path.dirname(__file__),"..","..","llm_traces.log")

    def health_check(self)->Tuple[bool,str]:
        try:
            with httpx.Client(timeout=5) as client:
                r=client.get(f"{self.host}/v1/models")
                if r.status_code==200:
                    models=[m.get("id") for m in r.json().get("data",[])]
                    if self.model in models: return True,"ok"
                    return False, f"model '{self.model}' not in {models}"
                r=client.get(f"{self.host}/api/tags")
                if r.status_code==200 and self.model in r.text: return True,"ok"
                return False,"host reachable but model not found"
        except Exception as e:
            return False, f"health_check error: {e}"

    def _trace(self, kind:str, payload:Dict[str,Any]):
        if not self.trace_enabled: return
        try:
            os.makedirs(os.path.dirname(self.trace_path), exist_ok=True)
            with open(self.trace_path,"a",encoding="utf-8") as f:
                f.write(json.dumps({"ts":datetime.utcnow().isoformat()+"Z","kind":kind,**payload}, ensure_ascii=False)+"\n")
        except Exception: pass

    def _chat(self, messages:list[dict], json_only:bool=False)->Dict[str,Any]:
        url=f"{self.host}/v1/chat/completions"
        payload={"model":self.model,"messages":messages,"temperature":self.temperature,"stream":False,"max_tokens":self.max_tokens}
        if json_only: payload["format"]={"type":"json_object"}
        self._trace("request",{"payload":payload})
        with httpx.Client(timeout=60) as client:
            r=client.post(url,json=payload); r.raise_for_status(); data=r.json()
        self._trace("response",{"data":data})
        content=data["choices"][0]["message"]["content"]
        usage=data.get("usage") or {}
        if not usage:
            prompt_text=" ".join(m.get("content","") for m in messages)
            usage={"prompt_tokens":approx_token_count(prompt_text),"completion_tokens":approx_token_count(content),"total_tokens":approx_token_count(prompt_text)+approx_token_count(content),"estimated":True}
        return {"content":content,"usage":usage}

    def _cached_json(self, system:str, user:str)->Dict[str,Any]:
        key=make_key("ollama",self.model,system,user)
        hit=cache_get(key,self.cache_ttl_sec,db_path=self.cache_db)
        if hit:
            text,meta=hit; self._trace("cache_hit",{"key":key})
            try: return {"json":json.loads(text),"usage":meta.get("usage",{}),"from_cache":True}
            except Exception: pass
        out=self._chat([{"role":"system","content":system},{"role":"user","content":user}], json_only=True)
        cache_put(key,out["content"],{"usage":out["usage"]}, db_path=self.cache_db)
        return {"json":json.loads(out["content"]), "usage":out["usage"], "from_cache":False}

    def _cached_text(self, system:str, user:str)->Dict[str,Any]:
        key=make_key("ollama",self.model,system,user)
        hit=cache_get(key,self.cache_ttl_sec,db_path=self.cache_db)
        if hit:
            text,meta=hit; self._trace("cache_hit",{"key":key})
            return {"text":text,"usage":meta.get("usage",{}),"from_cache":True}
        out=self._chat([{"role":"system","content":system},{"role":"user","content":user}], json_only=False)
        cache_put(key,out["content"],{"usage":out["usage"]}, db_path=self.cache_db)
        return {"text":out["content"],"usage":out["usage"],"from_cache":False}

    @property
    def enabled(self)->bool: return self._enabled
    @enabled.setter
    def enabled(self,v:bool): self._enabled=v

    def analyze_news(self, text:str)->dict:
        if not self.enabled: return {}
        user=USER_ANALYZE_TEMPLATE.format(news_text=text[:6000])
        try:
            out=self._cached_json(SYSTEM_NEWS_ANALYST,user); self._trace("analyze_usage",out["usage"]); data=out["json"]
        except Exception as e:
            data={"importance":0,"title":text.strip().split("\n")[0][:120] if text else "Новость","summary":"","category":"прочее","tickers":[],"error":str(e)}
        try: imp=float(data.get("importance",0))
        except Exception: imp=0.0
        data["importance"]=max(0.0,min(10.0,imp))
        data["category"]=(data.get("category") or "прочее").lower()
        if not isinstance(data.get("tickers"), list): data["tickers"]=[]
        return data

    def synthesize_digest(self, items:List[dict], header_emoji:str)->str:
        if not self.enabled: return ""
        payload=[{"title":it.get("llm_title") or it.get("title"),"summary":it.get("llm_summary") or it.get("summary"),"link":it.get("link"),"category":it.get("llm_category") or it.get("category",""),"channel":it.get("channel"),"score":it.get("score")} for it in items]
        user=USER_DIGEST_TEMPLATE.format(items_json=json.dumps(payload, ensure_ascii=False))
        out=self._cached_text(SYSTEM_DIGEST_WRITER, f"{header_emoji}\n\n"+user); self._trace("digest_usage", out["usage"])
        return out["text"].strip()
