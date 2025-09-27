from telethon import TelegramClient
from telethon.tl import types
from quadridigest.config import settings
import json, os

DATA_PATH = os.path.join(settings.data_dir, "channel_peers.json")

def build_client() -> TelegramClient:
    """
    Создаёт НЕзапущенный клиент Telethon. Запускать через await start_bot(client).
    """
    session_name = settings.session_name or "quadridigest_session"
    return TelegramClient(session_name, settings.tg_api_id, settings.tg_api_hash)

async def start_bot(client: TelegramClient) -> TelegramClient:
    await client.start(bot_token=settings.tg_bot_token)
    return client

def _load_map():
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

async def resolve_channel(client: TelegramClient, target: str):
    """
    Возвращает entity для send_message:
      - если target = @username → вернём строку (Telethon сам резолвит).
      - если target = числовой id → используем заранее полученный access_hash из data/channel_peers.json.
    """
    target = str(target).strip()
    if target.startswith("@"):
        return target

    # числовой id
    m = _load_map()
    row = m.get(target)
    if not row:
        raise ValueError(
            f"Для канала id={target} нет access_hash в {DATA_PATH}. "
            f"Сначала выполни bootstrap: "
            f"`python -m quadridigest.tools.bootstrap_channels --targets {target}`"
        )
    return types.InputPeerChannel(channel_id=int(row["id"]), access_hash=int(row["access_hash"]))
