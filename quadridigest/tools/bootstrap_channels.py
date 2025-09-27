# quadridigest/tools/bootstrap_channels.py
import argparse
import json
import os
from telethon import TelegramClient
from telethon.tl.types import Channel
from quadridigest.config import settings

DATA_PATH = os.path.join(settings.data_dir, "channel_peers.json")

def _ensure_data_dir():
    os.makedirs(settings.data_dir, exist_ok=True)

def _load_map():
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def _save_map(m):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)

async def resolve_as_user(ids_or_usernames: list[str]):
    """
    Логинимся КАК ПОЛЬЗОВАТЕЛЬ (НЕ бот), ищем каналы среди СВОИХ диалогов.
    Требование: пользовательский аккаунт должен состоять в этих каналах.
    """
    # отдельная юзер-сессия, чтобы не путать с ботом
    user_session = (settings.session_name or "quadridigest_session") + "_user"
    client = TelegramClient(user_session, settings.tg_api_id, settings.tg_api_hash)
    await client.start()  # первый раз спросит код/пароль 2FA

    cur = _load_map()
    updated = False

    # Снимем текущие диалоги в память: id -> Channel
    dialogs = {}
    async for d in client.iter_dialogs():
        ent = getattr(d, "entity", None)
        if isinstance(ent, Channel):
            dialogs[str(ent.id)] = ent  # id в виде '1002...'

    for raw in ids_or_usernames:
        key = str(raw).strip()
        ent = None

        if key.startswith("@"):
            # Разрешение по username, если вдруг решишь использовать публичный канал
            try:
                tmp = await client.get_entity(key)
                if isinstance(tmp, Channel):
                    ent = tmp
            except Exception as e:
                print(f"[ERR] {key}: username не найден ({e})")
        else:
            # Числовой ID: доступен ТОЛЬКО если ты состоишь в канале
            ent = dialogs.get(key)
            if not ent:
                print(f"[ERR] {key}: пользовательский аккаунт не состоит в канале; "
                      f"добавь его в канал или укажи @username")
                continue

        if not ent or not isinstance(ent, Channel):
            print(f"[SKIP] {key}: не Channel")
            continue

        if not getattr(ent, "access_hash", None):
            print(f"[WARN] {key}: нет access_hash (возможно, нет прав/доступа)")
            continue

        cur[str(ent.id)] = {"id": int(ent.id), "access_hash": int(ent.access_hash)}
        print(f"[OK] {key} → id={ent.id}, access_hash={ent.access_hash}")
        updated = True

    if updated:
        _ensure_data_dir()
        _save_map(cur)
        print(f"[SAVE] Обновлено {DATA_PATH}")
    await client.disconnect()

def main():
    ap = argparse.ArgumentParser(description="Bootstrap channel access_hash map (user session).")
    ap.add_argument("--targets", nargs="+", required=True,
                    help="Список каналов: числовые id (1002...) или @username")
    args = ap.parse_args()
    import asyncio
    asyncio.run(resolve_as_user(args.targets))

if __name__ == "__main__":
    main()
