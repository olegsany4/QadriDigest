from __future__ import annotations
import sqlite3, os, time, json, hashlib
DEFAULT_DB=os.path.join(os.path.dirname(__file__),"..","..","llm_cache.sqlite3")
SCHEMA="""CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, created_at INTEGER NOT NULL, response_text TEXT NOT NULL, meta_json TEXT);"""
def _connect(db_path: str = DEFAULT_DB)->sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn=sqlite3.connect(db_path); conn.execute("PRAGMA journal_mode=WAL;"); conn.execute("PRAGMA synchronous=NORMAL;"); conn.execute(SCHEMA); return conn
def make_key(*parts:str)->str:
    h=hashlib.sha256()
    for p in parts: h.update((p or "").encode("utf-8","ignore")); h.update(b"\x00")
    return h.hexdigest()
def get(key:str, ttl_sec:int, db_path:str=DEFAULT_DB):
    conn=_connect(db_path)
    try:
        cur=conn.execute("SELECT created_at,response_text,meta_json FROM cache WHERE key=?", (key,)); row=cur.fetchone()
        if not row: return None
        created_at,text,meta_json=row
        if ttl_sec>0 and (time.time()-created_at)>ttl_sec: return None
        meta=json.loads(meta_json) if meta_json else {}
        return text, meta
    finally:
        conn.close()
def put(key:str, text:str, meta:dict|None=None, db_path:str=DEFAULT_DB):
    conn=_connect(db_path)
    try:
        conn.execute("REPLACE INTO cache(key,created_at,response_text,meta_json) VALUES (?,?,?,?)",(key,int(time.time()),text,json.dumps(meta or {}, ensure_ascii=False))); conn.commit()
    finally:
        conn.close()
