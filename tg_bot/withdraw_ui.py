from __future__ import annotations

import re
import time
from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import withdraw as wd
from Utils.errors import short_error
from playerokapi.enums import TransactionProviderDirections
from tg_bot import keyboards as kbs

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

MODES = {"threshold": "при пороге", "daily": "раз в день"}
NUMBER_FIELDS = {"threshold": "threshold", "keep": "keep", "limit": "daily_limit"}
FIELD_PROMPTS = {
    "threshold": "💰 Порог: выводить, когда доступно не меньше этой суммы, ₽.",
    "keep": "🏦 Сколько оставлять на балансе (запас на комиссию), ₽.",
    "limit": "📅 Максимум вывода в сутки, ₽.",
    "time": "⏰ Время вывода в режиме «раз в день», ЧЧ:ММ, например <code>10:30</code>.",
}
CARD_PROVIDERS = ("BANK_CARD_RU", "BANK_CARD_BY", "BANK_CARD")
BANKS_SHOWN = 10
LATEST_DAILY = (23, 49)

_drafts: dict[int, dict] = {}


def cfg(c: Cardinal) -> dict:
    return c.settings["withdraw"]


def draft(user_id: int, acc_id: str) -> dict:
    d = _drafts.get(user_id)
    if d is None or d.get("acc") != acc_id:
        d = _drafts[user_id] = {"acc": acc_id}
    return d


def available_text(c: Cardinal) -> str:
    try:
        value = wd.balance(c)
    except Exception as e:
        return f"? ({escape(short_error(e))})"
    return "?" if value is None else f"{value} ₽"


def screen_text(tg: TGBot, c: Cardinal) -> str:
    s = cfg(c)
    method = tg.hub.withdraw_method(c.id)
    mode = (f"при пороге от {s['threshold']} ₽" if s["mode"] == "threshold"
            else f"раз в день после {escape(str(s['time']))}")
    lines = [f"💸 <b>Вывод — {escape(c.label)}</b>", "",
             f"Доступно к выводу: {available_text(c)}",
             f"Реквизиты: {escape(method.get('label') or 'не заданы')}",
             f"Режим: {mode}", f"Оставлять на балансе: {s['keep']} ₽ · лимит в сутки: {s['daily_limit']} ₽",
             f"🕒 Время сервера: {time.strftime('%H:%M')} (по нему считается «раз в день»)", "",
             f"{kbs.dot(s['enabled'])} Автовывод {'включён' if s['enabled'] else 'выключен'}",
             "✅ Реквизиты проверены" if s["verified"]
             else "⚠️ Реквизиты не проверены — сделайте ручной вывод и подтвердите, что деньги пришли"]
    if s["paused"]:
        lines.append(f"⏸ <b>На паузе</b>: {escape(s['paused'])}. Проверьте вывод на сайте, потом снимите паузу.")
    return "\n".join(lines)


def screen_kb(c: Cardinal) -> K:
    s = cfg(c)
    kb = K()
    kb.row(B("💳 Реквизиты", callback_data=kbs.cb("wdp", c.id)),
           B("💸 Вывести сейчас", callback_data=kbs.cb("wdn", c.id)))
    kb.row(*[B(("• " if s["mode"] == mode else "") + title.capitalize(), callback_data=kbs.cb("wdm", c.id, mode))
             for mode, title in MODES.items()])
    kb.row(B("💰 Порог", callback_data=kbs.cb("wde", c.id, "threshold")),
           B("🏦 Оставлять", callback_data=kbs.cb("wde", c.id, "keep")))
    kb.row(B("⏰ Время", callback_data=kbs.cb("wde", c.id, "time")),
           B("📅 Лимит в сутки", callback_data=kbs.cb("wde", c.id, "limit")))
    kb.row(B(f"{kbs.dot(s['enabled'])} Автовывод", callback_data=kbs.cb("wdt", c.id)))
    if s["paused"]:
        kb.row(B("▶️ Снять паузу", callback_data=kbs.cb("wdu", c.id)))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def send_screen(tg: TGBot, chat_id: int, c: Cardinal, note: str = "") -> None:
    tg.bot.send_message(chat_id, (note + "\n\n" if note else "") + screen_text(tg, c), reply_markup=screen_kb(c))


def cb_screen(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, screen_text(tg, c), screen_kb(c))
    tg.answer(call)


def providers(c: Cardinal) -> list:
    found = c.account.get_transaction_providers(direction=TransactionProviderDirections.OUT)
    return [p for p in found if getattr(getattr(p, "id", None), "name", None) in wd.SUPPORTED]


def pname(p) -> str:
    return getattr(p, "name", None) or p.id.name


def drop(tg: TGBot, m: Message) -> None:
    try:
        tg.bot.delete_message(m.chat.id, m.message_id)
    except Exception:
        pass


def limits_text(p) -> str:
    limits = getattr(getattr(p, "limits", None), "outgoing", None)
    low, high = getattr(limits, "min", None), getattr(limits, "max", None)
    return f"комиссия: {getattr(p, 'fee', '?')}, от {low if low is not None else '?'} до {high if high is not None else '?'} ₽"


def cb_providers(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    try:
        found = providers(c)
    except Exception as e:
        tg.answer(call, f"Не удалось получить способы вывода: {short_error(e)}"[:190], alert=True)
        return
    kb = K()
    lines = ["💳 <b>Способ вывода</b>", "", "Комиссию показываю так, как её отдаёт Playerok.", ""]
    for p in found:
        lines.append(f"• <b>{escape(pname(p))}</b> — {escape(limits_text(p))}")
        kb.row(B(pname(p), callback_data=kbs.cb("wdp1", c.id, p.id.name)))
    kb.row(B("◀️ Назад", callback_data=kbs.cb("wd", c.id)))
    tg.edit(call, "\n".join(lines) if found else "Нет доступных способов вывода.", kb)
    tg.answer(call)


def save_method(tg: TGBot, chat_id: int, c: Cardinal, d: dict, account: str, label: str, bank: str = "") -> None:
    with wd.lock_for(c.id):
        c.update_settings(lambda s: s["withdraw"].update(verified=False, enabled=False))
        wd.reset_verification(c)
        tg.hub.set_withdraw_method(c.id, provider=d["provider"], account=account,
                                   payment_method=d.get("payment", ""), bank=bank, label=label)
    _drafts.pop(next((u for u, x in _drafts.items() if x is d), None), None)
    c.log.info(f"Вывод: заданы реквизиты {label}")
    send_screen(tg, chat_id, c, "✅ Реквизиты сохранены. Проверьте их ручным выводом небольшой суммы.")


def cb_provider(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    name = call.data.split(":")[2] if len(call.data.split(":")) > 2 else ""
    if name not in wd.SUPPORTED:
        tg.answer(call, "Нет такого способа", alert=True)
        return
    try:
        found = next((p for p in providers(c) if p.id.name == name), None)
    except Exception as e:
        tg.answer(call, f"Не удалось получить способы вывода: {short_error(e)}"[:190], alert=True)
        return
    if found is None:
        tg.answer(call, "Этот способ сейчас недоступен", alert=True)
        return
    d = draft(call.from_user.id, c.id)
    for key in ("cards", "banks", "phone", "confirm"):
        d.pop(key, None)
    valid = [m for m in getattr(found, "payment_methods", None) or [] if getattr(m, "id", None) is not None]
    d.update(provider=name, payment="", methods=[m.id.name for m in valid])
    tg.answer(call)
    if len(valid) > 1:
        kb = K()
        for n, method in enumerate(valid):
            kb.row(B(method.name or method.id.name, callback_data=kbs.cb("wdpm", c.id, n)))
        tg.bot.send_message(call.message.chat.id, "Выберите платёжный способ:", reply_markup=kb)
        return
    if d["methods"]:
        d["payment"] = d["methods"][0]
    continue_setup(tg, call.message.chat.id, call.from_user.id, c, d)


def cb_payment(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    d = draft(call.from_user.id, c.id)
    parts = call.data.split(":")
    index = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
    if not d.get("provider") or not 0 <= index < len(d.get("methods", [])):
        tg.answer(call, "Начните выбор реквизитов заново", alert=True)
        return
    d["payment"] = d["methods"][index]
    tg.answer(call)
    continue_setup(tg, call.message.chat.id, call.from_user.id, c, d)


def continue_setup(tg: TGBot, chat_id: int, user_id: int, c: Cardinal, d: dict) -> None:
    provider = d["provider"]
    if provider in CARD_PROVIDERS:
        ask_card(tg, chat_id, c, d)
    elif provider == "SBP":
        ask_phone(tg, chat_id, user_id, c, d)
    else:
        ask_usdt(tg, chat_id, user_id, c, d)


def ask_card(tg: TGBot, chat_id: int, c: Cardinal, d: dict) -> None:
    try:
        cards = list(c.account.get_verified_cards().bank_cards)
    except Exception as e:
        tg.bot.send_message(chat_id, f"❌ Не удалось получить карты: {escape(short_error(e))}")
        return
    if not cards:
        tg.bot.send_message(chat_id, "На Playerok нет привязанных карт. Привяжите карту на сайте и вернитесь сюда.",
                            reply_markup=kbs.back_kb(kbs.cb("wd", c.id)))
        return
    d["cards"] = {card.id: wd.mask_card(card.card_first_six, card.card_last_four, card.card_type) for card in cards}
    kb = K()
    for card_id, label in d["cards"].items():
        kb.row(B(label, callback_data=kbs.cb("wdc", c.id, card_id)))
    kb.row(B("◀️ Назад", callback_data=kbs.cb("wd", c.id)))
    tg.bot.send_message(chat_id, "💳 Выберите карту, привязанную на Playerok:", reply_markup=kb)


def cb_card(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    d = draft(call.from_user.id, c.id)
    card_id = call.data.split(":", 2)[2] if len(call.data.split(":")) > 2 else ""
    if d.get("provider") not in CARD_PROVIDERS or card_id not in (d.get("cards") or {}):
        tg.answer(call, "Начните выбор реквизитов заново", alert=True)
        return
    tg.answer(call)
    save_method(tg, call.message.chat.id, c, d, card_id, d["cards"][card_id])


def ask_phone(tg: TGBot, chat_id: int, user_id: int, c: Cardinal, d: dict) -> None:
    def on_first(m: Message):
        drop(tg, m)
        phone = wd.normalize_phone(m.text)
        if phone is None:
            tg.bot.send_message(m.chat.id, "❌ Нужен российский номер из 11 цифр, например 8 999 123 45 67")
            return

        def on_second(answer: Message):
            drop(tg, answer)
            if wd.normalize_phone(answer.text) != phone:
                tg.bot.send_message(answer.chat.id, "❌ Номера не совпали, реквизиты не сохранены")
                return
            d["phone"] = phone
            ask_bank(tg, answer.chat.id, answer.from_user.id, c, d)

        tg.ask(m.chat.id, m.from_user.id, "📱 Повторите номер телефона для проверки:", on_second)

    tg.ask(chat_id, user_id, "📱 Номер телефона для СБП (на него придут деньги). /cancel — отмена.", on_first)


def ask_bank(tg: TGBot, chat_id: int, user_id: int, c: Cardinal, d: dict) -> None:
    def on_query(m: Message):
        query = (m.text or "").strip().lower()
        try:
            banks = list(c.account.get_sbp_bank_members())
        except Exception as e:
            tg.bot.send_message(m.chat.id, f"❌ Не удалось получить список банков: {escape(short_error(e))}")
            return
        found = [b for b in banks if query and query in (b.name or "").lower()][:BANKS_SHOWN]
        if not found:
            tg.bot.send_message(m.chat.id, "❌ Банк не найден. Нажмите «💳 Реквизиты» и попробуйте ещё раз.",
                                reply_markup=kbs.back_kb(kbs.cb("wd", c.id)))
            return
        d["banks"] = [(b.id, b.name) for b in found]
        kb = K()
        for n, (_, name) in enumerate(d["banks"]):
            kb.row(B(name, callback_data=kbs.cb("wdb", c.id, n)))
        tg.bot.send_message(m.chat.id, "🏦 Выберите банк:", reply_markup=kb)

    tg.ask(chat_id, user_id, "🏦 Начало названия банка, например «Т-Банк» или «Сбер»:", on_query)


def cb_bank(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    d = draft(call.from_user.id, c.id)
    parts = call.data.split(":")
    index = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
    if d.get("provider") != "SBP" or not d.get("phone") or not 0 <= index < len(d.get("banks", [])):
        tg.answer(call, "Начните выбор реквизитов заново", alert=True)
        return
    bank_id, bank_name = d["banks"][index]
    tg.answer(call)
    save_method(tg, call.message.chat.id, c, d, d["phone"], f"СБП {bank_name} {wd.mask_phone(d['phone'])}", bank_id)


def ask_usdt(tg: TGBot, chat_id: int, user_id: int, c: Cardinal, d: dict) -> None:
    def on_first(m: Message):
        drop(tg, m)
        address = (m.text or "").strip()
        if not wd.valid_usdt(address):
            tg.bot.send_message(m.chat.id, "❌ Это не адрес USDT TRC20: он начинается с T и состоит из 34 символов")
            return

        def on_second(answer: Message):
            drop(tg, answer)
            if (answer.text or "").strip() != address:
                tg.bot.send_message(answer.chat.id, "❌ Адреса не совпали, реквизиты не сохранены")
                return
            save_method(tg, answer.chat.id, c, d, address, f"USDT {wd.mask_usdt(address)}")

        tg.ask(m.chat.id, m.from_user.id, "🔁 Повторите адрес для проверки:", on_second)

    tg.ask(chat_id, user_id, "🪙 Адрес кошелька USDT в сети TRC20. Ошибка в адресе = потеря денег. /cancel — отмена.",
           on_first)


def cb_mode(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    mode = call.data.split(":")[2] if len(call.data.split(":")) > 2 else ""
    if mode not in MODES:
        tg.answer(call, "Нет такого режима", alert=True)
        return
    c.update_settings(lambda s: s["withdraw"].__setitem__("mode", mode))
    tg.edit(call, screen_text(tg, c), screen_kb(c))
    tg.answer(call)


def parse_time(text: str | None) -> str | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (text or "").strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    if (int(match.group(1)), int(match.group(2))) > LATEST_DAILY:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def cb_edit(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    field = call.data.split(":")[2] if len(call.data.split(":")) > 2 else ""
    if field not in FIELD_PROMPTS:
        tg.answer(call, "Нет такого пункта", alert=True)
        return

    def on_answer(m: Message):
        text = (m.text or "").strip()
        if field == "time":
            value = parse_time(text)
            error = "Нужно время ЧЧ:ММ не позже 23:49, например 10:30"
        else:
            value = int(text) if text.isdigit() else None
            error = "Нужно целое число от 0"
        if value is None:
            tg.bot.send_message(m.chat.id, f"❌ {error}, ничего не изменено",
                                reply_markup=kbs.back_kb(kbs.cb("wd", c.id)))
            return
        key = NUMBER_FIELDS.get(field, field)
        c.update_settings(lambda s: s["withdraw"].__setitem__(key, value))
        send_screen(tg, m.chat.id, c, "✅ Сохранено")

    tg.ask(call.message.chat.id, call.from_user.id, f"{FIELD_PROMPTS[field]} /cancel — отмена.", on_answer)
    tg.answer(call)


def cb_now(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    method = tg.hub.withdraw_method(c.id)
    if not method.get("provider"):
        tg.answer(call, "Сначала задайте реквизиты", alert=True)
        return
    low, _ = wd.provider_limits(c, method["provider"])

    def on_amount(m: Message):
        text = (m.text or "").strip()
        amount = int(text) if text.isdigit() else 0
        try:
            available = wd.balance(c)
        except Exception as e:
            tg.bot.send_message(m.chat.id, f"❌ Не удалось получить баланс: {escape(short_error(e))}")
            return
        if amount <= 0 or available is None or amount > available or (low and amount < low):
            tg.bot.send_message(m.chat.id, f"❌ Нужна сумма от {low or 1} до {available if available is not None else '?'} ₽",
                                reply_markup=kbs.back_kb(kbs.cb("wd", c.id)))
            return
        current = tg.hub.withdraw_method(c.id)
        draft(m.from_user.id, c.id)["confirm"] = (amount, current.get("provider"), current.get("account"))
        kb = K()
        kb.row(B(f"✅ Вывести {amount} ₽", callback_data=kbs.cb("wdn_yes", c.id, amount)),
               B("❌ Нет", callback_data=kbs.cb("wd", c.id)))
        tg.bot.send_message(m.chat.id, f"💸 Вывести <b>{amount} ₽</b> на {escape(method.get('label') or 'реквизиты')}?",
                            reply_markup=kb)

    hint = f" Минимум провайдера: {low} ₽." if low else ""
    tg.ask(call.message.chat.id, call.from_user.id,
           f"💸 Сколько вывести, ₽? Для проверки реквизитов хватит небольшой суммы.{hint} /cancel — отмена.", on_amount)
    tg.answer(call)


def cb_now_yes(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    parts = call.data.split(":")
    amount = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    d = draft(call.from_user.id, c.id)
    current = tg.hub.withdraw_method(c.id)
    if amount <= 0 or d.get("confirm") != (amount, current.get("provider"), current.get("account")):
        tg.answer(call, "Кнопка устарела — начните вывод заново", alert=True)
        return
    d.pop("confirm", None)
    tg.answer(call, "Отправляю…")
    result = wd.withdraw(c, amount, manual=True)
    if result.status == "created":
        kb = K()
        kb.row(B("✅ Деньги пришли", callback_data=kbs.cb("wdv", c.id)))
        kb.row(B("◀️ К выводу", callback_data=kbs.cb("wd", c.id)))
        tg.edit(call, f"💸 Вывод {amount} ₽ создан. Когда деньги придут на реквизиты — нажмите «✅ Деньги пришли», "
                      f"после этого можно включить автовывод.", kb)
    elif result.status == "rejected":
        tg.edit(call, f"❌ Playerok отказал: {escape(result.message)}", kbs.back_kb(kbs.cb("wd", c.id)))
    elif result.status == "unclear":
        tg.edit(call, f"⚠️ Не знаю, создан ли вывод — проверьте на сайте. Вывод на паузе: {escape(result.message)}",
                kbs.back_kb(kbs.cb("wd", c.id)))
    else:
        tg.edit(call, f"⏸ {escape(result.message)}", kbs.back_kb(kbs.cb("wd", c.id)))


def cb_verify(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    if not wd.load_state(c, time.time()).get("manual_created"):
        tg.answer(call, "Сначала сделайте ручной вывод", alert=True)
        return
    c.update_settings(lambda s: s["withdraw"].__setitem__("verified", True))
    c.log.info("Вывод: реквизиты подтверждены владельцем")
    tg.edit(call, screen_text(tg, c), screen_kb(c))
    tg.answer(call, "Реквизиты проверены")


def cb_toggle(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    if not cfg(c)["enabled"] and not cfg(c)["verified"]:
        tg.answer(call, "Сначала проверьте реквизиты ручным выводом", alert=True)
        return
    with wd.lock_for(c.id):
        c.update_settings(lambda s: s["withdraw"].__setitem__("enabled", not s["withdraw"]["enabled"]))
    tg.edit(call, screen_text(tg, c), screen_kb(c))
    tg.answer(call)


def cb_unpause(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    c.update_settings(lambda s: s["withdraw"].__setitem__("paused", ""))
    tg.edit(call, screen_text(tg, c), screen_kb(c))
    tg.answer(call, "Пауза снята")


def register(tg: TGBot):
    for prefix, handler in {"wd:": cb_screen, "wdp:": cb_providers, "wdp1:": cb_provider, "wdpm:": cb_payment,
                            "wdc:": cb_card, "wdb:": cb_bank, "wdm:": cb_mode, "wde:": cb_edit, "wdn:": cb_now,
                            "wdn_yes:": cb_now_yes, "wdv:": cb_verify, "wdt:": cb_toggle, "wdu:": cb_unpause}.items():
        tg.on_prefix(prefix, handler)
