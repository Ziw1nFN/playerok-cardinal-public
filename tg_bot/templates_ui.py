from __future__ import annotations

import threading
from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.errors import short_error
from Utils.settings import new_id
from Utils.variables import render
from tg_bot import keyboards as kbs
from tg_bot.settings_ui import ask_text, preview

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

MAX_TEMPLATES = 10
TITLE_MAX = 30
TITLE_PROMPT = f"💬 Пришлите название кнопки шаблона (до {TITLE_MAX} символов). /cancel — отмена."
USED_PROMPTS_MAX = 500

_used_prompts: set[tuple[int, int]] = set()
_used_lock = threading.Lock()


def claim_prompt(chat_id: int, message_id: int) -> bool:
    with _used_lock:
        if (chat_id, message_id) in _used_prompts:
            return False
        if len(_used_prompts) >= USED_PROMPTS_MAX:
            _used_prompts.clear()
        _used_prompts.add((chat_id, message_id))
        return True


def release_prompt(chat_id: int, message_id: int) -> None:
    with _used_lock:
        _used_prompts.discard((chat_id, message_id))


def close_prompt(tg: TGBot, chat_id: int, message_id: int) -> None:
    try:
        tg.bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
    except Exception:
        pass


def find(c: Cardinal, template_id: str) -> dict | None:
    return next((t for t in c.settings["templates"] if t["id"] == template_id), None)


def template_of(tg: TGBot, call: CallbackQuery, index: int = 2) -> tuple[Cardinal | None, dict | None]:
    c = tg.account_from(call)
    if c is None:
        return None, None
    template = find(c, call.data.split(":")[index])
    if template is None:
        tg.answer(call, "Шаблон не найден", alert=True)
    return c, template


def check_title(text: str | None) -> tuple[str, str | None]:
    title = (text or "").strip()
    if not title:
        return "", "Название пустое"
    if len(title) > TITLE_MAX:
        return "", f"Название длиннее {TITLE_MAX} символов"
    return title, None


def reply_kb(c: Cardinal, chat_id: str) -> K:
    kb = K()
    buttons = [B(tools.short(t["title"], TITLE_MAX), callback_data=kbs.cb("tpl", c.id, chat_id, t["id"]))
               for t in c.settings["templates"][:MAX_TEMPLATES]]
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i + 2])
    return kb


def list_text(c: Cardinal) -> str:
    count = len(c.settings["templates"])
    return (f"💬 <b>Шаблоны быстрых ответов — {escape(c.label)}</b>\n\n"
            f"Появляются кнопками, когда вы нажимаете «✉️ Ответить» на уведомлении. "
            f"Нажатие сразу отправляет текст покупателю.\n\nШаблонов: {count} из {MAX_TEMPLATES}")


def list_kb(c: Cardinal) -> K:
    kb = K()
    for t in c.settings["templates"]:
        kb.row(B(tools.short(t["title"], TITLE_MAX), callback_data=kbs.cb("tp1", c.id, t["id"])))
    if len(c.settings["templates"]) < MAX_TEMPLATES:
        kb.row(B("➕ Новый шаблон", callback_data=kbs.cb("tpnew", c.id)))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def entry_text(t: dict) -> str:
    return f"💬 <b>{escape(t['title'])}</b>\n\nТекст:\n<blockquote>{escape(t['text'])}</blockquote>"


def entry_kb(c: Cardinal, t: dict) -> K:
    kb = K()
    kb.row(B("✏️ Название", callback_data=kbs.cb("tptitle", c.id, t["id"])),
           B("✏️ Текст", callback_data=kbs.cb("tptxt", c.id, t["id"])))
    kb.row(B("🗑 Удалить", callback_data=kbs.cb("tpdel", c.id, t["id"])))
    kb.row(B("◀️ К шаблонам", callback_data=kbs.cb("tp", c.id)))
    return kb


def send_entry(tg: TGBot, chat_id: int, c: Cardinal, template_id: str, note: str):
    t = find(c, template_id)
    if t is not None:
        tg.bot.send_message(chat_id, f"{note}\n\n{entry_text(t)}", reply_markup=entry_kb(c, t))


def set_field(c: Cardinal, template_id: str, field: str, value: str):
    def change(s: dict):
        t = next((x for x in s["templates"] if x["id"] == template_id), None)
        if t is not None:
            t[field] = value

    c.update_settings(change)


def cb_list(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, list_text(c), list_kb(c))
    tg.answer(call)


def cb_entry(tg: TGBot, call: CallbackQuery):
    c, t = template_of(tg, call)
    if t is None:
        return
    tg.edit(call, entry_text(t), entry_kb(c, t))
    tg.answer(call)


def cb_new(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    if len(c.settings["templates"]) >= MAX_TEMPLATES:
        tg.answer(call, f"Можно не больше {MAX_TEMPLATES} шаблонов", alert=True)
        return
    back = kbs.back_kb(kbs.cb("tp", c.id))

    def on_title(m: Message):
        title, error = check_title(m.text)
        if error:
            tg.bot.send_message(m.chat.id, f"❌ {error}, шаблон не создан", reply_markup=back)
            return

        def on_text(answer: Message):
            text = (answer.text or "").strip()
            if not text:
                tg.bot.send_message(answer.chat.id, "❌ Текст пустой, шаблон не создан", reply_markup=back)
                return
            holder = {}

            def add(s: dict):
                if len(s["templates"]) >= MAX_TEMPLATES:
                    return
                holder["id"] = new_id(s["templates"], "t")
                s["templates"].append({"id": holder["id"], "title": title, "text": text})

            c.update_settings(add)
            if "id" not in holder:
                tg.bot.send_message(answer.chat.id, f"❌ Можно не больше {MAX_TEMPLATES} шаблонов", reply_markup=back)
                return
            send_entry(tg, answer.chat.id, c, holder["id"], "✅ Шаблон добавлен.\n" + preview(text, "template"))

        ask_text(tg, m.chat.id, m.from_user.id, title, "", "template", on_text)

    tg.ask(call.message.chat.id, call.from_user.id, TITLE_PROMPT, on_title)
    tg.answer(call)


def cb_title(tg: TGBot, call: CallbackQuery):
    c, t = template_of(tg, call)
    if t is None:
        return

    def on_title(m: Message):
        title, error = check_title(m.text)
        if error:
            tg.bot.send_message(m.chat.id, f"❌ {error}, ничего не изменено")
            return
        set_field(c, t["id"], "title", title)
        send_entry(tg, m.chat.id, c, t["id"], "✅ Название сохранено")

    tg.ask(call.message.chat.id, call.from_user.id, f"Сейчас: {escape(t['title'])}\n\n{TITLE_PROMPT}", on_title)
    tg.answer(call)


def cb_text(tg: TGBot, call: CallbackQuery):
    c, t = template_of(tg, call)
    if t is None:
        return

    def on_text(m: Message):
        text = (m.text or "").strip()
        if not text:
            tg.bot.send_message(m.chat.id, "❌ Текст пустой, ничего не изменено")
            return
        set_field(c, t["id"], "text", text)
        send_entry(tg, m.chat.id, c, t["id"], "✅ Текст сохранён.\n" + preview(text, "template"))

    ask_text(tg, call.message.chat.id, call.from_user.id, t["title"], t["text"], "template", on_text)
    tg.answer(call)


def cb_delete(tg: TGBot, call: CallbackQuery):
    c, t = template_of(tg, call)
    if t is None:
        return
    tg.bot.send_message(call.message.chat.id, f"🗑 Удалить шаблон <b>{escape(t['title'])}</b>?",
                        reply_markup=kbs.confirm_kb("tpdel", c.id, t["id"]))
    tg.answer(call)


def cb_delete_yes(tg: TGBot, call: CallbackQuery):
    c, t = template_of(tg, call)
    if t is None:
        return
    c.update_settings(lambda s: s.__setitem__("templates", [x for x in s["templates"] if x["id"] != t["id"]]))
    tg.edit(call, "🗑 Шаблон удалён.", kbs.back_kb(kbs.cb("tp", c.id)))
    tg.answer(call, "Удалено")


def cb_send(tg: TGBot, call: CallbackQuery):
    c, t = template_of(tg, call, index=3)
    if t is None:
        return
    chat_id = call.data.split(":")[2]
    prompt = (call.message.chat.id, call.message.message_id)
    if not claim_prompt(*prompt):
        tg.answer(call, "Уже отправлено")
        return
    thread = getattr(call.message, "message_thread_id", None)
    in_topic = thread is not None and tg.sync.find_chat(call.message.chat.id, thread) is not None
    keyboard = None if in_topic else kbs.message_kb(c.id, chat_id)

    def reply(text: str, markup=None):
        tg.bot.send_message(call.message.chat.id, text, reply_markup=markup, message_thread_id=thread)

    tg.pending.pop(call.from_user.id, None)
    tg.answer(call)
    try:
        chat = c.account.get_chat(chat_id)
    except Exception as e:
        release_prompt(*prompt)
        reply(f"❌ Не удалось открыть чат Playerok: {escape(short_error(e))}")
        return
    buyer = c.buyer_of_chat(chat)
    text = render(t["text"], {"nickname": getattr(buyer, "username", "") or "", "chat_id": chat_id, "seller": c.label})
    if in_topic:
        result = c.send_once(chat_id, text)
        status, error = result.status, result.error
        tg.sync.mark_echo(c, result.message_id)
    else:
        status, error = ("sent" if c.send_message(chat_id, text, mirror="owner") else "rejected"), ""
    if status == "sent":
        close_prompt(tg, *prompt)
        reply(f"✅ Отправлено:\n<blockquote>{escape(text)}</blockquote>", keyboard)
    elif status == "unclear":
        close_prompt(tg, *prompt)
        reply("⚠️ Не знаю, дошло ли — проверьте на сайте")
    else:
        release_prompt(*prompt)
        reply("❌ Не удалось отправить сообщение" + (f": {escape(error)}" if error else ""), keyboard)


def register(tg: TGBot):
    for prefix, handler in {"tp:": cb_list, "tp1:": cb_entry, "tpnew:": cb_new, "tptitle:": cb_title,
                            "tptxt:": cb_text, "tpdel:": cb_delete, "tpdel_yes:": cb_delete_yes,
                            "tpl:": cb_send}.items():
        tg.on_prefix(prefix, handler)
