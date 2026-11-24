from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import config as cfg
from Utils import tools
from Utils.settings import new_id
from tg_bot import keyboards as kbs
from tg_bot.settings_ui import ask_text, preview

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

PER_PAGE = 8
COMMANDS_PROMPT = ("🤖 Пришлите команды через <code>|</code>, например: <code>!цена | !price</code>\n"
                   "Регистр не важен. /cancel — отмена.")


def entry_of(tg: TGBot, call: CallbackQuery) -> tuple[Cardinal | None, dict | None]:
    c = tg.account_from(call)
    if c is None:
        return None, None
    entry_id = call.data.split(":")[2]
    entry = next((e for e in c.settings["auto_responses"] if e["id"] == entry_id), None)
    if entry is None:
        tg.answer(call, "Запись не найдена", alert=True)
    return c, entry


def parse_commands(c: Cardinal, text: str, skip_id: str | None = None) -> tuple[list[str], str | None]:
    commands = cfg.split_commands(text or "")
    if not commands:
        return [], "Нужна хотя бы одна команда"
    taken = {cmd: e for e in c.settings["auto_responses"] if e["id"] != skip_id for cmd in e["commands"]}
    clash = next((cmd for cmd in commands if cmd in taken), None)
    if clash:
        return [], f"Команда «{clash}» уже есть в другой записи"
    return commands, None


def list_text(c: Cardinal) -> str:
    count = len(c.settings["auto_responses"])
    return (f"🤖 <b>Автоответчик — {escape(c.label)}</b>\n\n"
            + (f"Команд: {count}. Нажмите, чтобы изменить." if count else "Команд пока нет."))


def list_kb(c: Cardinal, page: int) -> K:
    entries = c.settings["auto_responses"]
    pages = max(1, (len(entries) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    kb = K()
    for e in entries[page * PER_PAGE:(page + 1) * PER_PAGE]:
        kb.row(B(f"🤖 {tools.short(' | '.join(e['commands']), 40)}", callback_data=kbs.cb("ar1", c.id, e["id"])))
    nav = []
    if page > 0:
        nav.append(B("⬅️", callback_data=kbs.cb("ar", c.id, page - 1)))
    if page < pages - 1:
        nav.append(B("➡️", callback_data=kbs.cb("ar", c.id, page + 1)))
    if nav:
        kb.row(*nav)
    kb.row(B("➕ Новая команда", callback_data=kbs.cb("arnew", c.id)))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def entry_text(c: Cardinal, e: dict) -> str:
    return (f"🤖 <b>{escape(' | '.join(e['commands']))}</b>\n\n"
            f"Ответ:\n<blockquote>{escape(e['text'])}</blockquote>\n"
            f"🔔 Уведомлять меня: {'🟢' if e.get('notify') else '🔴'}")


def entry_kb(c: Cardinal, e: dict) -> K:
    kb = K()
    kb.row(B("✏️ Команды", callback_data=kbs.cb("arcmd", c.id, e["id"])),
           B("✏️ Ответ", callback_data=kbs.cb("artxt", c.id, e["id"])))
    kb.row(B(f"{kbs.dot(e.get('notify'))} Уведомление", callback_data=kbs.cb("arntf", c.id, e["id"])))
    kb.row(B("🗑 Удалить", callback_data=kbs.cb("ardel", c.id, e["id"])))
    kb.row(B("◀️ К списку", callback_data=kbs.cb("ar", c.id, 0)))
    return kb


def send_entry(tg: TGBot, chat_id: int, c: Cardinal, entry_id: str, note: str = ""):
    e = next((x for x in c.settings["auto_responses"] if x["id"] == entry_id), None)
    if e is not None:
        tg.bot.send_message(chat_id, (note + "\n\n" if note else "") + entry_text(c, e), reply_markup=entry_kb(c, e))


def cb_list(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, list_text(c), list_kb(c, int(call.data.split(":")[2])))
    tg.answer(call)


def cb_new(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return

    def on_commands(m: Message):
        commands, error = parse_commands(c, m.text)
        if error:
            tg.bot.send_message(m.chat.id, f"❌ {escape(error)}", reply_markup=kbs.back_kb(kbs.cb("ar", c.id, 0)))
            return

        def on_text(answer: Message):
            text = (answer.text or "").strip()
            if not text:
                tg.bot.send_message(answer.chat.id, "❌ Ответ пустой, запись не создана")
                return
            holder = {}

            def add(s: dict):
                holder["id"] = new_id(s["auto_responses"], "r")
                s["auto_responses"].append({"id": holder["id"], "commands": commands, "text": text, "notify": False})

            c.update_settings(add)
            send_entry(tg, answer.chat.id, c, holder["id"], "✅ Команда добавлена.\n" + preview(text, "auto_response"))

        ask_text(tg, m.chat.id, m.from_user.id, "Ответ на команду", "", "auto_response", on_text)

    tg.ask(call.message.chat.id, call.from_user.id, COMMANDS_PROMPT, on_commands)
    tg.answer(call)


def cb_entry(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return
    tg.edit(call, entry_text(c, e), entry_kb(c, e))
    tg.answer(call)


def cb_commands(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return

    def on_commands(m: Message):
        commands, error = parse_commands(c, m.text, skip_id=e["id"])
        if error:
            tg.bot.send_message(m.chat.id, f"❌ {escape(error)}")
            return
        c.update_settings(lambda s: next(x for x in s["auto_responses"] if x["id"] == e["id"]).__setitem__(
            "commands", commands))
        send_entry(tg, m.chat.id, c, e["id"], "✅ Команды сохранены")

    tg.ask(call.message.chat.id, call.from_user.id, COMMANDS_PROMPT, on_commands)
    tg.answer(call)


def cb_text(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return

    def on_text(m: Message):
        text = (m.text or "").strip()
        if not text:
            tg.bot.send_message(m.chat.id, "❌ Ответ пустой, ничего не изменено")
            return
        c.update_settings(lambda s: next(x for x in s["auto_responses"] if x["id"] == e["id"]).__setitem__(
            "text", text))
        send_entry(tg, m.chat.id, c, e["id"], "✅ Ответ сохранён.\n" + preview(text, "auto_response"))

    ask_text(tg, call.message.chat.id, call.from_user.id, "Ответ на команду", e["text"], "auto_response", on_text)
    tg.answer(call)


def cb_notify(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return
    c.update_settings(lambda s: next(x for x in s["auto_responses"] if x["id"] == e["id"]).__setitem__(
        "notify", not e.get("notify")))
    e = next(x for x in c.settings["auto_responses"] if x["id"] == e["id"])
    tg.edit(call, entry_text(c, e), entry_kb(c, e))
    tg.answer(call)


def cb_delete(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return
    tg.bot.send_message(call.message.chat.id, f"🗑 Удалить команду <b>{escape(' | '.join(e['commands']))}</b>?",
                        reply_markup=kbs.confirm_kb("ardel", c.id, e["id"]))
    tg.answer(call)


def cb_delete_yes(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return
    c.update_settings(lambda s: s.__setitem__("auto_responses", [x for x in s["auto_responses"] if x["id"] != e["id"]]))
    tg.edit(call, "🗑 Команда удалена.", kbs.back_kb(kbs.cb("ar", c.id, 0)))
    tg.answer(call, "Удалено")


def register(tg: TGBot):
    for prefix, handler in {"ar:": cb_list, "arnew:": cb_new, "ar1:": cb_entry, "arcmd:": cb_commands,
                            "artxt:": cb_text, "arntf:": cb_notify, "ardel:": cb_delete,
                            "ardel_yes:": cb_delete_yes}.items():
        tg.on_prefix(prefix, handler)
