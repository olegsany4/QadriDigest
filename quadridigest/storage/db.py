# -*- coding: utf-8 -*-
"""
QadriDigest — storage.db
Определение таблиц и индексов. Уникальный индекс (channel, fingerprint).
Добавлен init_db() для совместимости с main.py.
"""
from __future__ import annotations

import os
import datetime as dt
from sqlalchemy import (
    MetaData, Table, Column, Integer, String, Text, DateTime, Index, create_engine, text
)
from sqlalchemy.engine import Engine

metadata = MetaData()

posts = Table(
    "posts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("channel", String(255), nullable=False, index=True),
    Column("message_id", Integer, nullable=True),
    Column("text", Text, nullable=False),
    Column("norm_text", Text, nullable=False),
    Column("fingerprint", String(64), nullable=False),  # формат 'b64:s64' (с запасом)
    Column("created_at", DateTime, nullable=False, default=dt.datetime.utcnow),
)

# SQLAlchemy-индекс (дублируем DDL ниже для надёжности)
uq_posts_channel_fp = Index(
    "uq_posts_channel_fp", posts.c.channel, posts.c.fingerprint, unique=True
)


def get_engine(url: str | None = None) -> Engine:
    url = url or os.getenv("DATABASE_URL", "sqlite:///./quadridigest.db")
    return create_engine(url, future=True)


def ensure_indexes(engine: Engine) -> None:
    """Создаём таблицы и уникальный индекс idempotent-способом."""
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_posts_channel_fp ON posts(channel, fingerprint)"
        ))


# ---- Совместимость с main.py ----
def init_db(url: str | None = None) -> Engine:
    """
    Инициализация БД, создание индексов. Возвращает Engine.
    Совместима с импортом: `from quadridigest.storage.db import init_db`.
    """
    engine = get_engine(url)
    ensure_indexes(engine)
    return engine
