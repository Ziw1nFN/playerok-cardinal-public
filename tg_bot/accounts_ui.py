from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from accounts import AccountError
from Utils import tools
from Utils.errors import short_error
from tg_bot import keyboards as kbs
from tg_bot import misc_ui
from tg_bot.access import ANY

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

TOKEN_PROMPT = ("🔑 Пришлите токен Playerok (cookie <code>token</code>) или всю строку cookies.\n"
                "Сообщение с токеном я сразу удалю. /cancel — отмена.")
PROXY_PROMPT = "🧦 Пришлите прокси в формате <code>ip:port</code> или <code>user:pass@ip:port</code>."


def mask_proxy(proxy: str) -> str:
    if not proxy:
        return "нет"
    if "@" in proxy:
        auth, host = proxy.rsplit("@", 1)
        return f"{auth.split(':', 1)[0]}:***@{host}"
    return proxy


def split_secret(text: str) -> dict:
    value = text.strip()
    if ";" in value or "token=" in value:
        return {"cookies": value, "token": ""}
    return {"token": value, "cookies": ""}


def _drop_message(tg: TGBot, m: Message):
    try:
        tg.bot.delete_message(m.chat.id, m.message_id)
    except Exception:
        pass


def visible_accounts(tg: TGBot, user_id: int | None = None) -> list[Cardinal]:
    hub = tg.hub
    return [hub.cardinals[info.id] for info in hub.registry.accounts if info.id in hub.cardinals
            and (user_id is None or tg.can(user_id, ANY, info.id))]


def accounts_text(tg: TGBot, user_id: int | None = None) -> str:
    hub = tg.hub
    if not hub.cardinals:
        return "👥 <b>Аккаунты</b>\n\nПока пусто. Нажмите «➕ Добавить аккаунт»."
    lines = [misc_ui.account_line(c) for c in visible_accounts(tg, user_id)]
    if user_id is not None and not tg.is_owner(user_id):
        return "👥 <b>Аккаунты</b>\n\n" + "\n".join(lines)
    return f"👥 <b>Аккаунты</b> ({len(lines)} из {hub.registry.max_accounts})\n\n" + "\n".join(lines)


def accounts_kb(tg: TGBot, user_id: int | None = None) -> K:
    kb = K()
    for c in visible_accounts(tg, user_id):
        row = [B(f"{kbs.status_icon(c)} {tools.short(c.label, 26)}", callback_data=kbs.cb("accsel", c.id))]
        if user_id is None or tg.can_call(user_id, kbs.cb("acc", c.id)):
            row.append(B("⚙️", callback_data=kbs.cb("acc", c.id)))
        kb.row(*row)
    if user_id is None or tg.is_owner(user_id):
        kb.row(B("➕ Добавить аккаунт", callback_data="accadd"))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def card_text(tg: TGBot, c: Cardinal, note: str = "") -> str:
    creds = tg.hub.credentials(c.id)
    lines = [f"👤 <b>{escape(c.label)}</b> (<code>{c.id}</code>)", f"Статус: {misc_ui.account_line(c)}"]
    profile = getattr(c.account, "profile", None)
    balance = getattr(getattr(profile, "balance", None), "available", None)
    if balance is not None:
        lines.append(f"💰 Баланс: {balance} ₽")
    lines.append(f"🧦 Прокси: <code>{escape(mask_proxy(creds.get('proxy', '')))}</code>")
    lines.append(f"🌐 User-Agent: {'свой' if creds.get('user_agent') else 'стандартный'}")
    lines.append(f"🔑 Вход: {'строка cookies' if creds.get('cookies') else 'токен'}")
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines)


def card_kb(c: Cardinal) -> K:
    kb = K()
    if c.settings_error:
        kb.row(B("♻️ Сбросить настройки", callback_data=kbs.cb("accreset", c.id)))
    kb.row(B("🔄 Проверить вход", callback_data=kbs.cb("acccheck", c.id)))
    kb.row(B("🧦 Прокси", callback_data=kbs.cb("accproxy", c.id)), B("🚫 Убрать прокси", callback_data=kbs.cb("accproxy_rm", c.id)))
    kb.row(B("🔑 Токен", callback_data=kbs.cb("acctoken", c.id)), B("🌐 User-Agent", callback_data=kbs.cb("accua", c.id)))
    kb.row(B("▶️ Включить" if c.status == "disabled" else "⏸ Выключить", callback_data=kbs.cb("acctgl", c.id)))
    kb.row(B("🗑 Убрать аккаунт", callback_data=kbs.cb("accdel", c.id)))
    kb.row(B("◀️ К аккаунтам", callback_data="accs"))
    return kb


def send_card(tg: TGBot, chat_id: int, acc_id: str, note: str = ""):
    c = tg.hub.cardinals.get(acc_id)
    if c is None:
        tg.bot.send_message(chat_id, note or "Аккаунт не найден", reply_markup=kbs.back_kb("accs"))
        return
    tg.bot.send_message(chat_id, card_text(tg, c, note), reply_markup=card_kb(c), disable_web_page_preview=True)


def cb_list(tg: TGBot, call: CallbackQuery):
    tg.edit(call, accounts_text(tg, call.from_user.id), accounts_kb(tg, call.from_user.id))
    tg.answer(call)


def cb_select(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.select(call.from_user.id, c.id)
    tg.edit(call, misc_ui.menu_text(tg, c), tg.menu_markup(call.from_user.id, c))
    tg.answer(call, f"Выбран {c.label}")


def cb_card(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, card_text(tg, c), card_kb(c))
    tg.answer(call)


def _finish_add(tg: TGBot, chat_id: int, user_id: int, proxy: str):
    flow = tg.flows.pop(user_id, None)
    if not flow:
        tg.bot.send_message(chat_id, "Начните заново: ➕ Добавить аккаунт", reply_markup=kbs.back_kb("accs"))
        return
    try:
        c = tg.hub.add_account(token=flow["token"], cookies=flow["cookies"], proxy=proxy)
    except AccountError as e:
        if "Прокси" in e.text:
            tg.flows[user_id] = flow
            _ask_proxy_for_new(tg, chat_id, user_id, f"❌ {e.text}\n\n")
            return
        tg.bot.send_message(chat_id, f"❌ {escape(e.text)}", reply_markup=kbs.back_kb("accs"))
        return
    tg.select(user_id, c.id)
    send_card(tg, chat_id, c.id, "✅ Аккаунт добавлен и выбран в панели")


def _ask_proxy_for_new(tg: TGBot, chat_id: int, user_id: int, prefix: str = ""):
    kb = K()
    kb.row(B("➡️ Без прокси", callback_data="accproxy_skip"))
    tg.ask(chat_id, user_id, prefix + PROXY_PROMPT, lambda m: _finish_add(tg, m.chat.id, m.from_user.id, m.text),
           keyboard=kb)


def cb_add(tg: TGBot, call: CallbackQuery):
    user_id = call.from_user.id

    def on_token(m: Message):
        _drop_message(tg, m)
        tg.flows[user_id] = split_secret(m.text or "")
        _ask_proxy_for_new(tg, m.chat.id, user_id, "🔒 Сообщение с токеном удалено.\n\n")

    tg.ask(call.message.chat.id, user_id, TOKEN_PROMPT, on_token)
    tg.answer(call)


def cb_proxy_skip(tg: TGBot, call: CallbackQuery):
    tg.pending.pop(call.from_user.id, None)
    _finish_add(tg, call.message.chat.id, call.from_user.id, "")
    tg.answer(call)


def _update(tg: TGBot, chat_id: int, acc_id: str, note_ok: str, **creds):
    try:
        tg.hub.update_credentials(acc_id, **creds)
    except AccountError as e:
        tg.bot.send_message(chat_id, f"❌ {escape(e.text)}", reply_markup=kbs.back_kb(kbs.cb("acc", acc_id)))
        return
    c = tg.hub.cardinals.get(acc_id)
    note = note_ok if c is None or c.status != "error" else f"⚠️ Сохранено, но вход не удался: {escape(c.error)}"
    send_card(tg, chat_id, acc_id, note)


def cb_check(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    if c.status == "running":
        try:
            c.account.get()
            note = "✅ Вход работает"
        except Exception as e:
            note = f"❌ {escape(short_error(e))}"
    else:
        c = tg.hub.restart_account(c.id)
        note = "✅ Вход работает" if c.status == "running" else f"❌ {escape(c.error)}"
    tg.edit(call, card_text(tg, c, note), card_kb(c))
    tg.answer(call)


def cb_proxy(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.ask(call.message.chat.id, call.from_user.id, PROXY_PROMPT + " /cancel — отмена.",
           lambda m: _update(tg, m.chat.id, c.id, "✅ Прокси сохранён", proxy=m.text or ""))
    tg.answer(call)


def cb_proxy_remove(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.answer(call, "Прокси убран")
    _update(tg, call.message.chat.id, c.id, "✅ Прокси убран", proxy="")


def cb_token(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return

    def on_token(m: Message):
        _drop_message(tg, m)
        secret = split_secret(m.text or "")
        if not (secret["token"] or secret["cookies"]):
            tg.bot.send_message(m.chat.id, "❌ Пустой токен")
            return
        _update(tg, m.chat.id, c.id, "✅ Токен обновлён, сообщение с ним удалено", **secret)

    tg.ask(call.message.chat.id, call.from_user.id, TOKEN_PROMPT, on_token)
    tg.answer(call)


def cb_user_agent(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return

    def on_answer(m: Message):
        value = (m.text or "").strip()
        _update(tg, m.chat.id, c.id, "✅ User-Agent сохранён", user_agent="" if value == "-" else value)

    tg.ask(call.message.chat.id, call.from_user.id,
           "🌐 Пришлите User-Agent вашего браузера или <code>-</code>, чтобы вернуть стандартный.", on_answer)
    tg.answer(call)


def cb_toggle(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    enable = c.status == "disabled"
    tg.hub.set_account_enabled(c.id, enable)
    c = tg.hub.cardinals[c.id]
    tg.edit(call, card_text(tg, c), card_kb(c))
    tg.answer(call, "Аккаунт включён" if enable else "Аккаунт выключен")


def cb_remove(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.bot.send_message(call.message.chat.id,
                        f"🗑 Убрать аккаунт <b>{escape(c.label)}</b> из бота?\n"
                        f"Его настройки и склад не удаляются, а переносятся в папку <code>accounts/_removed/</code>.",
                        reply_markup=kbs.confirm_kb("accdel", c.id))
    tg.answer(call)


def cb_remove_yes(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    label = c.label
    tg.pending.pop(call.from_user.id, None)
    path = tg.hub.remove_account(c.id)
    tg.edit(call, f"🗑 Аккаунт <b>{escape(label)}</b> убран.\nДанные: <code>{escape(path)}</code>", kbs.back_kb("accs"))
    tg.answer(call, "Убрано")


def cb_reset(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.bot.send_message(call.message.chat.id,
                        f"♻️ Сбросить настройки аккаунта <b>{escape(c.label)}</b> к стандартным?\n"
                        f"Причина: {escape(c.settings_error or 'нет')}\n"
                        "Повреждённый файл сохранится рядом как <code>settings.json.broken-…</code>.",
                        reply_markup=kbs.confirm_kb("accreset", c.id))
    tg.answer(call)


def cb_reset_yes(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    backup = tg.hub.reset_settings(c.id)
    c = tg.hub.cardinals[c.id]
    note = "✅ Настройки сброшены" + (f", старый файл: <code>{escape(backup)}</code>" if backup else "")
    tg.edit(call, card_text(tg, c, note), card_kb(c))
    tg.answer(call, "Сброшено")


def register(tg: TGBot):
    if not hasattr(tg, "flows"):
        tg.flows = {}
    tg.on_exact("accs", cb_list)
    tg.on_exact("accadd", cb_add)
    tg.on_exact("accproxy_skip", cb_proxy_skip)
    for prefix, handler in {"accsel:": cb_select, "acc:": cb_card, "acccheck:": cb_check, "accproxy:": cb_proxy,
                            "accproxy_rm:": cb_proxy_remove, "acctoken:": cb_token, "accua:": cb_user_agent,
                            "acctgl:": cb_toggle, "accdel:": cb_remove, "accdel_yes:": cb_remove_yes,
                            "accreset:": cb_reset, "accreset_yes:": cb_reset_yes}.items():
        tg.on_prefix(prefix, handler)
