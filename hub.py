from __future__ import annotations

import copy
import logging
import os
import sys
import threading
import time
from datetime import datetime
from html import escape
from typing import Callable

from accounts import AccountError, AccountRegistry, env_keys, normalize_proxy
from cardinal import Cardinal
from plugin_manager import PluginManager
from Utils import tools
from Utils.envfile import read_env, remove_env, set_env
from Utils.errors import FATAL_ERRORS, fatal_text, short_error
from Utils.settings import DEFAULT_SETTINGS, SettingsError, load_settings
from Utils.storage import GLOBAL, AccountStorage

logger = logging.getLogger("cardinal.hub")

ERROR_NOTIFY_INTERVAL = 3600
WITHDRAW_FIELDS = ("provider", "account", "payment_method", "bank", "label")


def account_kwargs(creds: dict) -> dict:
    kwargs = {"user_agent": creds.get("user_agent", ""), "proxy": creds.get("proxy") or None}
    if creds.get("cookies"):
        kwargs["cookies"] = creds["cookies"]
    else:
        kwargs["token"] = creds.get("token")
        kwargs["ddg5"] = creds.get("ddg5", "")
    return kwargs


def default_account_factory(creds: dict):
    from playerokapi.account import Account
    return Account(**account_kwargs(creds))


class Hub:
    def __init__(self, env: dict, registry: AccountRegistry, plugins: PluginManager,
                 account_factory: Callable[[dict], object] | None = None, listener_factory=None,
                 env_path: str = ".env"):
        self.env = env
        self.registry = registry
        self.plugins = plugins
        self.account_factory = account_factory or default_account_factory
        self.listener_factory = listener_factory
        self.env_path = env_path
        self.cardinals: dict[str, Cardinal] = {}
        self.telegram = None
        self.start_time = time.time()
        self.started = False
        self._notices: list[str] = []
        self._error_notified: dict[str, float] = {}
        self._lock = threading.RLock()
        self._account_locks: dict[str, threading.RLock] = {}
        self._stop = threading.Event()

    def _acc_lock(self, acc_id: str) -> threading.RLock:
        with self._lock:
            return self._account_locks.setdefault(acc_id, threading.RLock())

    def _require(self, acc_id: str):
        info = self.registry.get(acc_id)
        if info is None:
            raise AccountError("Аккаунт не найден")
        return info

    def credentials(self, acc_id: str) -> dict:
        env = read_env(self.env_path)
        return {field: env.get(key, "") for field, key in env_keys(acc_id).items()}

    @staticmethod
    def withdraw_keys(acc_id: str) -> dict[str, str]:
        return {field: f"ACC_{acc_id}_WITHDRAW_{field.upper()}" for field in WITHDRAW_FIELDS}

    def withdraw_method(self, acc_id: str) -> dict:
        env = read_env(self.env_path)
        return {field: env.get(key, "") for field, key in self.withdraw_keys(acc_id).items()}

    def set_withdraw_method(self, acc_id: str, **fields: str) -> None:
        keys = self.withdraw_keys(acc_id)
        unknown = set(fields) - set(keys)
        if unknown:
            raise ValueError(f"unknown withdraw fields: {sorted(unknown)}")
        with self._acc_lock(acc_id):
            remove_env(list(keys.values()), self.env_path)
            set_env({keys[f]: v for f, v in fields.items() if v}, self.env_path)

    def clear_withdraw_method(self, acc_id: str) -> None:
        with self._acc_lock(acc_id):
            remove_env(list(self.withdraw_keys(acc_id).values()), self.env_path)

    def create_account(self, acc_id: str):
        return self.account_factory(self.credentials(acc_id))

    def _login(self, creds: dict):
        try:
            account = self.account_factory(creds)
            account.get()
            return account
        except FATAL_ERRORS as e:
            raise AccountError(fatal_text(e))
        except AccountError:
            raise
        except Exception as e:
            raise AccountError(f"Не удалось войти в аккаунт: {short_error(e)}")

    def _make_cardinal(self, acc_id: str, account=None) -> Cardinal:
        info = self._require(acc_id)
        storage = AccountStorage(self.registry.folder(acc_id))
        try:
            settings = load_settings(storage.settings_path)
            error = ""
        except SettingsError as e:
            settings = copy.deepcopy(DEFAULT_SETTINGS)
            error = e.text
        cardinal = Cardinal(self, info, storage, settings, account=account, listener_factory=self.listener_factory)
        if error:
            cardinal.status = "error"
            cardinal.error = error
            cardinal.settings_error = error
        return cardinal

    def attempt_start(self, cardinal: Cardinal) -> str:
        try:
            cardinal.start()
        except FATAL_ERRORS as e:
            cardinal.status = "error"
            cardinal.error = fatal_text(e)
            self.notify_error(cardinal)
            return "fatal"
        except Exception as e:
            cardinal.status = "error"
            cardinal.error = f"Нет связи с Playerok: {short_error(e)}"
            self.notify_error(cardinal)
            return "retry"
        if cardinal.status == "running" and cardinal.info.username:
            self.registry.set_username(cardinal.id, cardinal.info.username)
        self._error_notified.pop(cardinal.id, None)
        return "ok"

    def _retry_loop(self, cardinal: Cardinal) -> None:
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            if cardinal._stop.wait(min(600, 30 * 2 ** (attempt - 1))):
                return
            with self._acc_lock(cardinal.id):
                if self.cardinals.get(cardinal.id) is not cardinal or cardinal._stop.is_set():
                    return
                if self.attempt_start(cardinal) != "retry":
                    return

    def _launch(self, cardinal: Cardinal) -> None:
        if self.attempt_start(cardinal) == "retry":
            threading.Thread(target=self._retry_loop, args=(cardinal,), daemon=True,
                             name=f"{cardinal.id}-retry").start()

    def start_account(self, acc_id: str) -> Cardinal:
        self._require(acc_id)
        with self._acc_lock(acc_id):
            old = self.cardinals.get(acc_id)
            if old is not None:
                old.stop(30)
            cardinal = self._make_cardinal(acc_id)
            self.cardinals[acc_id] = cardinal
            if cardinal.status == "error":
                self.notify_error(cardinal)
                return cardinal
            if not cardinal.info.enabled:
                cardinal.status = "disabled"
                return cardinal
            self._launch(cardinal)
            return cardinal

    def stop_account(self, acc_id: str) -> None:
        with self._acc_lock(acc_id):
            cardinal = self.cardinals.get(acc_id)
            if cardinal is not None:
                cardinal.stop(30)

    def restart_account(self, acc_id: str) -> Cardinal:
        return self.start_account(acc_id)

    def add_account(self, token: str = "", cookies: str = "", proxy: str = "", user_agent: str = "") -> Cardinal:
        proxy = normalize_proxy(proxy)
        token, cookies, user_agent = token.strip(), cookies.strip(), user_agent.strip()
        if not token and not cookies:
            raise AccountError("Нужен токен или строка cookies")
        if len(self.registry.accounts) >= self.registry.max_accounts:
            raise AccountError(f"Достигнут лимит аккаунтов: {self.registry.max_accounts}")
        creds = {"token": token, "cookies": cookies, "ddg5": "", "proxy": proxy, "user_agent": user_agent}
        account = self._login(creds)
        with self._lock:
            if self.registry.by_username(account.username):
                raise AccountError("Этот аккаунт уже добавлен")
            info = self.registry.add(account.username)
            keys = env_keys(info.id)
            set_env({keys[field]: value for field, value in creds.items() if value}, self.env_path)
        with self._acc_lock(info.id):
            cardinal = self._make_cardinal(info.id, account=account)
            self.cardinals[info.id] = cardinal
            logger.info(f"Добавлен аккаунт {account.username} ({info.id})")
            self._launch(cardinal)
        return cardinal

    def remove_account(self, acc_id: str) -> str:
        self._require(acc_id)
        with self._acc_lock(acc_id):
            cardinal = self.cardinals.pop(acc_id, None)
            if cardinal is not None:
                cardinal.stop(30)
            path = self.registry.remove(acc_id)
            keys = list(env_keys(acc_id).values()) + list(self.withdraw_keys(acc_id).values())
            remove_env(keys, self.env_path)
        logger.info(f"Аккаунт {acc_id} убран, данные перенесены в {path}")
        return path

    def set_account_enabled(self, acc_id: str, enabled: bool) -> None:
        self._require(acc_id)
        with self._acc_lock(acc_id):
            self.registry.set_enabled(acc_id, enabled)
            if enabled:
                self.start_account(acc_id)
            else:
                self.stop_account(acc_id)
                if acc_id in self.cardinals:
                    self.cardinals[acc_id].status = "disabled"

    def update_credentials(self, acc_id: str, **creds) -> Cardinal:
        info = self._require(acc_id)
        if "proxy" in creds:
            creds["proxy"] = normalize_proxy(creds["proxy"])
        cleaned = {field: value.strip() for field, value in creds.items() if value is not None}
        if cleaned.get("token") or cleaned.get("cookies"):
            merged = {**self.credentials(acc_id), **cleaned}
            if cleaned.get("token") and not cleaned.get("cookies"):
                merged["cookies"] = ""
            probe = self._login(merged)
            name = getattr(probe, "username", "") or ""
            if info.username and name.lower() != info.username.lower():
                raise AccountError(f"Это другой аккаунт Playerok ({name}). "
                                   f"Новый аккаунт добавляйте через «➕ Добавить аккаунт»")
            other = self.registry.by_username(name)
            if other is not None and other.id != acc_id:
                raise AccountError("Этот аккаунт уже добавлен")
        keys = env_keys(acc_id)
        with self._acc_lock(acc_id):
            updates = {keys[f]: v for f, v in cleaned.items() if v}
            clears = [keys[f] for f, v in cleaned.items() if not v]
            if updates:
                set_env(updates, self.env_path)
            if clears:
                remove_env(clears, self.env_path)
            if keys["token"] in updates and "cookies" not in cleaned:
                remove_env([keys["cookies"]], self.env_path)
            return self.start_account(acc_id)

    def reset_settings(self, acc_id: str) -> str:
        self._require(acc_id)
        with self._acc_lock(acc_id):
            path = AccountStorage(self.registry.folder(acc_id)).settings_path
            backup = ""
            if os.path.exists(path):
                backup = f"{path}.broken-{datetime.now():%Y%m%d-%H%M%S}"
                os.replace(path, backup)
            self.start_account(acc_id)
        logger.info(f"Настройки аккаунта {acc_id} сброшены, старый файл: {backup}")
        return backup

    def notify(self, text: str, keyboard=None, account: Cardinal | None = None, kind: str | None = None) -> None:
        if account is not None and kind and not account.settings["notifications"].get(kind, True):
            return
        if account is not None and len(self.cardinals) > 1:
            text = f"[{escape(account.label)}] {text}"
        if self.telegram is None:
            return
        try:
            self.telegram.send_notification(text, keyboard, account.id if account else None, kind)
        except Exception:
            logger.exception("Не удалось отправить уведомление в Telegram")

    def notify_error(self, cardinal: Cardinal) -> None:
        now = time.time()
        if now - self._error_notified.get(cardinal.id, 0) < ERROR_NOTIFY_INTERVAL:
            return
        self._error_notified[cardinal.id] = now
        logger.error(f"[{cardinal.label}] {cardinal.error}")
        from tg_bot import keyboards
        self.notify(f"🔴 Аккаунт <b>{escape(cardinal.label)}</b>: {escape(cardinal.error)}",
                    keyboards.account_error_kb(cardinal.id))

    def queue_notice(self, text: str) -> None:
        if self.started and self.telegram is not None:
            self.telegram.send_notification(text)
        else:
            self._notices.append(text)

    def run_lifecycle(self, bind: str) -> None:
        for func in self.plugins.handlers(bind):
            try:
                func(self)
            except Exception:
                logging.getLogger("cardinal").exception(f"Ошибка в хэндлере {getattr(func, '__name__', func)}")

    def add_telegram_commands(self, uuid: str, commands: list[tuple[str, str, bool]]) -> None:
        self.plugins.add_commands(uuid, commands)
        if self.telegram is not None:
            for command, description, in_menu in commands:
                if in_menu:
                    self.telegram.add_command_to_menu(command, description)

    def load_plugin_data(self, plugin: str, default):
        return GLOBAL.load_plugin_data(plugin, default)

    def save_plugin_data(self, plugin: str, data) -> None:
        GLOBAL.save_plugin_data(plugin, data)

    def is_blacklisted(self, username: str | None) -> bool:
        return bool(username) and username.lower() in tools.load_cache("blacklist.json", [])

    def ban(self, username: str) -> bool:
        name = username.strip().lstrip("@").lower()
        with self._lock:
            blacklist = tools.load_cache("blacklist.json", [])
            if not name or name in blacklist:
                return False
            blacklist.append(name)
            tools.save_cache("blacklist.json", blacklist)
            return True

    def unban(self, username: str) -> bool:
        name = username.strip().lstrip("@").lower()
        with self._lock:
            blacklist = tools.load_cache("blacklist.json", [])
            if name not in blacklist:
                return False
            blacklist.remove(name)
            tools.save_cache("blacklist.json", blacklist)
            return True

    @property
    def blacklist(self) -> list[str]:
        return tools.load_cache("blacklist.json", [])

    def start(self) -> None:
        self.run_lifecycle("BIND_TO_PRE_INIT")
        for info in list(self.registry.accounts):
            self.start_account(info.id)
        self.run_lifecycle("BIND_TO_POST_INIT")
        self.run_lifecycle("BIND_TO_PRE_START")
        if self.telegram is not None:
            self.telegram.start()
        self.started = True
        for notice in self._notices:
            self.queue_notice(notice)
        self._notices.clear()
        self.run_lifecycle("BIND_TO_POST_START")
        logger.info(f"Playerok Cardinal запущен. Аккаунтов: {len(self.cardinals)}, плагинов: {len(self.plugins.plugins)}")

    def run_forever(self) -> None:
        self.start()
        try:
            while not self._stop.wait(1):
                pass
        except KeyboardInterrupt:
            logger.info("Остановка по Ctrl+C")
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stop.is_set() and not self.started:
            return
        self.run_lifecycle("BIND_TO_PRE_STOP")
        for cardinal in list(self.cardinals.values()):
            cardinal.stop(10)
        self.run_lifecycle("BIND_TO_POST_STOP")
        self.started = False
        self._stop.set()

    def restart(self) -> None:
        self.stop()
        logger.info("Перезапуск...")
        os.execl(sys.executable, sys.executable, *sys.argv)
