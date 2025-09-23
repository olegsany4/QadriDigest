# quadridigest/dedupe.py
import sqlite3, time, hashlib
DB="digests.sqlite3"

def _db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS digests(
        h TEXT PRIMARY KEY,
        ts INTEGER NOT NULL
    )""")
    return c

def make_hash(source: str, text: str, url: str="") -> str:
    m = hashlib.sha256()
    m.update((source or "").encode())
    m.update((text or "").encode())
    m.update((url or "").encode())
    return m.hexdigest()

def seen(h: str, ttl_days: int=14) -> bool:
    with _db() as c:
        row = c.execute("SELECT ts FROM digests WHERE h=?", (h,)).fetchone()
        if not row: return False
        return (time.time()-row[0]) < ttl_days*86400

def mark(h: str):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO digests(h,ts) VALUES(?,?)", (h,int(time.time())))
