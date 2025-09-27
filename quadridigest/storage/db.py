
from sqlalchemy import create_engine, text
from quadridigest.config import settings
import os

def get_engine():
    os.makedirs(settings.data_dir, exist_ok=True)
    db_path = os.path.join(settings.data_dir, "digests.sqlite3")
    return create_engine(f"sqlite:///{db_path}", echo=False, future=True)

def init_db():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
        create table if not exists posts(
            id integer primary key autoincrement,
            topic text not null,
            fingerprint text not null,
            headline text not null,
            summary text not null,
            full_text text not null,
            channel text not null,
            message_id integer,
            expanded integer default 0,
            prompt_version text default 'V2',
            created_at datetime default current_timestamp
        );
        """))
