from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Callable

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.profiles import RAISE_MODES, has_conditions, new_profile, normalize
from Utils.settings import DEAL_REPLY_KINDS, NUMBER_LIMITS, new_id
from tg_bot import keyboards as kbs
from tg_bot.settings_ui import ask_text, preview

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

TITLE_MAX = 30
MODE_TITLES = {"default": "как обычно", "off": "не поднимать", "custom": "своё"}
MODE_BUTTONS = {"default": "⬆️ Как обычно", "off": "⛔ Не поднимать", "custom": "⚙️ Своё"}
KIND_TITLES = {"confirmed": "✅ Подтвердил заказ", "problem": "🚨 Проблема", "rolled_back": "↩️ Возврат",
               "sent": "📤 «Отправлено»"}
LIST_PROMPT = "Пришлите через <code>|</code>, например: <code>Robux | робукс</code>. <code>-</code> — убрать условие."
INTRO = ("Профиль — это фильтр товаров (слова в названии, цена, игра или категория) и свои настройки: поднятие, "
         "тексты ответов на сделки и ответы на команды. Товар берёт первый подходящий профиль по порядку.")


def find(c: Cardinal, profile_id: str) -> dict | None:
    raw = next((p for p in c.settings["profiles"] if p.get("id") == profile_id), None)
    return normalize(raw) if raw is not None else None


def profile_of(tg: TGBot, call: CallbackQuery) -> tuple[Cardinal | None, dict | None]:
    c = tg.account_from(call)
    if c is None:
        return None, None
    profile = find(c, call.data.split(":")[2])
    if profile is None:
        tg.answer(call, "Профиль не найден", alert=True)
    return c, profile


def change(c: Cardinal, profile_id: str, fn: Callable[[dict], None]) -> None:
    def apply(s: dict):
        for i, raw in enumerate(s["profiles"]):
            if raw.get("id") == profile_id:
                s["profiles"][i] = normalize(raw)
                fn(s["profiles"][i])

    c.update_settings(apply)


def parse_list(text: str | None) -> list[str] | None:
    text = (text or "").strip()
    if text == "-":
        return []
    items = [part.strip() for part in text.split("|") if part.strip()]
    return items or None


def parse_price(text: str | None) -> tuple[int, int] | None:
    parts = (text or "").split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    low, high = int(parts[0]), int(parts[1])
    if low and high and low > high:
        low, high = high, low
    return low, high


def parse_limit(text: str | None, key: str) -> int | None:
    low, high = NUMBER_LIMITS[key]
    value = (text or "").strip()
    if not value.isdigit() or not low <= int(value) <= high:
        return None
    return int(value)


def raise_line(p: dict) -> str:
    r = p["raise"]
    if r["mode"] == "custom":
        return f"⬆️ Поднятие: своё — раз в {r['interval']} мин, до {r['max_price']} ₽"
    return f"⬆️ Поднятие: {MODE_TITLES[r['mode']]}"


def profile_text(p: dict) -> str:
    f = p["filters"]
    price = "—"
    if f["price_min"] or f["price_max"]:
        price = " ".join(part for part in (f"от {f['price_min']}" if f["price_min"] else "",
                                           f"до {f['price_max']}" if f["price_max"] else "") if part) + " ₽"
    lines = [f"🎯 <b>{escape(p['title'])}</b> {kbs.dot(p['enabled'])}", "",
             f"🔤 Слова в названии: {escape(' | '.join(f['words'])) or '—'}",
             f"💵 Цена: {price}",
             f"🎮 Игра/категория: {escape(' | '.join(f['places'])) or '—'}"]
    if not has_conditions(p):
        lines.append("⚠️ Нет условий — профиль ни к чему не применяется")
    texts = sum(1 for kind in DEAL_REPLY_KINDS if p["deal_replies"].get(kind))
    lines += ["", raise_line(p), f"🧾 Своих текстов ответов: {texts} из {len(DEAL_REPLY_KINDS)}",
              f"🤖 Команд: {len(p['auto_responses'])}"]
    return "\n".join(lines)


def profile_kb(c: Cardinal, p: dict) -> K:
    pid = p["id"]
    kb = K()
    kb.row(B(f"{kbs.dot(p['enabled'])} {'Выключить' if p['enabled'] else 'Включить'}",
             callback_data=kbs.cb("pft", c.id, pid)),
           B("✏️ Название", callback_data=kbs.cb("pfe", c.id, pid, "title")))
    kb.row(B("🔤 Слова", callback_data=kbs.cb("pfe", c.id, pid, "words")),
           B("💵 Цена", callback_data=kbs.cb("pfe", c.id, pid, "price")),
           B("🎮 Игра", callback_data=kbs.cb("pfe", c.id, pid, "places")))
    kb.row(*[B(("• " if p["raise"]["mode"] == mode else "") + MODE_BUTTONS[mode],
               callback_data=kbs.cb("pfr", c.id, pid, mode)) for mode in RAISE_MODES])
    if p["raise"]["mode"] == "custom":
        kb.row(B("⏱ Интервал", callback_data=kbs.cb("pfe", c.id, pid, "interval")),
               B("💰 Макс. цена", callback_data=kbs.cb("pfe", c.id, pid, "maxprice")))
    kb.row(B("🧾 Тексты ответов", callback_data=kbs.cb("pfx", c.id, pid)),
           B("🤖 Команды", callback_data=kbs.cb("pfc", c.id, pid)))
    kb.row(B("🔼", callback_data=kbs.cb("pfm", c.id, pid, "u")), B("🔽", callback_data=kbs.cb("pfm", c.id, pid, "d")),
           B("🗑 Удалить", callback_data=kbs.cb("pfd", c.id, pid)))
    kb.row(B("◀️ К профилям", callback_data=kbs.cb("pf", c.id)))
    return kb


def send_profile(tg: TGBot, chat_id: int, c: Cardinal, profile_id: str, note: str = "") -> None:
    p = find(c, profile_id)
    if p is not None:
        tg.bot.send_message(chat_id, (note + "\n\n" if note else "") + profile_text(p), reply_markup=profile_kb(c, p))


def list_text(c: Cardinal) -> str:
    count = len(c.settings["profiles"])
    return f"🎯 <b>Профили товаров — {escape(c.label)}</b>\n\n{INTRO}\n\nПрофилей: {count}"


def list_kb(c: Cardinal) -> K:
    kb = K()
    for raw in c.settings["profiles"]:
        p = normalize(raw)
        kb.row(B(f"{kbs.dot(p['enabled'])} {tools.short(p['title'], TITLE_MAX)}",
                 callback_data=kbs.cb("pf1", c.id, p["id"])))
    kb.row(B("➕ Новый профиль", callback_data=kbs.cb("pfn", c.id)))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def cb_list(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, list_text(c), list_kb(c))
    tg.answer(call)


def check_title(text: str | None) -> str | None:
    title = (text or "").strip()
    return title if title and len(title) <= TITLE_MAX else None


def cb_new(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return

    def on_title(m: Message):
        title = check_title(m.text)
        if title is None:
            tg.bot.send_message(m.chat.id, f"❌ Название пустое или длиннее {TITLE_MAX} символов",
                                reply_markup=kbs.back_kb(kbs.cb("pf", c.id)))
            return
        holder = {}

        def add(s: dict):
            holder["id"] = new_id(s["profiles"], "p")
            s["profiles"].append(new_profile(holder["id"], title))

        c.update_settings(add)
        send_profile(tg, m.chat.id, c, holder["id"], "✅ Профиль создан. Задайте условия — без них он не работает.")

    tg.ask(call.message.chat.id, call.from_user.id,
           f"🎯 Название профиля (до {TITLE_MAX} символов), например «Robux». /cancel — отмена.", on_title)
    tg.answer(call)


def cb_screen(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    tg.edit(call, profile_text(p), profile_kb(c, p))
    tg.answer(call)


def cb_toggle(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    change(c, p["id"], lambda x: x.__setitem__("enabled", not x["enabled"]))
    p = find(c, p["id"])
    tg.edit(call, profile_text(p), profile_kb(c, p))
    tg.answer(call)


def cb_raise_mode(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    parts = call.data.split(":")
    mode = parts[3] if len(parts) > 3 else ""
    if mode not in RAISE_MODES:
        tg.answer(call, "Нет такого пункта", alert=True)
        return

    def set_mode(x: dict):
        if mode == "custom" and x["raise"]["mode"] != "custom":
            x["raise"].update(interval=int(c.number("raiseInterval")), max_price=int(c.number("raiseMaxPrice")))
        x["raise"]["mode"] = mode

    change(c, p["id"], set_mode)
    p = find(c, p["id"])
    tg.edit(call, profile_text(p), profile_kb(c, p))
    tg.answer(call)


EDITS = {
    "title": f"✏️ Новое название (до {TITLE_MAX} символов).",
    "words": f"🔤 Слова, которые должны быть в названии товара. {LIST_PROMPT}",
    "price": "💵 Цена товара от и до двумя числами, например <code>100 500</code>. <code>0</code> — без границы.",
    "places": f"🎮 Игра или категория товара. {LIST_PROMPT}",
    "interval": "⏱ Как часто поднимать, в минутах (от {0} до {1}).".format(*NUMBER_LIMITS["raiseInterval"]),
    "maxprice": "💰 Максимальная цена одного поднятия, ₽ (от {0} до {1}).".format(*NUMBER_LIMITS["raiseMaxPrice"]),
}


def apply_edit(field: str, text: str | None) -> tuple[Callable[[dict], None] | None, str]:
    if field == "title":
        title = check_title(text)
        return (lambda p: p.__setitem__("title", title)) if title else None, \
            f"Название пустое или длиннее {TITLE_MAX} символов"
    if field in ("words", "places"):
        items = parse_list(text)
        return (lambda p: p["filters"].__setitem__(field, items)) if items is not None else None, \
            "Нужно хотя бы одно значение (или «-», чтобы убрать условие)"
    if field == "price":
        bounds = parse_price(text)
        return (lambda p: p["filters"].update(price_min=bounds[0], price_max=bounds[1])) if bounds else None, \
            "Нужны два целых числа от 0, например «100 500»"
    key = "raiseInterval" if field == "interval" else "raiseMaxPrice"
    value = parse_limit(text, key)
    target = "interval" if field == "interval" else "max_price"
    low, high = NUMBER_LIMITS[key]
    return (lambda p: p["raise"].__setitem__(target, value)) if value is not None else None, \
        f"Нужно целое число от {low} до {high}"


def cb_edit(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    parts = call.data.split(":")
    field = parts[3] if len(parts) > 3 else ""
    if field not in EDITS:
        tg.answer(call, "Нет такого пункта", alert=True)
        return

    def on_answer(m: Message):
        fn, error = apply_edit(field, m.text)
        if fn is None:
            tg.bot.send_message(m.chat.id, f"❌ {error}, ничего не изменено",
                                reply_markup=kbs.back_kb(kbs.cb("pf1", c.id, p["id"])))
            return
        change(c, p["id"], fn)
        send_profile(tg, m.chat.id, c, p["id"], "✅ Сохранено")

    tg.ask(call.message.chat.id, call.from_user.id, f"{EDITS[field]}\n/cancel — отмена.", on_answer)
    tg.answer(call)


def texts_kb(c: Cardinal, p: dict) -> K:
    kb = K()
    for kind in DEAL_REPLY_KINDS:
        mark = "✏️" if p["deal_replies"].get(kind) else "▫️"
        kb.row(B(f"{mark} {KIND_TITLES[kind]}", callback_data=kbs.cb("pfxk", c.id, p["id"], kind)))
    kb.row(B("◀️ К профилю", callback_data=kbs.cb("pf1", c.id, p["id"])))
    return kb


def cb_texts(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    tg.edit(call, f"🧾 <b>Тексты ответов — {escape(p['title'])}</b>\n\n✏️ — свой текст профиля, ▫️ — общий текст. "
                  f"Включаются ответы в «🧾 Ответы на сделки».", texts_kb(c, p))
    tg.answer(call)


def cb_text(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    parts = call.data.split(":")
    kind = parts[3] if len(parts) > 3 else ""
    if kind not in DEAL_REPLY_KINDS:
        tg.answer(call, "Нет такого пункта", alert=True)
        return

    def on_text(m: Message):
        value = (m.text or "").strip()
        if not value:
            tg.bot.send_message(m.chat.id, "❌ Текст пустой, ничего не изменено")
            return
        value = "" if value == "-" else value
        change(c, p["id"], lambda x: x["deal_replies"].__setitem__(kind, value))
        note = "✅ Свой текст убран, будет общий." if not value else "✅ Сохранено.\n" + preview(value, "deal_reply")
        send_profile(tg, m.chat.id, c, p["id"], note)

    ask_text(tg, call.message.chat.id, call.from_user.id, f"{p['title']}: {KIND_TITLES[kind]}",
             p["deal_replies"].get(kind, ""), "deal_reply", on_text, allow_clear=True)
    tg.answer(call)


def commands_kb(c: Cardinal, p: dict) -> K:
    kb = K()
    for entry in p["auto_responses"]:
        kb.row(B(f"🗑 {tools.short(' | '.join(entry.get('commands', [])), 40)}",
                 callback_data=kbs.cb("pfcd", c.id, p["id"], entry["id"])))
    kb.row(B("➕ Добавить команду", callback_data=kbs.cb("pfca", c.id, p["id"])))
    kb.row(B("◀️ К профилю", callback_data=kbs.cb("pf1", c.id, p["id"])))
    return kb


def commands_text(p: dict) -> str:
    lines = [f"🤖 <b>Команды профиля — {escape(p['title'])}</b>",
             "Если покупатель в чате купил товар этого профиля, на команду придёт этот ответ вместо общего.", ""]
    for entry in p["auto_responses"]:
        lines.append(f"<b>{escape(' | '.join(entry.get('commands', [])))}</b> → "
                     f"{escape(tools.short(entry.get('text', ''), 80))}")
    if not p["auto_responses"]:
        lines.append("Команд пока нет.")
    return "\n".join(lines)


def cb_commands(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    tg.edit(call, commands_text(p), commands_kb(c, p))
    tg.answer(call)


def cb_command_add(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return

    def on_commands(m: Message):
        commands = parse_list(m.text)
        if not commands:
            tg.bot.send_message(m.chat.id, "❌ Нужна хотя бы одна команда, например <code>!инструкция</code>")
            return

        def on_text(answer: Message):
            text = (answer.text or "").strip()
            if not text:
                tg.bot.send_message(answer.chat.id, "❌ Ответ пустой, команда не добавлена")
                return

            def add(x: dict):
                x["auto_responses"].append({"id": new_id(x["auto_responses"], "r"), "commands": commands,
                                            "text": text})

            change(c, p["id"], add)
            send_profile(tg, answer.chat.id, c, p["id"], "✅ Команда добавлена.\n" + preview(text, "auto_response"))

        ask_text(tg, m.chat.id, m.from_user.id, "Ответ на команду", "", "auto_response", on_text)

    tg.ask(call.message.chat.id, call.from_user.id,
           "🤖 Команды через <code>|</code>, например <code>!инструкция | !how</code>. /cancel — отмена.", on_commands)
    tg.answer(call)


def cb_command_delete(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    entry_id = call.data.split(":")[3] if len(call.data.split(":")) > 3 else ""
    change(c, p["id"], lambda x: x.__setitem__("auto_responses",
                                               [e for e in x["auto_responses"] if e.get("id") != entry_id]))
    p = find(c, p["id"])
    tg.edit(call, commands_text(p), commands_kb(c, p))
    tg.answer(call, "Удалено")


def cb_move(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    step = -1 if call.data.split(":")[-1] == "u" else 1

    def move(s: dict):
        items = s["profiles"]
        i = next(n for n, x in enumerate(items) if x.get("id") == p["id"])
        j = i + step
        if 0 <= j < len(items):
            items[i], items[j] = items[j], items[i]

    c.update_settings(move)
    tg.edit(call, profile_text(p), profile_kb(c, p))
    tg.answer(call, "Порядок изменён")


def cb_delete(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    tg.bot.send_message(call.message.chat.id, f"🗑 Удалить профиль <b>{escape(p['title'])}</b>?",
                        reply_markup=kbs.confirm_kb("pfd", c.id, p["id"]))
    tg.answer(call)


def cb_delete_yes(tg: TGBot, call: CallbackQuery):
    c, p = profile_of(tg, call)
    if p is None:
        return
    c.update_settings(lambda s: s.__setitem__("profiles", [x for x in s["profiles"] if x.get("id") != p["id"]]))
    tg.edit(call, "🗑 Профиль удалён.", kbs.back_kb(kbs.cb("pf", c.id)))
    tg.answer(call, "Удалено")


def register(tg: TGBot):
    for prefix, handler in {"pf:": cb_list, "pfn:": cb_new, "pf1:": cb_screen, "pft:": cb_toggle,
                            "pfr:": cb_raise_mode, "pfe:": cb_edit, "pfx:": cb_texts, "pfxk:": cb_text,
                            "pfc:": cb_commands, "pfca:": cb_command_add, "pfcd:": cb_command_delete,
                            "pfm:": cb_move, "pfd:": cb_delete, "pfd_yes:": cb_delete_yes}.items():
        tg.on_prefix(prefix, handler)
