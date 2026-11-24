from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime
from html import escape
from typing import TYPE_CHECKING, NamedTuple

from Utils.errors import short_error
from playerokapi.enums import (TransactionOperations, TransactionPaymentMethodIds, TransactionProviderDirections,
                               TransactionProviderIds, TransactionStatuses)
from playerokapi.exceptions import RequestPlayerokError

if TYPE_CHECKING:
    from cardinal import Cardinal

STATE_FILE = "withdraw.json"
RETRY_AFTER = 3600
QUIET_SECONDS = 86400
SUPPORTED = ("BANK_CARD_RU", "BANK_CARD_BY", "BANK_CARD", "SBP", "USDT")
CARD_NAMES = {"MIR": "МИР", "VISA": "VISA", "MASTERCARD": "Mastercard"}
BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
FINISHED = (TransactionStatuses.CONFIRMED, TransactionStatuses.ROLLED_BACK, TransactionStatuses.FAILED)
SERVER_CODES = {"INTERNAL_SERVER_ERROR", "SERVICE_UNAVAILABLE", "GATEWAY_TIMEOUT", "TIMEOUT", "UNKNOWN"}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class WithdrawResult(NamedTuple):
    status: str
    amount: int
    message: str


def lock_for(acc_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(acc_id, threading.Lock())


def mask_card(first_six: str | None, last_four: str | None, card_type) -> str:
    name = CARD_NAMES.get(getattr(card_type, "name", str(card_type or "")), "Карта")
    return f"{name} •••• {last_four or '????'}"


def mask_phone(phone: str) -> str:
    return f"+7•••{phone[-4:]}"


def mask_usdt(address: str) -> str:
    return f"{address[:4]}…{address[-4:]}"


def normalize_phone(text: str | None) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    return None


def valid_usdt(address: str | None) -> bool:
    address = address or ""
    return len(address) == 34 and address.startswith("T") and set(address) <= BASE58


def amount_for(withdrawable: int | None, keep: int, daily_left: int, low: int | None, high: int | None) -> int:
    if withdrawable is None:
        return 0
    amount = min(int(withdrawable) - int(keep), int(daily_left))
    if high:
        amount = min(amount, int(high))
    if amount <= 0 or (low and amount < int(low)):
        return 0
    return amount


def config(c: Cardinal) -> dict:
    return c.settings["withdraw"]


def load_state(c: Cardinal, now: float) -> dict:
    today = date.fromtimestamp(now).isoformat()
    with c.storage.lock:
        state = c.storage.load_cache(STATE_FILE, {})
    if state.get("date") != today:
        state.update(date=today, withdrawn=0)
    return state


def save_state(c: Cardinal, state: dict) -> None:
    with c.storage.lock:
        c.storage.save_cache(STATE_FILE, state)


def reset_verification(c: Cardinal) -> None:
    state = load_state(c, time.time())
    if state.pop("manual_created", None) is not None:
        save_state(c, state)


def balance(c: Cardinal) -> int | None:
    c.account.get()
    b = getattr(getattr(c.account, "profile", None), "balance", None)
    value = getattr(b, "withdrawable", None)
    return value if value is not None else getattr(b, "available", None)


def provider_limits(c: Cardinal, provider: str) -> tuple[int | None, int | None]:
    try:
        providers = c.account.get_transaction_providers(direction=TransactionProviderDirections.OUT)
    except Exception as e:
        c.log.warning(f"Вывод: не удалось получить лимиты: {short_error(e)}")
        return None, None
    found = next((p for p in providers if getattr(getattr(p, "id", None), "name", None) == provider), None)
    limits = getattr(getattr(found, "limits", None), "outgoing", None)
    return getattr(limits, "min", None), getattr(limits, "max", None)


def has_pending(c: Cardinal) -> bool:
    page = c.account.get_transactions(operation=TransactionOperations.WITHDRAW, count=24)
    items = getattr(page, "transactions", None)
    if items is None:
        raise RuntimeError("Playerok не вернул список выводов")
    return any(getattr(t, "status", None) not in FINISHED for t in items)


def is_server_error(e: Exception) -> bool:
    return str(getattr(e, "error_code", "") or "").upper() in SERVER_CODES


def pause(c: Cardinal, reason: str) -> None:
    c.update_settings(lambda s: s["withdraw"].__setitem__("paused", reason))


def unclear(c: Cardinal, state: dict, amount: int, label: str, reason: str) -> WithdrawResult:
    state["withdrawn"] = state.get("withdrawn", 0) + amount
    save_state(c, state)
    pause(c, reason)
    c.log.warning(f"Вывод {amount} ₽: неясный ответ, автовывод на паузе: {reason}")
    c.notify(f"⚠️ Не знаю, создан ли вывод {amount} ₽ на {escape(label)} — проверьте на сайте. "
             f"Автовывод на паузе, снять её можно в «💸 Вывод».")
    return WithdrawResult("unclear", amount, reason)


def withdraw(c: Cardinal, amount: int, manual: bool = False, now: float | None = None) -> WithdrawResult:
    now = now if now is not None else time.time()
    lock = lock_for(c.id)
    if not lock.acquire(blocking=False):
        return WithdrawResult("skipped", 0, "Вывод уже выполняется")
    try:
        settings = config(c)
        if not manual and (not settings.get("enabled") or not settings.get("verified")):
            return WithdrawResult("skipped", 0, "Автовывод выключен")
        method = c.hub.withdraw_method(c.id)
        if not method.get("provider") or not method.get("account"):
            return WithdrawResult("skipped", 0, "Реквизиты не заданы")
        if config(c).get("paused"):
            return WithdrawResult("skipped", 0, "Вывод на паузе")
        if amount <= 0:
            return WithdrawResult("skipped", 0, "Нечего выводить")
        try:
            if has_pending(c):
                return WithdrawResult("skipped", 0, "Уже есть вывод в обработке — дождитесь его")
        except Exception as e:
            return WithdrawResult("skipped", 0, f"Не удалось проверить прошлые выводы: {short_error(e)}")
        label = method.get("label") or "реквизиты"
        provider = TransactionProviderIds[method["provider"]]
        payment = TransactionPaymentMethodIds[method["payment_method"]] if method.get("payment_method") else None
        state = load_state(c, now)
        try:
            c.account.request_withdrawal(provider, method["account"], amount, payment, method.get("bank") or None,
                                         retries=1)
        except RequestPlayerokError as e:
            if is_server_error(e):
                return unclear(c, state, amount, label, short_error(e))
            reason = short_error(e)
            c.log.warning(f"Вывод {amount} ₽ отклонён: {reason}")
            state.update(last_error_at=now)
            quiet = state.get("last_error") == reason and now - state.get("last_error_notified_at", 0) < QUIET_SECONDS
            if manual or not quiet:
                state.update(last_error=reason, last_error_notified_at=now)
                if not manual:
                    c.notify(f"❌ Автовывод {amount} ₽ отклонён Playerok: {escape(reason)}")
            save_state(c, state)
            return WithdrawResult("rejected", amount, reason)
        except Exception as e:
            return unclear(c, state, amount, label, short_error(e))
        state["withdrawn"] = state.get("withdrawn", 0) + amount
        state.pop("last_error", None)
        if manual:
            state["manual_created"] = now
        save_state(c, state)
        c.log.info(f"Создан вывод {amount} ₽ на {label}")
        c.notify(f"💸 Вывод {amount} ₽ на {escape(label)} создан" + (" (вручную)" if manual else ""))
        return WithdrawResult("created", amount, "")
    finally:
        lock.release()


def due_daily(cfg: dict, now: float) -> bool:
    try:
        hour, minute = (int(part) for part in str(cfg.get("time", "12:00")).split(":"))
    except ValueError:
        hour, minute = 12, 0
    moment = datetime.fromtimestamp(now)
    return (moment.hour, moment.minute) >= (hour, minute)


def check_auto(c: Cardinal, now: float | None = None) -> WithdrawResult | None:
    now = now if now is not None else time.time()
    cfg = config(c)
    if not cfg.get("enabled") or not cfg.get("verified") or cfg.get("paused"):
        return None
    state = load_state(c, now)
    if now - state.get("last_error_at", 0) < RETRY_AFTER:
        return None
    daily = cfg.get("mode") == "daily"
    if daily and (state.get("daily_done") == state["date"] or not due_daily(cfg, now)):
        return None
    try:
        available = balance(c)
    except Exception as e:
        c.log.warning(f"Вывод: не удалось получить баланс: {short_error(e)}")
        return None
    if not daily and (available is None or available < int(cfg.get("threshold", 0))):
        return None
    method = c.hub.withdraw_method(c.id)
    low, high = provider_limits(c, method.get("provider", ""))
    daily_left = int(cfg.get("daily_limit", 0)) - int(state.get("withdrawn", 0))
    amount = amount_for(available, int(cfg.get("keep", 0)), daily_left, low, high)
    if amount <= 0:
        return None
    result = withdraw(c, amount, now=now)
    if daily and result.status == "created":
        state = load_state(c, now)
        state["daily_done"] = state["date"]
        save_state(c, state)
    return result
