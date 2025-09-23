import os
import time
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError, AuthRestartError

load_dotenv()
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
session_name = os.getenv("SESSION_NAME", "quadridigest_session")

client = TelegramClient(session_name, api_id, api_hash)

async def main():
    await client.connect()
    if await client.is_user_authorized():
        print("✅ Уже авторизовано. Сессия есть.")
        return

    try:
        # Попробуем QR-логин — без ввода 2FA-пароля
        print("📱 Открой Telegram на телефоне → Настройки → Устройства → Подключить устройство (сканировать QR).")
        print("⚠️ Если QR не покажется, ты ещё под FloodWait — подожди и повтори.")
        qr = await client.qr_login()
        print("🔳 Отсканируй QR-код этой сессии в приложении Telegram (устройства → подключить).")
        print("⏳ Ожидаю подтверждение на телефоне…")
        await qr.wait()  # ждём подтверждение на телефоне
        print("✅ QR-логин подтверждён.")
    except FloodWaitError as e:
        print(f"⛔ FloodWait: подожди {e.seconds} сек и запусти снова.")
        return
    except AuthRestartError:
        print("↻ Telegram перезапросил авторизацию. Запусти скрипт ещё раз.")
        return
    except SessionPasswordNeededError:
        # На случай если QR отключён — fallback на пароль 2FA
        pwd = input("Введите 2FA-пароль Telegram: ").strip()
        await client.sign_in(password=pwd)

    print("✅ Сессия сохранена:", session_name + ".session")
    await client.disconnect()

with client:
    client.loop.run_until_complete(main())
