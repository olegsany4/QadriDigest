# -*- coding: utf-8 -*-
# quadridigest/targets.py
"""
Резолвер TARGET_* (куда публикуем сообщения). Особенности:
  • Поддержка @username / https://t.me/... / numeric id.
  • Для numeric id нужен access_hash → берём из локального sqlite-кэша (peers.sqlite3).
  • Если записи нет, автоматически прогреваем кэш через client.iter_dialogs().
  • Возвращаем объект, который можно передать напрямую в client.send_message():
       - '@username' (строка), либо
       - InputPeerChannel(id, access_hash) для числовых каналов.

Файл самодостаточный: сам создаёт/использует базу peers.sqlite3 в текущем каталоге проекта
(путь можно переопределить переменной окружения PEERS_DB_PATH).
Также содержит утилиты для обслуживания кэша (inspect/clear/manual_insert).
"""

from __future__ import annotations

import os
import re
import sqlite3
import logging
from typing import Optional, Tuple, Any, List, Iterable

from telethon.tl.types import InputPeerChannel, Channel

# -----------------------------------
# Логирование для этого модуля
# -----------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("QuadriDigest.Targets")

# -----------------------------------
# Параметры/KV
# -----------------------------------
DB_PATH = os.getenv("PEERS_DB_PATH", os.path.join(os.getcwd(), "peers.sqlite3"))
RE_TME = re.compile(r"https?://t\.me/(.+)$", re.IGNORECASE)

# -----------------------------------
# БД: инициализация и helpers
# -----------------------------------
def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peers(
            channel_id INTEGER PRIMARY KEY,
            access_hash INTEGER NOT NULL,
            username TEXT,
            title TEXT
        )
    """)
    return conn

def _save_peer(channel_id: int, access_hash: int, username: Optional[str], title: Optional[str]) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO peers(channel_id, access_hash, username, title) VALUES(?,?,?,?)",
            (channel_id, access_hash, username, title),
        )
        log.debug("Saved peer: id=%s, username=%s, title=%s", channel_id, username, title)

def _get_peer(channel_id: int) -> Optional[Tuple[int, int]]:
    with _db() as conn:
        row = conn.execute(
            "SELECT channel_id, access_hash FROM peers WHERE channel_id=?",
            (channel_id,)
        ).fetchone()
        return (row[0], row[1]) if row else None

def _list_peers() -> List[Tuple[int, int, Optional[str], Optional[str]]]:
    with _db() as conn:
        rows = conn.execute("SELECT channel_id, access_hash, username, title FROM peers ORDER BY channel_id").fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

def _clear_peers() -> None:
    with _db() as conn:
        conn.execute("DELETE FROM peers")

def _manual_insert_peer(channel_id: int, access_hash: int, username: Optional[str] = None, title: Optional[str] = None) -> None:
    _save_peer(channel_id, access_hash, username, title)

# -----------------------------------
# Нормализация строки цели
# -----------------------------------
def _parse_target_str(s: str) -> str:
    """
    Нормализуем строки вида 'https://t.me/username' → 'username'.
    Другие строки возвращаем как есть.
    """
    s = (s or "").strip()
    m = RE_TME.match(s)
    if m:
        tail = m.group(1).strip("/")
        return tail
    return s

# -----------------------------------
# Прогрев кэша из client.iter_dialogs()
# -----------------------------------
async def _warm_cache_from_dialogs(client) -> None:
    """
    Пробегаемся по диалогам и сохраняем пары id + access_hash каналов.
    Это самый надёжный способ получить access_hash для numeric id.
    """
    async for d in client.iter_dialogs():
        ent = d.entity
        if isinstance(ent, Channel) and ent.access_hash is not None:
            _save_peer(ent.id, ent.access_hash, getattr(ent, "username", None), getattr(ent, "title", None))

# -----------------------------------
# Основной резолвер цели
# -----------------------------------
async def resolve_target(client, target: str) -> Any:
    """
    Принимает target-строку из .env (TARGET_*).
    Возвращает объект, который можно передать в client.send_message():
      • '@username' (строка) — если цель строковая,
      • InputPeerChannel(channel_id, access_hash) — если цель числовая.
    Алгоритм:
      1) Нормализуем 'https://t.me/...' → 'username'.
      2) Если не цифры — превращаем в '@username' и возвращаем.
      3) Если цифры — ищем access_hash в локальном кэше.
      4) Если нет — прогреваем кэш через iter_dialogs() и пробуем снова.
      5) В крайнем случае пробуем client.get_entity() (если сессия уже знает объект).
      6) Если ничего не помогло — возвращаем исходную строку (Telethon поднимет понятную ошибку).
    """
    s = _parse_target_str(target)

    # Username-цели
    if not s.isdigit():
        return s if s.startswith("@") else "@" + s

    # Numeric id → нужен access_hash
    cid = int(s)
    cached = _get_peer(cid)
    if cached:
        _, access_hash = cached
        return InputPeerChannel(channel_id=cid, access_hash=access_hash)

    # Прогреем кэш из диалогов
    await _warm_cache_from_dialogs(client)
    cached = _get_peer(cid)
    if cached:
        _, access_hash = cached
        return InputPeerChannel(channel_id=cid, access_hash=access_hash)

    # Попробуем через get_entity (если энтити уже попадалось в этой сессии)
    try:
        ent = await client.get_entity(cid)
        if isinstance(ent, Channel) and ent.access_hash is not None:
            _save_peer(ent.id, ent.access_hash, getattr(ent, "username", None), getattr(ent, "title", None))
            return InputPeerChannel(channel_id=ent.id, access_hash=ent.access_hash)
        return ent
    except Exception:
        # Ничего не вышло — Telethon выбросит ошибку сам при send_message
        return s

# -----------------------------------
# Служебные утилиты для администрирования кэша (необязательно)
# -----------------------------------
def inspect_cache() -> List[Tuple[int, int, Optional[str], Optional[str]]]:
    """
    Вернуть список известных пиров: (channel_id, access_hash, username, title).
    Удобно для диагностики.
    """
    return _list_peers()

def clear_cache() -> None:
    """
    Удалить все записи из кэша peers.sqlite3.
    """
    _clear_peers()

def manual_insert(channel_id: int, access_hash: int, username: Optional[str] = None, title: Optional[str] = None) -> None:
    """
    Ручное добавление пары (id, access_hash) в кэш. Полезно, если известны значения заранее.
    """
    _manual_insert_peer(channel_id, access_hash, username, title)
