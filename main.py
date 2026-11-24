import logging
import os
import sys
from logging.handlers import RotatingFileHandler

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

from Utils.envfile import read_env as read_env_file

FATAL_EXIT = 3

BANNER = """
==============================================
          P L A Y E R O K   C A R D I N A L
==============================================
"""


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s", "%d.%m %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    file = RotatingFileHandler("logs/log.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file.setLevel(logging.DEBUG)
    file.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file)
    for noisy in ("urllib3", "TeleBot", "websocket", "httpx", "curl_cffi"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("playerokapi").setLevel(logging.INFO)


def read_env() -> dict:
    env = read_env_file(".env")
    for key in ("PLAYEROK_TOKEN", "PLAYEROK_COOKIES", "TELEGRAM_BOT_TOKEN", "TELEGRAM_PASSWORD", "MAX_ACCOUNTS"):
        if os.getenv(key) and not env.get(key):
            env[key] = os.getenv(key)
    return {
        "tg_token": env.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "tg_password": env.get("TELEGRAM_PASSWORD", "").strip(),
        "max_accounts": int(env.get("MAX_ACCOUNTS", "") or 5),
        "control_url": env.get("CONTROL_URL", "https://2-26-227-197.sslip.io/pc-control").strip(),
        "control_admin_id": env.get("CONTROL_ADMIN_ID", "").strip(),
        "control_db": env.get("CONTROL_DB", "").strip(),
        "has_v1_token": bool(env.get("PLAYEROK_TOKEN") or env.get("PLAYEROK_COOKIES")),
    }


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    print(BANNER)
    setup_logging()
    logger = logging.getLogger("cardinal")
    for folder in ("plugins", "logs", "storage/cache", "accounts"):
        os.makedirs(folder, exist_ok=True)

    from accounts import AccountRegistry
    from Utils.migration import migrate_v1, needs_migration

    migrated = migrate_v1() if needs_migration() else None
    env = read_env()
    registry = AccountRegistry(max_accounts=env["max_accounts"])
    if not registry.accounts and not env["tg_token"]:
        if sys.stdin and sys.stdin.isatty():
            import first_setup
            first_setup.run()
            print("Запускаю бота...\n")
            if needs_migration():
                migrated = migrate_v1()
            env = read_env()
            registry = AccountRegistry(max_accounts=env["max_accounts"])
        if not registry.accounts and not env["tg_token"]:
            logger.error("Нет ни аккаунта Playerok, ни Telegram-бота. Запустите: python first_setup.py")
            sys.exit(FATAL_EXIT)

    from hub import Hub
    from plugin_manager import PluginManager

    plugins = PluginManager()
    plugins.load()
    hub = Hub(env, registry, plugins)
    if env["tg_token"]:
        from tg_bot.bot import TGBot
        hub.telegram = TGBot(hub)
        hub.telegram.init()
    elif not registry.accounts:
        logger.error("Аккаунтов нет, а без Telegram добавить их нельзя")
        sys.exit(FATAL_EXIT)
    if migrated:
        hub.queue_notice("✅ <b>Обновлено до v2.</b> Аккаунт, настройки, склад и чёрный список перенесены. "
                         "Копия старых файлов — в папке backup/.")
    hub.run_forever()


if __name__ == "__main__":
    main()
