
from sqlalchemy import text
from quadridigest.storage.db import get_engine
from quadridigest.types import PostRecord

class PostsRepo:
    def __init__(self):
        self.engine = get_engine()

    def upsert(self, rec: PostRecord):
        with self.engine.begin() as conn:
            conn.execute(text("""
                insert into posts(topic,fingerprint,headline,summary,full_text,channel,message_id,expanded,prompt_version)
                values(:topic,:fingerprint,:headline,:summary,:full_text,:channel,:message_id,:expanded,:prompt_version)
            """), rec.model_dump())

    def exists(self, channel: str, fingerprint: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                select 1 from posts where channel=:channel and fingerprint=:fp limit 1
            """), {"channel": channel, "fp": fingerprint}).fetchone()
            return bool(row)
