from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.settings import DEAL_REPLY_KINDS, REMINDER_HOURS
from tg_bot import keyboards as kbs
from tg_bot.settings_ui import ask_text, preview

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

REPLY_TITLES = {
    "confirmed": "✅ Подтвердил заказ",
    "problem": "🚨 Проблема в сделке",
    "rolled_back": "↩️ Возврат",
    "sent": "📤 «Отправлено»",
}
WORD_TITLES = {"complete": "«выполнить»", "refund": "«вернуть»"}
TOGGLE_KINDS = set(DEAL_REPLY_KINDS) | {"reminder", "commands"}
TEXT_KINDS = set(DEAL_REPLY_KINDS) | {"reminder"}


def screen_text(c: Cardinal) -> str:
    s = c.settings
    lines = [f"🧾 <b>Ответы на сделки — {escape(c.label)}</b>\n", "<b>Автоответы покупателю:</b>"]
    for kind in DEAL_REPLY_KINDS:
        reply = s["deal_replies"][kind]
        text = escape(tools.short(reply["text"], 120))
        lines.append(f"{kbs.dot(reply['enabled'])} {REPLY_TITLES[kind]}: <i>{text}</i>")
    reminder = s["reminder"]
    lines.append(f"\n<b>Напоминание подтвердить</b> {kbs.dot(reminder['enabled'])} через {reminder['hours']} ч:\n"
                 f"<i>{escape(tools.short(reminder['text'], 120))}</i>")
    commands = s["chat_commands"]
    lines.append(f"\n<b>Команды в чате Playerok</b> {kbs.dot(commands['enabled'])} (только твои сообщения):\n"
                 f"📤 {escape(tools.short(' | '.join(commands['complete']), 120))}\n"
                 f"↩️ {escape(tools.short(' | '.join(commands['refund']), 120))}")
    return "\n".join(lines)


def screen_kb(c: Cardinal) -> K:
    s = c.settings
    kb = K()
    for kind in DEAL_REPLY_KINDS:
        kb.row(B(f"{kbs.dot(s['deal_replies'][kind]['enabled'])} {REPLY_TITLES[kind]}",
                 callback_data=kbs.cb("drt", c.id, kind)),
               B("✏️", callback_data=kbs.cb("drx", c.id, kind)))
    kb.row(B(f"{kbs.dot(s['reminder']['enabled'])} ⏰ Напоминание", callback_data=kbs.cb("drt", c.id, "reminder")),
           B(f"⏱ {s['reminder']['hours']} ч", callback_data=kbs.cb("drh", c.id)),
           B("✏️", callback_data=kbs.cb("drx", c.id, "reminder")))
    kb.row(B(f"{kbs.dot(s['chat_commands']['enabled'])} ⌨️ Команды в чате", callback_data=kbs.cb("drt", c.id, "commands")))
    kb.row(B("✏️ Слова «выполнить»", callback_data=kbs.cb("drw", c.id, "complete")),
           B("✏️ Слова «вернуть»", callback_data=kbs.cb("drw", c.id, "refund")))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def _kind(tg: TGBot, call: CallbackQuery, allowed: set) -> tuple[Cardinal | None, str | None]:
    c = tg.account_from(call)
    if c is None:
        return None, None
    kind = call.data.split(":")[2]
    if kind not in allowed:
        tg.answer(call, "Нет такого пункта", alert=True)
        return None, None
    return c, kind


def cb_screen(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, screen_text(c), screen_kb(c))
    tg.answer(call)


def cb_toggle(tg: TGBot, call: CallbackQuery):
    c, kind = _kind(tg, call, TOGGLE_KINDS)
    if c is None:
        return

    def change(s: dict):
        target = {"reminder": s["reminder"], "commands": s["chat_commands"]}.get(kind) or s["deal_replies"][kind]
        target["enabled"] = not target["enabled"]

    c.update_settings(change)
    tg.edit(call, screen_text(c), screen_kb(c))
    tg.answer(call)


def cb_text(tg: TGBot, call: CallbackQuery):
    c, kind = _kind(tg, call, TEXT_KINDS)
    if c is None:
        return
    is_reminder = kind == "reminder"
    title = "⏰ Напоминание" if is_reminder else REPLY_TITLES[kind]
    current = c.settings["reminder"]["text"] if is_reminder else c.settings["deal_replies"][kind]["text"]
    text_kind = "reminder" if is_reminder else "deal_reply"

    def on_text(m: Message):
        value = (m.text or "").strip()
        if not value:
            tg.bot.send_message(m.chat.id, "❌ Текст пустой, ничего не изменено")
            return

        def change(s: dict):
            target = s["reminder"] if is_reminder else s["deal_replies"][kind]
            target["text"] = value

        c.update_settings(change)
        tg.bot.send_message(m.chat.id, f"✅ {title}: сохранено.\n\n" + preview(value, text_kind),
                            reply_markup=kbs.back_kb(kbs.cb("dr", c.id)))

    ask_text(tg, call.message.chat.id, call.from_user.id, title, current, text_kind, on_text)
    tg.answer(call)


def cb_hours(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    low, high = REMINDER_HOURS
    back = kbs.back_kb(kbs.cb("dr", c.id))

    def on_hours(m: Message):
        try:
            value = int((m.text or "").strip())
        except ValueError:
            value = None
        if value is None or not low <= value <= high:
            tg.bot.send_message(m.chat.id, f"❌ Введите целое число от {low} до {high}", reply_markup=back)
            return
        c.update_settings(lambda s: s["reminder"].__setitem__("hours", value))
        tg.bot.send_message(m.chat.id, f"✅ Напоминание через {value} ч", reply_markup=back)

    tg.ask(call.message.chat.id, call.from_user.id,
           f"⏱ Через сколько часов напоминать? Сейчас: {c.settings['reminder']['hours']} ч.\n"
           f"Число от {low} до {high}. /cancel — отмена.", on_hours)
    tg.answer(call)


def cb_words(tg: TGBot, call: CallbackQuery):
    c, action = _kind(tg, call, set(WORD_TITLES))
    if c is None:
        return
    back = kbs.back_kb(kbs.cb("dr", c.id))

    def on_words(m: Message):
        words = [w.strip() for w in (m.text or "").split("|") if w.strip()]
        if not words:
            tg.bot.send_message(m.chat.id, "❌ Нужно хотя бы одно слово", reply_markup=back)
            return
        c.update_settings(lambda s: s["chat_commands"].__setitem__(action, words))
        tg.bot.send_message(m.chat.id, f"✅ Слова {WORD_TITLES[action]}: {escape(' | '.join(words))}", reply_markup=back)

    tg.ask(call.message.chat.id, call.from_user.id,
           f"⌨️ Пришлите слова для команды {WORD_TITLES[action]} через <code>|</code>.\n"
           f"Сейчас: {escape(' | '.join(c.settings['chat_commands'][action]))}. /cancel — отмена.", on_words)
    tg.answer(call)


def register(tg: TGBot):
    for prefix, handler in {"dr:": cb_screen, "drt:": cb_toggle, "drx:": cb_text, "drh:": cb_hours,
                            "drw:": cb_words}.items():
        tg.on_prefix(prefix, handler)
