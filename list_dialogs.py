# list_dialogs.py
import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
session_name = os.getenv("SESSION_NAME", "quadridigest_session")

client = TelegramClient(session_name, api_id, api_hash)

async def main():
    await client.connect()
    async for d in client.iter_dialogs():
        ent = d.entity
        name = getattr(ent, "title", None) or getattr(ent, "first_name", None) or str(ent.id)
        username = getattr(ent, "username", None)
        print(f"{name:40}  id={getattr(ent,'id',None)}  username={username}")

with client:
    client.loop.run_until_complete(main())
