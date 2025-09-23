from __future__ import annotations
import os
from dotenv import load_dotenv
def load_config()->dict:
    load_dotenv()
    cfg={
        "api_id": int(os.getenv("API_ID","0")),
        "api_hash": os.getenv("API_HASH",""),
        "session_name": os.getenv("SESSION_NAME","quadridigest_session"),
        "timezone": os.getenv("TZ","Europe/Prague"),
        "max_items": int(os.getenv("MAX_ITEMS","10")),
        "min_score": float(os.getenv("MIN_SCORE","3.0")),
        "default_schedule": os.getenv("DEFAULT_SCHEDULE","hourly"),
        "window": os.getenv("WINDOW","hours=1"),
        "targets": {
            "politics": os.getenv("TARGET_POLITICS",""),
            "trading": os.getenv("TARGET_TRADING",""),
            "infosec": os.getenv("TARGET_INFOSEC",""),
            "personal": os.getenv("TARGET_PERSONAL",""),
        },
        "llm": {
            "enabled": os.getenv("LLM_ENABLED","true").lower()=="true",
            "host": os.getenv("OLLAMA_HOST","http://127.0.0.1:11434"),
            "model": os.getenv("LLM_MODEL","llama3.1:8b"),
            "temperature": float(os.getenv("LLM_TEMPERATURE","0.2")),
            "max_tokens": int(os.getenv("LLM_MAX_TOKENS","800")),
            "publish_mode": os.getenv("PUBLISH_MODE","bullets"),
            "llm_weight": float(os.getenv("LLM_WEIGHT","1.2")),
            "cache_db": os.getenv("LLM_CACHE_DB","llm_cache.sqlite3"),
            "cache_ttl_days": float(os.getenv("LLM_CACHE_TTL_DAYS","7")),
            "trace_enabled": os.getenv("LLM_TRACE_ENABLED","true").lower()=="true",
            "trace_path": os.getenv("LLM_TRACE_PATH","llm_traces.log"),
        }
    }
    return cfg
