import getpass
import os
import secrets
import sys
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))

INTRO = """
⚙️  Первая настройка Playerok Cardinal

Где взять токен Playerok:
  1. Войдите на playerok.com в браузере.
  2. F12 → Application (Приложение) → Cookies → https://playerok.com
  3. Скопируйте значение cookie «token».
  (Можно вставить и всю строку cookies целиком — тогда бот возьмёт её.)
"""


def ask(prompt: str, required: bool = False, default: str = "") -> str:
    while True:
        value = input(prompt).strip()
        if value or not required:
            return value or default
        print("  Это поле обязательно.")


def write_env(values: dict):
    lines = [f"{key}={value}" for key, value in values.items()]
    with open(".env", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run():
    if Path(".env").exists():
        print("Настройки уже существуют в .env. Они сохранены; повторная настройка их не изменит.")
        return
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    print(INTRO)
    raw = ask("🔑 Токен Playerok (или вся строка cookies): ", required=True)
    values = {"PLAYEROK_TOKEN": "", "PLAYEROK_COOKIES": "", "PLAYEROK_DDG5": ""}
    if ";" in raw or "token=" in raw:
        values["PLAYEROK_COOKIES"] = raw
    else:
        values["PLAYEROK_TOKEN"] = raw
        values["PLAYEROK_DDG5"] = ask("🛡 Cookie __ddg5_ (Enter — пропустить): ")
    values["PLAYEROK_USER_AGENT"] = ask("🌐 User-Agent браузера (Enter — стандартный): ")
    values["PLAYEROK_PROXY"] = ask("🧦 Прокси ip:port или user:pass@ip:port (Enter — без прокси): ")
    print("\n🤖 Telegram-панель управления (создайте бота у @BotFather).")
    values["TELEGRAM_BOT_TOKEN"] = ask("   Токен Telegram-бота (Enter — без Telegram): ")
    if values["TELEGRAM_BOT_TOKEN"]:
        password = getpass.getpass("   Пароль для входа в панель (Enter — сгенерировать): ").strip()
        if not password:
            password = secrets.token_urlsafe(9)
            print(f"   Ваш пароль: {password}  (сохранён в .env)")
        values["TELEGRAM_PASSWORD"] = password
    else:
        values["TELEGRAM_PASSWORD"] = ""
    write_env(values)
    print("\n✅ Настройки сохранены в .env\n")


if __name__ == "__main__":
    run()
