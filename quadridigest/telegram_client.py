# -*- coding: utf-8 -*-
from __future__ import annotations

"""
quadridigest.telegram_client
----------------------------
Обёртка вокруг Telethon для:
- безопасного резолва каналов по username/URL/числовому id (в т.ч. формата 100xxxxxxxxxx из .env);
- конкурентного сбора сообщений с ограничением по таймаутам;
- отправки сообщений В КАНАЛ/супергруппу (без запасного варианта «в личку»);
- строгого логирования ошибок без падений всего процесса.

Особенности:
- При резолве numeric id мы сначала ищем его в собственных диалогах (самый надёжный путь),
  затем пытаемся получить entity через PeerChannel(channel_id=?).
- Поддерживаем id вида 100XXXXXXXXXX (встречается в конфиге) — нормализуем к последним 10 цифрам,
  которые совпадают с .id из list_dialogs.py.
"""

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Union

from telethon import TelegramClient
from telethon.errors import UsernameInvalidError, UsernameNotOccupiedError
from telethon.tl.types import Message, PeerChannel

_log = logging.getLogger("quadridigest.tg")


@dataclass
class TGMessage:
    id: int
    peer: str           # имя источника (username/идентификатор из входного списка)
    text: str
    date: datetime
    url: Optional[str] = None  # ссылка на оригинал, если смогли построить


def _normalize_source(s: str) -> str:
    """Удаляем @ и t.me/... -> username"""
    s = (s or "").strip()
    if s.startswith("@"):
        s = s[1:]
    m = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)/?$", s)
    if m:
        return m.group(1)
    return s


class TG:
    """
    Управляющий класс для работы с Telethon.
    """

    def __init__(self, session_name: str, api_id: int, api_hash: str, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.loop = loop or asyncio.get_event_loop()
        self.client: Optional[TelegramClient] = None

        # Кэши для быстрого резолва
        self._dialogs_indexed = False
        self._by_id: Dict[int, Any] = {}
        self._by_username: Dict[str, Any] = {}

    async def __aenter__(self) -> "TG":
        self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized. Use your signin flow first.")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.client:
            await self.client.disconnect()
        self.client = None
        self._dialogs_indexed = False
        self._by_id.clear()
        self._by_username.clear()

    # ---------- dialogs indexing / resolve ----------

    async def _ensure_dialogs_indexed(self) -> None:
        """Построение локального индекса id/username -> entity из доступных диалогов."""
        if self._dialogs_indexed:
            return
        assert self.client is not None
        async for d in self.client.iter_dialogs():
            ent = d.entity
            cid = getattr(ent, "id", None)
            if isinstance(cid, int):
                self._by_id[cid] = ent
                # Бывает, что в .env кладут id вида 100XXXXXXXXXX — добавим эквивалент в кэш на всякий
                try:
                    with contextlib.suppress(Exception):
                        long_id = int("100" + str(cid))
                        self._by_id[long_id] = ent
                except Exception:
                    pass
            uname = getattr(ent, "username", None)
            if isinstance(uname, str) and uname:
                self._by_username[uname.lower()] = ent
        self._dialogs_indexed = True

    @staticmethod
    def _normalize_numeric_id(val: int) -> int:
        """
        Приводим разные представления channel id к виду, совпадающему с .id из list_dialogs.py.
        - если прислали 100XXXXXXXXXX -> берём последние 10 цифр
        - иначе оставляем как есть
        """
        aval = abs(int(val))
        if aval >= 10**12:  # 13+ знаков
            # последние 10 цифр дают тот же id, что печатает list_dialogs.py
            return int(str(aval)[-10:])
        return aval

    async def _resolve_entity(self, ref: Union[str, int]) -> Any:
        """
        Поддерживаем:
          - username/URL (@name, name, t.me/name)
          - numeric id (из list_dialogs.py) и формат 100XXXXXXXXXX из .env
        """
        assert self.client is not None
        await self._ensure_dialogs_indexed()

        # numeric?
        s = str(ref).strip()
        if re.fullmatch(r"-?\d+", s):
            nid = self._normalize_numeric_id(int(s))
            ent = self._by_id.get(nid)
            if ent:
                return ent
            # Попробуем ещё через PeerChannel (может сработать без access_hash, если клиент его знает)
            with contextlib.suppress(Exception):
                return await self.client.get_entity(PeerChannel(channel_id=nid))
            raise ValueError(f"Unknown numeric channel id: {ref} (norm={nid})")

        # username / url
        uname = _normalize_source(s).lower()
        ent = self._by_username.get(uname)
        if ent:
            return ent
        # онлайн-резолв username
        try:
            ent = await self.client.get_entity(uname)
            self._by_username[uname] = ent
            cid = getattr(ent, "id", None)
            if isinstance(cid, int):
                self._by_id[cid] = ent
            return ent
        except (UsernameInvalidError, UsernameNotOccupiedError) as e:
            raise e

    # ---------- fetching ----------

    async def fetch_recent_from_channels(
        self,
        sources: Iterable[str],
        since_utc: Optional[datetime] = None,
        limit_per_channel: int = 50,
        per_channel_timeout: int = 20,
        concurrency: int = 6,
        debug: bool = False,
    ) -> List[TGMessage]:
        """Скачиваем последние сообщения из набора источников."""
        assert self.client is not None

        sem = asyncio.Semaphore(concurrency)
        out: List[TGMessage] = []

        async def fetch_one(src: str):
            t0 = asyncio.get_running_loop().time()
            try:
                async with sem:
                    ent = await self._resolve_entity(src)
                    count = 0

                    async def _stream():
                        async for m in self.client.iter_messages(ent, limit=limit_per_channel):
                            yield m

                    async with asyncio.timeout(per_channel_timeout):
                        async for m in _stream():
                            if not isinstance(m, Message):
                                continue
                            txt = (m.message or "").strip()
                            if not txt:
                                continue
                            dt = m.date or datetime.now(tz=timezone.utc)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)

                            # строим URL
                            url = None
                            uname = getattr(ent, "username", None)
                            try:
                                ch_id = getattr(m.peer_id, "channel_id", None)
                            except Exception:
                                ch_id = None
                            if isinstance(uname, str) and uname:
                                url = f"https://t.me/{uname}/{m.id}"
                            elif isinstance(ch_id, int):
                                url = f"https://t.me/c/{ch_id}/{m.id}"

                            out.append(TGMessage(
                                id=m.id,
                                peer=str(src),
                                text=txt,
                                date=dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc),
                                url=url,
                            ))
                            count += 1

                if debug:
                    dtsec = asyncio.get_running_loop().time() - t0
                    print(f"[fetch] {src}: {count} msgs in {dtsec:.1f}s")
            except asyncio.TimeoutError:
                if debug:
                    print(f"[fetch] {src}: timeout after {per_channel_timeout}s")
            except Exception as e:
                if debug:
                    print(f"[fetch] {src}: error {e}")

        await asyncio.gather(*(fetch_one(s) for s in sources))
        return out

    # ---------- posting ----------

    async def send_text(self, target: Union[str, int], text: str) -> bool:
        """
        Отправляем ТОЛЬКО в указанный канал/супергруппу.
        Если резолв не удался — логируем и возвращаем False.
        """
        assert self.client is not None
        name = str(target)
        try:
            ent = await self._resolve_entity(target)
        except Exception as e:
            print(f"[post] resolve error for {name}: {e}")
            return False
        try:
            await self.client.send_message(entity=ent, message=text, link_preview=False)
            return True
        except Exception as e:
            print(f"[post] send error for {name}: {e}")
            return False
