from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from tg_bot import keyboards as kbs

if TYPE_CHECKING:
    from tg_bot.bot import TGBot

PER_PAGE = 10


def list_text(tg: TGBot) -> str:
    names = tg.hub.blacklist
    head = "⛔ <b>Чёрный список</b> (общий для всех аккаунтов)\n\n"
    if not names:
        return head + "Пусто. Покупателям из списка бот не выдаёт товар и не отвечает автоматически."
    return head + f"Всего: {len(names)}. Нажмите ❌, чтобы убрать."


def list_kb(tg: TGBot, page: int) -> K:
    names = tg.hub.blacklist
    pages = max(1, (len(names) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    kb = K()
    for name in names[page * PER_PAGE:(page + 1) * PER_PAGE]:
        try:
            kb.row(B(f"❌ {name}", callback_data=kbs.cb("blrm", name)))
        except ValueError:
            continue
    nav = []
    if page > 0:
        nav.append(B("⬅️", callback_data=kbs.cb("bl", page - 1)))
    if page < pages - 1:
        nav.append(B("➡️", callback_data=kbs.cb("bl", page + 1)))
    if nav:
        kb.row(*nav)
    kb.row(B("➕ Добавить", callback_data="bladd"))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def cb_list(tg: TGBot, call: CallbackQuery):
    tg.edit(call, list_text(tg), list_kb(tg, int(call.data.split(":")[1])))
    tg.answer(call)


def cb_add(tg: TGBot, call: CallbackQuery):
    def on_name(m: Message):
        name = (m.text or "").strip()
        if not name:
            tg.bot.send_message(m.chat.id, "❌ Пустой ник")
            return
        added = tg.hub.ban(name)
        text = f"⛔ {escape(name)} добавлен в чёрный список" if added else f"{escape(name)} уже в чёрном списке"
        tg.bot.send_message(m.chat.id, text, reply_markup=list_kb(tg, 0))

    tg.ask(call.message.chat.id, call.from_user.id, "⛔ Пришлите ник покупателя на Playerok. /cancel — отмена.",
           on_name)
    tg.answer(call)


def cb_remove(tg: TGBot, call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    tg.hub.unban(name)
    tg.edit(call, list_text(tg), list_kb(tg, 0))
    tg.answer(call, f"{name} убран")


def register(tg: TGBot):
    tg.on_exact("bladd", cb_add)
    tg.on_prefix("bl:", cb_list)
    tg.on_prefix("blrm:", cb_remove)
