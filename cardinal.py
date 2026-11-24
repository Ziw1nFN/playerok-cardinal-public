from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date
from html import escape
from typing import Callable, NamedTuple

from Utils import tools
from Utils import withdraw as withdrawals
from Utils.errors import FATAL_ERRORS, fatal_text, short_error
from Utils.profiles import match as match_profile
from Utils.profiles import needs_places
from Utils.settings import DEFAULT_SETTINGS, NUMBER_LIMITS, SettingsError, save_settings
from Utils.stats import SalesJournal, deal_row
from Utils.storage import AccountStorage
from Utils.variables import render
from accounts import AccountInfo
from playerokapi.enums import ItemDealDirections, ItemDealStatuses, ItemStatuses, PriorityTypes
from playerokapi.exceptions import RequestPlayerokError
from plugin_manager import (ACTION_BINDS, ALL_BINDS, EVENT_BINDS, LIFECYCLE_BINDS, PLUGIN_FIELDS,  # noqa: F401
                            PluginData, PluginError, is_enabled)

logger = logging.getLogger("cardinal")

VERSION = "2.1.0"
HEALTH_INTERVAL = 900
REMINDER_INTERVAL = 600
SENT_KEEP_SECONDS = 8 * 86400
INITIAL_SALES = 1000
START_SYNC_SALES = 48
REFRESH_SALES = 200
RECHECK_LIMIT = 50
RECHECK_DAYS = 30
ITEM_PLACES_FILE = "item_places.json"
ITEM_PLACES_RETRY = 3600
ITEM_PLACES_PAUSE = 1.0
WITHDRAW_INTERVAL = 600

TEXT_KEYS = {"greetingsText": "greeting", "reviewThanksText": "review_thanks", "watermark": "watermark"}


class PlacesUnknown(Exception):
    pass


LIVE_STATUSES = (ItemStatuses.APPROVED, ItemStatuses.PENDING_APPROVAL, ItemStatuses.PENDING_MODERATION)
KEEP_REPORT_FAILS = 3
KEEP_RELIST_WAIT = 60


def is_not_found(error: RequestPlayerokError) -> bool:
    text = str(getattr(error, "error_message", "") or error).lower()
    return "не удалось найти" in text or str(getattr(error, "error_code", "") or "").upper() == "NOT_FOUND"


class SendResult(NamedTuple):
    status: str
    message_id: str | None
    error: str


class _AccountLog(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['cardinal'].label}] {msg}", kwargs


class Cardinal:
    def __init__(self, hub, info: AccountInfo, storage: AccountStorage, settings: dict, account=None,
                 listener_factory=None):
        self.hub = hub
        self.info = info
        self.id = info.id
        self.storage = storage
        self.settings = settings
        self.account = account
        self.listener_factory = listener_factory
        self.listener = None
        self.status = "stopped"
        self.error = ""
        self.settings_error = ""
        self.start_time = time.time()
        self.greeted: dict[str, float] = storage.load_cache("greeted.json", {})
        self.delivered: list[str] = storage.load_cache("delivered_deals.json", [])
        self.raise_state: dict = storage.load_cache("raise.json", {"date": "", "spent": 0, "last": {}})
        self.log = _AccountLog(logger, {"cardinal": self})
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._journal: SalesJournal | None = None
        self._stats_busy = threading.Lock()
        self._places_last = 0.0

    @property
    def label(self) -> str:
        return self.info.username or self.id

    @property
    def journal(self) -> SalesJournal:
        with self._lock:
            if self._journal is None:
                self._journal = SalesJournal(os.path.join(self.storage.root, "stats.db"))
            return self._journal

    def is_own_sale(self, deal) -> bool:
        user = getattr(deal, "user", None)
        own = user is not None and user.id == getattr(self.account, "id", None)
        return not own and getattr(deal, "direction", None) != ItemDealDirections.IN

    def sync_sales(self, limit: int, pause: float = 1.0) -> list[str]:
        return self._sync_sales(limit, pause)[0]

    def _sync_sales(self, limit: int, pause: float = 1.0) -> tuple[list[str], bool]:
        ids, cursor, complete = [], None, False
        while len(ids) < limit and not self._stop.is_set():
            page = self.account.get_deals(direction=ItemDealDirections.OUT, count=24, after_cursor=cursor)
            for deal in page.deals:
                if len(ids) >= limit:
                    break
                if self.is_own_sale(deal):
                    self.journal.record(deal_row(deal))
                    ids.append(deal.id)
            info = getattr(page, "page_info", None)
            if info is None or not info.has_next_page or len(ids) >= limit:
                complete = True
                break
            cursor = info.end_cursor
            if pause:
                self._stop.wait(pause)
        return ids, complete and not self._stop.is_set()

    def recheck_in_work(self, skip: set, limit: int = RECHECK_LIMIT, pause: float = 1.0) -> int:
        ids = [i for i in self.journal.in_work_ids(time.time() - RECHECK_DAYS * 86400) if i not in skip][:limit]
        checked = 0
        for deal_id in ids:
            if self._stop.is_set():
                break
            try:
                self.journal.record(deal_row(self.account.get_deal(deal_id)))
                checked += 1
            except Exception as e:
                self.log.warning(f"Статистика: не удалось проверить сделку {deal_id}: {short_error(e)}")
            if pause:
                self._stop.wait(pause)
        return checked

    def stats_on_start(self, pause: float = 1.0) -> None:
        with self._stats_busy:
            try:
                if self.journal.meta("initial_loaded") is None:
                    ids, complete = self._sync_sales(INITIAL_SALES, pause)
                    if complete:
                        self.journal.set_meta("initial_loaded", str(int(time.time())))
                        self.log.info(f"Статистика: загружено {len(ids)} продаж")
                    else:
                        self.log.info(f"Статистика: загрузка прервана на {len(ids)} продажах, продолжу при запуске")
                else:
                    ids = self.sync_sales(START_SYNC_SALES, pause)
                    self.recheck_in_work(set(ids), pause=pause)
            except Exception as e:
                self.log.warning(f"Статистика: не удалось загрузить продажи: {short_error(e)}")

    def item_places(self, item_id: str | None) -> list[str] | None:
        if not item_id:
            return None
        with self.storage.lock:
            entry = self.storage.load_cache(ITEM_PLACES_FILE, {}).get(item_id) or {}
        if "places" in entry:
            return list(entry["places"])
        if time.time() - entry.get("error_at", 0) < ITEM_PLACES_RETRY:
            return None
        wait = self._places_last + ITEM_PLACES_PAUSE - time.time()
        if wait > 0:
            self._stop.wait(wait)
        self._places_last = time.time()
        try:
            item = self.account.get_item(item_id)
            places = [n for n in (getattr(getattr(item, "game", None), "name", None),
                                  getattr(getattr(item, "category", None), "name", None)) if n]
            entry = {"places": places}
        except Exception as e:
            self.log.warning(f"Не удалось узнать игру и категорию товара {item_id}: {short_error(e)}")
            places, entry = None, {"error_at": time.time()}
        with self.storage.lock:
            cache = self.storage.load_cache(ITEM_PLACES_FILE, {})
            cache[item_id] = entry
            self.storage.save_cache(ITEM_PLACES_FILE, cache)
        return places

    def profile_for(self, name: str | None, price: int | None, item_id: str | None = None,
                    places: list[str] | None = None, strict: bool = False) -> dict | None:
        profiles = self.settings.get("profiles") or []
        if not profiles:
            return None
        if places is None and needs_places(profiles, name, price):
            places = self.item_places(item_id)
            if places is None and strict:
                raise PlacesUnknown(item_id)
        return match_profile(profiles, name, price, places or [])

    def stats_busy(self) -> bool:
        return self._stats_busy.locked()

    def refresh_stats(self) -> int | None:
        if not self._stats_busy.acquire(blocking=False):
            return None
        try:
            return len(self.sync_sales(REFRESH_SALES))
        finally:
            self._stats_busy.release()

    @property
    def telegram(self):
        return getattr(self.hub, "telegram", None)

    @property
    def plugins(self) -> dict[str, PluginData]:
        return self.hub.plugins.plugins

    def update_settings(self, change: Callable[[dict], None]) -> None:
        if self.settings_error:
            raise SettingsError(f"Настройки аккаунта {self.label} повреждены ({self.settings_error}). "
                                f"Сбросьте их: 👤 ▾ → ⚙️ → ♻️ Сбросить настройки")
        with self._lock:
            change(self.settings)
            save_settings(self.storage.settings_path, self.settings)

    @staticmethod
    def _group(section: str) -> str:
        return "notifications" if section == "Notifications" else "toggles"

    def is_on(self, key: str, section: str = "Playerok") -> bool:
        group = self._group(section)
        return bool(self.settings[group].get(key, DEFAULT_SETTINGS[group].get(key, False)))

    def setting(self, key: str, section: str = "Playerok") -> str:
        if section == "Notifications":
            return "1" if self.is_on(key, section) else "0"
        texts = self.settings["texts"]
        if key in TEXT_KEYS:
            return texts.get(TEXT_KEYS[key], "")
        if key in texts:
            return texts[key]
        if key == "raiseItems":
            return self.settings["raise_items"]
        if key in self.settings["numbers"]:
            return str(self.settings["numbers"][key])
        if key in self.settings["toggles"]:
            return "1" if self.settings["toggles"][key] else "0"
        return ""

    def number(self, key: str, section: str = "Playerok") -> float:
        return float(self.settings["numbers"].get(key, DEFAULT_SETTINGS["numbers"].get(key, 0)))

    def clip_number(self, key: str, value) -> float:
        low, high = NUMBER_LIMITS[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = float(DEFAULT_SETTINGS["numbers"][key])
        return min(max(number, low), high)

    def toggle(self, key: str, section: str = "Playerok") -> bool:
        group = self._group(section)
        result = {}

        def change(s: dict):
            s[group][key] = not s[group].get(key, False)
            result["value"] = s[group][key]

        self.update_settings(change)
        self.log.info(f"{group}.{key} = {result['value']}")
        return result["value"]

    def find_auto_response(self, text: str | None) -> dict | None:
        if not text:
            return None
        wanted = text.strip().lower()
        return next((e for e in self.settings["auto_responses"]
                     if wanted in [c.strip().lower() for c in e.get("commands", [])]), None)

    def find_auto_delivery(self, item_name: str | None) -> dict | None:
        if not item_name:
            return None
        wanted = item_name.strip().lower()
        return next((e for e in self.settings["auto_delivery"]
                     if e.get("enabled", True) and e.get("item", "").strip().lower() == wanted), None)

    def is_blacklisted(self, username: str | None) -> bool:
        return self.hub.is_blacklisted(username)

    def ban(self, username: str) -> bool:
        return self.hub.ban(username)

    def unban(self, username: str) -> bool:
        return self.hub.unban(username)

    def _fresh_delivered(self) -> list[str]:
        self.delivered = self.storage.load_cache("delivered_deals.json", [])
        return self.delivered

    def is_delivered(self, deal_id: str) -> bool:
        with self.storage.lock:
            return deal_id in self._fresh_delivered()

    def claim_deal(self, deal_id: str) -> bool:
        with self.storage.lock:
            delivered = self._fresh_delivered()
            if deal_id in delivered:
                return False
            delivered.append(deal_id)
            self.delivered = delivered[-1000:]
            self.storage.save_cache("delivered_deals.json", self.delivered)
            return True

    def release_deal(self, deal_id: str) -> None:
        with self.storage.lock:
            delivered = self._fresh_delivered()
            if deal_id in delivered:
                delivered.remove(deal_id)
                self.storage.save_cache("delivered_deals.json", delivered)

    def mark_delivered(self, deal_id: str) -> None:
        self.claim_deal(deal_id)

    def remember_command(self, message_id: str | None) -> bool:
        if not message_id:
            return True
        with self.storage.lock:
            done = self.storage.load_cache("commands_done.json", [])
            if message_id in done:
                return False
            done.append(message_id)
            self.storage.save_cache("commands_done.json", done[-200:])
            return True

    def remember_sent(self, deal_id: str, chat_id: str, values: dict) -> None:
        with self.storage.lock:
            data = self.storage.load_cache("sent_deals.json", {})
            data[deal_id] = {"chat_id": chat_id, "at": time.time(), "reminded": False, "values": values}
            self.storage.save_cache("sent_deals.json", data)

    def forget_sent(self, deal_id: str) -> None:
        with self.storage.lock:
            data = self.storage.load_cache("sent_deals.json", {})
            if data.pop(deal_id, None) is not None:
                self.storage.save_cache("sent_deals.json", data)

    def _set_reminded(self, deal_id: str, value: bool) -> bool:
        with self.storage.lock:
            data = self.storage.load_cache("sent_deals.json", {})
            entry = data.get(deal_id)
            if entry is None or bool(entry.get("reminded")) == value:
                return False
            entry["reminded"] = value
            self.storage.save_cache("sent_deals.json", data)
            return True

    def check_reminders(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        with self.storage.lock:
            data = self.storage.load_cache("sent_deals.json", {})
            fresh = {k: v for k, v in data.items() if now - v.get("at", now) <= SENT_KEEP_SECONDS}
            if len(fresh) != len(data):
                self.storage.save_cache("sent_deals.json", fresh)
        reminder = self.settings.get("reminder", {})
        if not reminder.get("enabled"):
            return 0
        limit = float(reminder.get("hours", 12)) * 3600
        sent = 0
        for deal_id, entry in fresh.items():
            if self._stop.is_set():
                break
            if entry.get("reminded") or now - entry.get("at", now) < limit:
                continue
            try:
                deal = self.account.get_deal(deal_id)
            except Exception as e:
                self.log.warning(f"Напоминание по сделке {deal_id} отложено: {short_error(e)}")
                continue
            if getattr(deal, "status", None) != ItemDealStatuses.SENT:
                self.forget_sent(deal_id)
                continue
            if getattr(deal, "has_problem", False):
                continue
            text = render(reminder.get("text", ""), {**entry.get("values", {}), "seller": self.label})
            if not text.strip() or not self._set_reminded(deal_id, True):
                continue
            if self.send_message(entry["chat_id"], text):
                self.log.info(f"⏰ Напоминание о подтверждении по сделке {deal_id}")
                sent += 1
            else:
                self._set_reminded(deal_id, False)
        return sent

    def should_greet(self, chat_id: str) -> bool:
        last = self.greeted.get(chat_id)
        return last is None or time.time() - last > self.number("greetingsCooldown") * 86400

    def mark_greeted(self, chat_id: str, timestamp: float | None = None) -> None:
        with self._lock:
            self.greeted[chat_id] = timestamp or time.time()
            self.storage.save_cache("greeted.json", self.greeted)

    def buyer_of_chat(self, chat):
        if not chat or not getattr(chat, "users", None):
            return None
        my_id = getattr(self.account, "id", None)
        return next((u for u in chat.users if u.id != my_id), None)

    def _with_watermark(self, text: str | None) -> str | None:
        watermark = render(self.settings["texts"].get("watermark", ""), {"seller": self.label}).strip()
        return f"{watermark}\n{text}" if text and watermark else text

    def _mirror(self, chat_id: str, message, text: str | None, images: list | None, who: str | None) -> None:
        sync = getattr(getattr(self.hub, "telegram", None), "sync", None)
        if sync is None or who is None:
            return
        try:
            sync.mirror_own(self, chat_id, getattr(message, "id", None), getattr(message, "text", None) or text,
                            bool(images), who)
        except Exception:
            self.log.exception("Не удалось показать своё сообщение в теме группы")

    def send_once(self, chat_id: str, text: str | None = None, images: list | None = None,
                  mirror: str | None = None) -> SendResult:
        text = self._with_watermark(text)
        try:
            message = self.account.send_message(chat_id, text, images=images or [], retries=1)
        except RequestPlayerokError as e:
            self.log.error(f"Playerok отклонил сообщение в чат {chat_id}: {e}")
            return SendResult("rejected", None, short_error(e))
        except Exception as e:
            self.log.warning(f"Неясно, дошло ли сообщение в чат {chat_id}: {e}")
            return SendResult("unclear", None, short_error(e))
        self._mirror(chat_id, message, text, images, mirror)
        return SendResult("sent", getattr(message, "id", None), "")

    def send_message(self, chat_id: str, text: str | None = None, images: list | None = None,
                     mirror: str | None = "bot") -> bool:
        text = self._with_watermark(text)
        for attempt in range(3):
            try:
                message = self.account.send_message(chat_id, text, images=images or [])
                self._mirror(chat_id, message, text, images, mirror)
                return True
            except RequestPlayerokError as e:
                self.log.error(f"Playerok отклонил сообщение в чат {chat_id}: {e}")
                return False
            except Exception as e:
                self.log.warning(f"Не удалось отправить сообщение в чат {chat_id} (попытка {attempt + 1}/3): {e}")
                time.sleep(2)
        return False

    def notify(self, text: str, keyboard=None, kind: str | None = None) -> None:
        try:
            self.hub.notify(text, keyboard, account=self, kind=kind)
        except Exception:
            self.log.exception("Не удалось отправить уведомление")

    def run_handlers(self, bind: str, *args) -> None:
        for func in self.hub.plugins.handlers(bind):
            uuid = getattr(func, "plugin_uuid", None)
            if uuid is not None and (uuid not in self.plugins or not is_enabled(uuid, self.settings)):
                continue
            try:
                func(*args)
            except Exception:
                owner = self.plugins[uuid].name if uuid in self.plugins else "встроенный"
                self.log.exception(f"Ошибка в хэндлере {getattr(func, '__name__', func)} ({owner})")

    def toggle_plugin(self, uuid: str) -> bool:
        value = not is_enabled(uuid, self.settings)
        self.update_settings(lambda s: s["plugins"].__setitem__(uuid, value))
        return value

    def delete_plugin(self, uuid: str) -> None:
        self.hub.plugins.delete(uuid, self.hub)

    def add_telegram_commands(self, uuid: str, commands: list[tuple[str, str, bool]]) -> None:
        self.hub.add_telegram_commands(uuid, commands)

    def load_plugin_data(self, plugin: str, default):
        return self.storage.load_plugin_data(plugin, default)

    def save_plugin_data(self, plugin: str, data) -> None:
        self.storage.save_plugin_data(plugin, data)

    def start(self) -> None:
        if self._stop.is_set():
            self.status = "stopped"
            return
        self.status = "starting"
        self.error = ""
        if self.account is None:
            self.account = self.hub.create_account(self.id)
        self.account.get()
        if self._stop.is_set():
            self.status = "stopped"
            return
        if getattr(self.account, "username", None):
            self.info.username = self.account.username
        self.status = "running"
        self.start_time = time.time()
        profile = getattr(self.account, "profile", None)
        balance = getattr(getattr(profile, "balance", None), "available", None)
        self.log.info("Аккаунт запущен" + (f", баланс {balance} ₽" if balance is not None else ""))
        self._spawn(self._periodic_loop, "periodic")
        self._spawn(self._listen_loop, "listener")
        self._spawn(self.stats_on_start, "stats")

    def _spawn(self, target: Callable, name: str) -> None:
        thread = threading.Thread(target=target, daemon=True, name=f"{self.id}-{name}")
        self._threads.append(thread)
        thread.start()

    def _listen_loop(self) -> None:
        if self.listener_factory is None:
            from playerokapi.listener.listener import EventListener
            self.listener_factory = EventListener
        while not self._stop.is_set():
            try:
                self.listener = self.listener_factory(self.account)
                for event in self.listener.listen():
                    if self._stop.is_set():
                        break
                    self.process_event(event)
            except Exception:
                self.log.exception("Слушатель упал, перезапускаю через 10 секунд")
            self._stop.wait(10)

    def stop(self, timeout: float = 30) -> None:
        self._stop.set()
        deadline = time.time() + timeout
        listener = self.listener
        if listener is not None and hasattr(listener, "stop"):
            try:
                listener.stop(max(0.0, deadline - time.time()))
            except Exception:
                self.log.exception("Ошибка остановки слушателя")
        for thread in list(self._threads):
            if thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.time()))
        self._threads = [t for t in self._threads if t.is_alive()]
        if self.status != "error":
            self.status = "stopped"

    def process_event(self, event) -> None:
        bind = EVENT_BINDS.get(event.type)
        if bind:
            self.run_handlers(bind, self, event)

    def check_health(self) -> bool:
        try:
            self.account.get()
            return True
        except Exception as e:
            text = fatal_text(e)
            if text is None:
                self.log.warning(f"Проверка связи с Playerok не удалась: {short_error(e)}")
                return True
            self.status = "error"
            self.error = text
            self._stop.set()
            listener = self.listener
            if listener is not None and hasattr(listener, "stop"):
                try:
                    listener.stop(5)
                except Exception:
                    self.log.exception("Ошибка остановки слушателя")
            notify_error = getattr(self.hub, "notify_error", None)
            if notify_error:
                notify_error(self)
            return False

    def _periodic_loop(self) -> None:
        last_restore = 0.0
        last_raise = 0.0
        last_health = time.time()
        last_reminder = 0.0
        last_withdraw = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now - last_health >= HEALTH_INTERVAL:
                last_health = now
                if not self.check_health():
                    return
            try:
                if now - last_reminder >= REMINDER_INTERVAL:
                    last_reminder = now
                    self.check_reminders(now)
                if now - last_withdraw >= WITHDRAW_INTERVAL:
                    last_withdraw = now
                    withdrawals.check_auto(self, now)
                if self.is_on("autoRestoreExpired") and now - last_restore >= self.number("restoreCheckInterval") * 60:
                    last_restore = now
                    self.restore_expired_items()
                if self.is_on("autoRaise") and now - last_raise >= 60:
                    last_raise = now
                    self.raise_items()
            except Exception:
                self.log.exception("Ошибка в фоновой задаче")
            self._stop.wait(30)

    def my_items(self, statuses: list) -> list:
        items, cursor = [], None
        for _ in range(20):
            page = self.account.get_my_items(statuses=statuses, count=24, after_cursor=cursor)
            items.extend(page.items)
            if not page.page_info.has_next_page:
                break
            cursor = page.page_info.end_cursor
            time.sleep(1)
        return items

    def has_stock_for(self, item_name: str | None) -> bool:
        entry = self.find_auto_delivery(item_name)
        if not entry:
            return True
        products_file = entry.get("products_file", "").strip()
        return not products_file or self.storage.count_products(products_file) > 0

    def live_lot(self, name: str, statuses=LIVE_STATUSES):
        wanted = (name or "").strip().lower()
        if not wanted:
            return None
        return next((item for item in self.my_items(list(statuses))
                     if (getattr(item, "name", "") or "").strip().lower() == wanted), None)

    def restore_sold(self, deal) -> str:
        name = getattr(deal.item, "name", "") or ""
        try:
            item = self.account.get_item(deal.item.id)
        except RequestPlayerokError as e:
            if not is_not_found(e):
                raise
            item = None
        if item is not None and getattr(item, "status", None) in LIVE_STATUSES:
            self.log.info(f"«{tools.short(item.name)}» после продажи остался в продаже")
            return "kept"
        if item is None:
            for wait in (0, KEEP_RELIST_WAIT):
                time.sleep(wait)
                if self.live_lot(name) is not None:
                    self.log.info(f"«{tools.short(name)}» Playerok оставил в продаже сам")
                    return "kept"
            sold = self.live_lot(name, (ItemStatuses.SOLD,))
            if sold is not None:
                return "restored" if self.restore_item(sold.id, "продан") else "skipped"
            self.log.warning(f"«{tools.short(name)}» не восстановлен: Playerok не отдаёт проданный товар")
            self.notify(f"⚠️ Товар <b>{escape(tools.short(name, 60))}</b> не восстановлен (продан): Playerok не "
                        f"отдаёт проданный товар, а такого лота в продаже нет. Выставьте его вручную.",
                        kind="restore")
            return "failed"
        return "restored" if self.restore_item(item.id, "продан", item=item) else "skipped"

    def keep_in_sale_report(self) -> dict:
        report = {"kept": [], "bot": [], "unknown": []}
        fails = 0
        for lot in self.my_items([ItemStatuses.APPROVED]):
            if fails >= KEEP_REPORT_FAILS:
                report["unknown"].append(lot.name or "?")
                continue
            try:
                keep = getattr(self.account.get_item(lot.id), "keep_in_sale", None)
                fails = 0
            except FATAL_ERRORS:
                raise
            except Exception:
                keep = None
                fails += 1
            report["unknown" if keep is None else "kept" if keep else "bot"].append(lot.name or "?")
            time.sleep(0.5)
        return report

    def restore_item(self, item_id: str, reason: str, item=None) -> bool:
        item = item if item is not None else self.account.get_item(item_id)
        name = tools.short(item.name)
        if not self.has_stock_for(item.name):
            self.log.info(f"«{name}» не восстановлен: закончились товары для автовыдачи")
            return False
        time.sleep(1)
        statuses = self.account.get_item_priority_statuses(item.id, item.raw_price)
        free = next((s for s in statuses if s.type == PriorityTypes.DEFAULT or s.price == 0), None)
        if not free or free.price > 0:
            self.log.warning(f"«{name}» не восстановлен: Playerok не даёт бесплатный статус")
            self.notify(f"⚠️ Товар <b>{tools.short(item.name, 60)}</b> не восстановлен ({reason}): "
                        f"бесплатный статус недоступен. Выставьте его вручную.", kind="restore")
            return False
        time.sleep(1)
        self.account.publish_item(item.id, free.id)
        self.log.info(f"«{name}» восстановлен ({reason})")
        self.run_handlers("BIND_TO_POST_RESTORE", self, item)
        self.notify(f"♻️ Товар <b>{tools.short(item.name, 60)}</b> снова выставлен ({reason})", kind="restore")
        return True

    def restore_expired_items(self) -> None:
        for item in self.my_items([ItemStatuses.EXPIRED]):
            if self._stop.is_set():
                return
            try:
                self.restore_item(item.id, "истёк срок")
            except Exception as e:
                self.log.error(f"Ошибка восстановления «{tools.short(item.name)}»: {e}")
            time.sleep(2)

    def _raise_today(self) -> dict:
        today = date.today().isoformat()
        if self.raise_state.get("date") != today:
            self.raise_state = {"date": today, "spent": 0, "last": self.raise_state.get("last", {}),
                                "limit_notified": False}
        return self.raise_state

    def raise_filter(self, item_name: str) -> bool:
        patterns = [p.strip().lower() for p in self.settings["raise_items"].split(",") if p.strip()]
        return not patterns or any(p in (item_name or "").lower() for p in patterns)

    def raise_items(self) -> None:
        state = self._raise_today()
        interval = self.number("raiseInterval") * 60
        max_price = self.number("raiseMaxPrice")
        limit = self.number("raiseDailyLimit")
        now = time.time()
        for item in self.my_items([ItemStatuses.APPROVED]):
            if self._stop.is_set() or not self.is_on("autoRaise"):
                return
            item_interval, item_max = interval, max_price
            try:
                profile = self.profile_for(item.name, getattr(item, "price", None), item.id, strict=True)
            except PlacesUnknown:
                continue
            mode = profile["raise"].get("mode", "default") if profile else "default"
            if mode == "off":
                continue
            if mode == "custom":
                item_interval = self.clip_number("raiseInterval", profile["raise"].get("interval")) * 60
                item_max = self.clip_number("raiseMaxPrice", profile["raise"].get("max_price"))
            elif not self.raise_filter(item.name):
                continue
            if now - state["last"].get(item.id, 0) < item_interval:
                continue
            statuses = self.account.get_item_priority_statuses(item.id, item.raw_price)
            premium = next((s for s in statuses if s.type == PriorityTypes.PREMIUM or s.price > 0), None)
            if not premium:
                continue
            if premium.price > item_max:
                self.log.info(f"«{tools.short(item.name)}» не поднят: цена {premium.price} ₽ выше лимита {item_max:g} ₽")
                state["last"][item.id] = now
                continue
            if state["spent"] + premium.price > limit:
                if not state.get("limit_notified"):
                    state["limit_notified"] = True
                    self.notify(f"⏸ Дневной лимит на поднятие исчерпан: {state['spent']:g} из {limit:g} ₽",
                                kind="raise")
                self.storage.save_cache("raise.json", state)
                return
            self.run_handlers("BIND_TO_PRE_RAISE", self, item)
            state["spent"] += premium.price
            state["last"][item.id] = now
            self.storage.save_cache("raise.json", state)
            try:
                self.account.increase_item_priority_status(item.id, premium.id)
            except RequestPlayerokError as e:
                state["spent"] -= premium.price
                self.storage.save_cache("raise.json", state)
                self.log.error(f"«{tools.short(item.name)}» не поднят: {e}")
                continue
            self.log.info(f"«{tools.short(item.name)}» поднят за {premium.price} ₽")
            self.run_handlers("BIND_TO_POST_RAISE", self, item, premium.price)
            self.notify(f"⬆️ Поднят <b>{tools.short(item.name, 60)}</b> за {premium.price} ₽ "
                        f"(сегодня потрачено {state['spent']:g} из {limit:g} ₽)", kind="raise")
            time.sleep(2)
