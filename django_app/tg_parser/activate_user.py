from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# ==== ВПИШИТЕ СВОИ ДАННЫЕ ====
api_id = 2040            # API ID (с https://my.telegram.org/auth)
api_hash = "b18441a1ff607e10a989891a5462e627"  # API HASH
session_name = "my_session"  # имя файла сессии
# ==============================


async def main():
    client = TelegramClient(session_name, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        phone = input(
            "Введите номер телефона (в международном формате, например +79991234567): ")

        # Отправляем код авторизации
        await client.send_code_request(phone)

        code = input("Введите код из SMS/Telegram: ")

        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            # Если включена двухфакторная защита
            password = input("Введите пароль двухфакторной аутентификации: ")
            await client.sign_in(password=password)

    print("Авторизация успешно выполнена!")
    await client.disconnect()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
