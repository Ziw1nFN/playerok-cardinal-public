from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.errors import short_error
from handlers import chat_sales, is_system_text
from playerokapi.enums import ChatTypes, ItemDealStatuses
from tg_bot import chatview
from tg_bot import keyboards as kbs

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

PER_PAGE = 8
HISTORY_PAGE = 10
LINE_LIMIT = 350
SNIPPET = 40


def chat_button(c: Cardinal, chat) -> str:
    unread = "🔵 " if getattr(chat, "unread_messages_counter", 0) else ""
    text = getattr(getattr(chat, "last_message", None), "text", None) or ""
    snippet = "" if is_system_text(text) else " ".join(text.split())
    label = f"{unread}{chatview.buyer_name(c, chat)}" + (f" — {tools.short(snippet, SNIPPET)}" if snippet else "")
    return tools.short(label, 64)


def int_part(call: CallbackQuery, index: int) -> int:
    parts = call.data.split(":")
    return int(parts[index]) if len(parts) > index and parts[index].isdigit() else 0


def cb_list(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    try:
        chats = list(c.account.get_chats(count=24, type=ChatTypes.PM).chats)
    except Exception as e:
        tg.edit(call, f"❌ Не удалось получить чаты: {escape(short_error(e))}", kbs.back_kb("menu"))
        tg.answer(call)
        return
    pages = max(1, (len(chats) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(int_part(call, 2), pages - 1))
    kb = K()
    for chat in chats[page * PER_PAGE:(page + 1) * PER_PAGE]:
        kb.row(B(chat_button(c, chat), callback_data=kbs.cb("ch1", c.id, chat.id, 0)))
    nav = []
    if page > 0:
        nav.append(B("⬅️", callback_data=kbs.cb("chl", c.id, page - 1)))
    if page < pages - 1:
        nav.append(B("➡️", callback_data=kbs.cb("chl", c.id, page + 1)))
    if nav:
        kb.row(*nav)
    kb.row(B("◀️ Назад", callback_data="menu"))
    text = (f"💬 <b>Чаты — {escape(c.label)}</b>\nПоследние 24 личных чата, 🔵 — есть непрочитанные"
            + (f" · стр. {page + 1}/{pages}" if pages > 1 else "") + ("\n\nЧатов нет." if not chats else ""))
    tg.edit(call, text, kb)
    tg.answer(call)


def history_kb(tg: TGBot, c: Cardinal, chat_id: str, page: int, more: bool) -> K:
    kb = K()
    nav = []
    if more:
        nav.append(B("⬅️ Раньше", callback_data=kbs.cb("ch1", c.id, chat_id, page + 1)))
    if page > 0:
        nav.append(B("Новее ➡️", callback_data=kbs.cb("ch1", c.id, chat_id, page - 1)))
    if nav:
        kb.row(*nav)
    kb.row(B("✉️ Ответить", callback_data=kbs.cb("reply", c.id, chat_id)),
           B("📋 Сделки чата", callback_data=kbs.cb("chd", c.id, chat_id)))
    if tg.sync.is_active(c):
        kb.row(B("🧵 Тема в группе", callback_data=kbs.cb("cht", c.id, chat_id)))
    kb.row(B("🌐 Чат на сайте", url=tools.chat_link(chat_id)))
    kb.row(B("◀️ К чатам", callback_data=kbs.cb("chl", c.id, 0)))
    return kb


def cb_history(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    chat_id = call.data.split(":")[2]
    try:
        messages = list(c.account.get_chat_messages(chat_id, count=24).messages)
    except Exception as e:
        tg.answer(call, f"Не удалось получить переписку: {short_error(e)}"[:190], alert=True)
        return
    buyer = chatview.buyer_name(c, messages=messages)
    lines = chatview.history_lines(c, messages, buyer, limit=LINE_LIMIT)
    pages = max(1, (len(lines) + HISTORY_PAGE - 1) // HISTORY_PAGE)
    page = max(0, min(int_part(call, 3), pages - 1))
    end = len(lines) - page * HISTORY_PAGE
    shown = lines[max(0, end - HISTORY_PAGE):end]
    head = f"💬 <b>{escape(buyer)}</b>" + (f" · стр. {page + 1}/{pages}" if pages > 1 else "")
    body = "\n\n".join(shown) if shown else "Сообщений пока нет."
    text = f"{head}\n\n{body}"
    if len(text) > chatview.TEXT_LIMIT:
        text = chatview.pack(shown, head)[-1]
    tg.edit(call, text, history_kb(tg, c, chat_id, page, page < pages - 1))
    tg.answer(call)


def cb_chat_deals(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    chat_id = call.data.split(":")[2]
    try:
        chat = c.account.get_chat(chat_id)
    except Exception as e:
        tg.answer(call, f"Не удалось получить сделки: {short_error(e)}"[:190], alert=True)
        return
    deals = chat_sales(c, chat, set(ItemDealStatuses))
    kb = K()
    for d in deals[:PER_PAGE]:
        kb.row(B(chatview.deal_button(d), callback_data=kbs.cb("dl1", c.id, "a", d.id)))
    kb.row(B("◀️ К переписке", callback_data=kbs.cb("ch1", c.id, chat_id, 0)))
    text = f"📋 <b>Сделки чата с {escape(chatview.buyer_name(c, chat))}</b>" + ("" if deals else "\n\nСделок нет.")
    tg.edit(call, text, kb)
    tg.answer(call)


def cb_topic(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    if not tg.sync.is_active(c):
        tg.answer(call, "Синхронизация чатов не включена", alert=True)
        return
    chat_id = call.data.split(":")[2]
    tg.answer(call)
    try:
        thread = tg.sync.ensure_topic(c, chat_id)
    except Exception as e:
        tg.bot.send_message(call.message.chat.id, f"❌ Не удалось создать тему: {escape(short_error(e))}")
        return
    kb = K()
    kb.row(B("🧵 Открыть тему", url=chatview.topic_url(tg.sync.config(c)["group_id"], thread)))
    tg.bot.send_message(call.message.chat.id, "🧵 Тема чата в группе готова", reply_markup=kb)


def register(tg: TGBot):
    for prefix, handler in {"chl:": cb_list, "ch1:": cb_history, "chd:": cb_chat_deals, "cht:": cb_topic}.items():
        tg.on_prefix(prefix, handler)
