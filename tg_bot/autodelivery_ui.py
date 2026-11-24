from __future__ import annotations

import os
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.settings import new_id, validate_delivery
from playerokapi.enums import ItemStatuses
from tg_bot import keyboards as kbs
from tg_bot.settings_ui import ask_text, preview

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

PER_PAGE = 8
MAX_PICK = 30
ON_MODERATION = (ItemStatuses.PENDING_APPROVAL, ItemStatuses.PENDING_MODERATION)
PICK_STATUSES = [ItemStatuses.APPROVED, *ON_MODERATION]
MAX_UPLOAD = 5 * 1024 * 1024


def has_product(text: str) -> bool:
    return "#product" in text or "$product" in text


def entry_of(tg: TGBot, call: CallbackQuery) -> tuple[Cardinal | None, dict | None]:
    c = tg.account_from(call)
    if c is None:
        return None, None
    entry_id = call.data.split(":")[2]
    entry = next((e for e in c.settings["auto_delivery"] if e["id"] == entry_id), None)
    if entry is None:
        tg.answer(call, "Запись не найдена", alert=True)
    return c, entry


def _set(c: Cardinal, entry_id: str, **fields):
    def change(s: dict):
        target = next(x for x in s["auto_delivery"] if x["id"] == entry_id)
        target.update(fields)
    c.update_settings(change)


def _get(c: Cardinal, entry_id: str) -> dict | None:
    return next((x for x in c.settings["auto_delivery"] if x["id"] == entry_id), None)


def stock_line(c: Cardinal, e: dict) -> str:
    if not e.get("products_file"):
        return "без склада (текст без #product)"
    return f"{c.storage.count_products(e['products_file'])} шт."


def list_text(c: Cardinal) -> str:
    count = len(c.settings["auto_delivery"])
    return (f"📦 <b>Автовыдача — {escape(c.label)}</b>\n\n"
            + (f"Товаров: {count}. Нажмите, чтобы изменить." if count else "Товаров пока нет."))


def list_kb(c: Cardinal, page: int) -> K:
    entries = c.settings["auto_delivery"]
    pages = max(1, (len(entries) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    kb = K()
    for e in entries[page * PER_PAGE:(page + 1) * PER_PAGE]:
        stock = f" ({c.storage.count_products(e['products_file'])})" if e.get("products_file") else ""
        kb.row(B(f"{kbs.dot(e.get('enabled', True))} {tools.short(e['item'], 34)}{stock}",
                 callback_data=kbs.cb("ad1", c.id, e["id"])))
    nav = []
    if page > 0:
        nav.append(B("⬅️", callback_data=kbs.cb("ad", c.id, page - 1)))
    if page < pages - 1:
        nav.append(B("➡️", callback_data=kbs.cb("ad", c.id, page + 1)))
    if nav:
        kb.row(*nav)
    kb.row(B("➕ Новый товар", callback_data=kbs.cb("adnew", c.id)))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def entry_text(c: Cardinal, e: dict) -> str:
    return (f"📦 <b>{escape(e['item'])}</b>\n"
            f"Состояние: {'🟢 включена' if e.get('enabled', True) else '🔴 выключена'}\n\n"
            f"Сообщение покупателю:\n<blockquote>{escape(e['text'])}</blockquote>\n"
            f"🗂 Склад: {stock_line(c, e)}")


def entry_kb(c: Cardinal, e: dict) -> K:
    kb = K()
    kb.row(B("✏️ Текст", callback_data=kbs.cb("adtxt", c.id, e["id"])),
           B(f"{kbs.dot(e.get('enabled', True))} Вкл/выкл", callback_data=kbs.cb("adtgl", c.id, e["id"])))
    if e.get("products_file"):
        kb.row(B("🗂 Склад", callback_data=kbs.cb("adst", c.id, e["id"])))
    kb.row(B("🗑 Удалить", callback_data=kbs.cb("addel", c.id, e["id"])))
    kb.row(B("◀️ К списку", callback_data=kbs.cb("ad", c.id, 0)))
    return kb


def send_entry(tg: TGBot, chat_id: int, c: Cardinal, entry_id: str, note: str = ""):
    e = _get(c, entry_id)
    if e is not None:
        tg.bot.send_message(chat_id, (note + "\n\n" if note else "") + entry_text(c, e), reply_markup=entry_kb(c, e))


def stock_text(c: Cardinal, e: dict) -> str:
    return (f"🗂 <b>Склад «{escape(e['item'])}»</b>\n\nОсталось: {stock_line(c, e)}\n"
            "Одна строка — один товар (код, ключ, данные аккаунта).")


def stock_kb(c: Cardinal, e: dict) -> K:
    kb = K()
    kb.row(B("➕ Добавить", callback_data=kbs.cb("adadd", c.id, e["id"])),
           B("📤 Скачать", callback_data=kbs.cb("adget", c.id, e["id"])))
    kb.row(B("🧹 Очистить", callback_data=kbs.cb("adclr", c.id, e["id"])))
    kb.row(B("◀️ К товару", callback_data=kbs.cb("ad1", c.id, e["id"])))
    return kb


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
    refresh = call.data.split(":")[2:3] == ["r"]
    try:
        items = c.my_items(PICK_STATUSES)[:MAX_PICK]
        failed = False
    except Exception as e:
        c.log.warning(f"Не удалось получить товары: {e}")
        items, failed = [], True
    names = [i.name for i in items]
    tg.flows[call.from_user.id] = {"items": names}
    kb = K()
    for index, item in enumerate(items):
        mark = "🕓" if getattr(item, "status", None) in ON_MODERATION else "📋"
        kb.row(B(f"{mark} {tools.short(item.name, 40)}", callback_data=kbs.cb("adpick", c.id, index)))
    kb.row(B("🔄 Обновить список лотов", callback_data=kbs.cb("adnew", c.id, "r")))
    kb.row(B("✏️ Ввести название", callback_data=kbs.cb("adname", c.id)))
    kb.row(B("◀️ Назад", callback_data=kbs.cb("ad", c.id, 0)))
    if failed:
        body = "Не удалось получить лоты с Playerok — нажмите «🔄 Обновить» или введите название вручную."
    elif names:
        body = ("Выберите товар из своих лотов на Playerok или введите название вручную (точно как на сайте).\n"
                "🕓 — лот ещё на проверке модерацией.")
    else:
        body = "Активных лотов не нашлось — нажмите «🔄 Обновить» или введите название товара точно как на сайте."
    tg.edit(call, "📦 <b>Новый товар для автовыдачи</b>\n\n" + body, kb)
    if failed:
        tg.answer(call, "Не удалось получить лоты с Playerok")
    elif refresh:
        tg.answer(call, f"🔄 Список обновлён: лотов {len(names)}")
    else:
        tg.answer(call)


def _ask_text_for_new(tg: TGBot, chat_id: int, user_id: int, c: Cardinal, name: str):
    name = name.strip()
    if not name:
        tg.bot.send_message(chat_id, "❌ Пустое название")
        return
    if any(e["item"].strip().lower() == name.lower() for e in c.settings["auto_delivery"]):
        tg.bot.send_message(chat_id, f"❌ Товар «{escape(name)}» уже есть в автовыдаче",
                            reply_markup=kbs.back_kb(kbs.cb("ad", c.id, 0)))
        return

    def on_text(m: Message):
        text = (m.text or "").strip()
        entry_id = new_id(c.settings["auto_delivery"], "d")
        entry = {"id": entry_id, "item": name, "text": text,
                 "products_file": f"{entry_id}.txt" if has_product(text) else "", "enabled": True}
        error = validate_delivery(entry)
        if error:
            tg.bot.send_message(m.chat.id, f"❌ {escape(error)}")
            return
        c.update_settings(lambda s: s["auto_delivery"].append(entry))
        note = "✅ Товар добавлен в автовыдачу.\n" + preview(text, "delivery")
        if entry["products_file"]:
            note += "\n\nТеперь добавьте товары на склад: «🗂 Склад» → «➕ Добавить»."
        send_entry(tg, m.chat.id, c, entry_id, note)

    ask_text(tg, chat_id, user_id, f"Сообщение для «{name}»", "", "delivery", on_text)


def cb_pick(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    names = tg.flows.get(call.from_user.id, {}).get("items", [])
    index = int(call.data.split(":")[2])
    if index >= len(names):
        tg.answer(call, "Список устарел, откройте заново", alert=True)
        return
    tg.answer(call)
    _ask_text_for_new(tg, call.message.chat.id, call.from_user.id, c, names[index])


def cb_name(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.ask(call.message.chat.id, call.from_user.id,
           "✏️ Пришлите название товара точно как на Playerok. /cancel — отмена.",
           lambda m: _ask_text_for_new(tg, m.chat.id, m.from_user.id, c, m.text or ""))
    tg.answer(call)


def cb_entry(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return
    tg.edit(call, entry_text(c, e), entry_kb(c, e))
    tg.answer(call)


def cb_text(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return

    def on_text(m: Message):
        text = (m.text or "").strip()
        products_file = (e.get("products_file") or f"{e['id']}.txt") if has_product(text) else ""
        error = validate_delivery({"text": text, "products_file": products_file})
        if error:
            tg.bot.send_message(m.chat.id, f"❌ {escape(error)}")
            return
        _set(c, e["id"], text=text, products_file=products_file)
        send_entry(tg, m.chat.id, c, e["id"], "✅ Текст сохранён.\n" + preview(text, "delivery"))

    ask_text(tg, call.message.chat.id, call.from_user.id, f"Сообщение для «{e['item']}»", e["text"], "delivery",
             on_text)
    tg.answer(call)


def cb_toggle(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return
    _set(c, e["id"], enabled=not e.get("enabled", True))
    e = _get(c, e["id"])
    tg.edit(call, entry_text(c, e), entry_kb(c, e))
    tg.answer(call, "Включено" if e["enabled"] else "Выключено")


def _stock_entry(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return None, None
    if not e.get("products_file"):
        tg.answer(call, "У товара нет склада: добавьте #product в текст", alert=True)
        return None, None
    return c, e


def cb_stock(tg: TGBot, call: CallbackQuery):
    c, e = _stock_entry(tg, call)
    if e is None:
        return
    tg.edit(call, stock_text(c, e), stock_kb(c, e))
    tg.answer(call)


def cb_add(tg: TGBot, call: CallbackQuery):
    c, e = _stock_entry(tg, call)
    if e is None:
        return

    def on_answer(m: Message):
        if getattr(m, "document", None):
            if m.document.file_size and m.document.file_size > MAX_UPLOAD:
                tg.bot.send_message(m.chat.id, "❌ Файл больше 5 МБ")
                return
            data = tg.bot.download_file(tg.bot.get_file(m.document.file_id).file_path)
            try:
                lines = data.decode("utf-8-sig").split("\n")
            except UnicodeDecodeError:
                tg.bot.send_message(m.chat.id, "❌ Файл должен быть в кодировке UTF-8")
                return
        else:
            lines = (m.text or "").split("\n")
        added = c.storage.add_products(e["products_file"], lines)
        total = c.storage.count_products(e["products_file"])
        tg.bot.send_message(m.chat.id, f"✅ Добавлено: {added}. Всего на складе: {total}",
                            reply_markup=stock_kb(c, e))

    tg.ask(call.message.chat.id, call.from_user.id,
           "➕ Пришлите товары: каждая строка — один товар. Можно отправить .txt файлом. /cancel — отмена.",
           on_answer, accept_documents=True)
    tg.answer(call)


def cb_get(tg: TGBot, call: CallbackQuery):
    c, e = _stock_entry(tg, call)
    if e is None:
        return
    path = c.storage.products_path(e["products_file"])
    if c.storage.count_products(e["products_file"]) == 0:
        tg.answer(call, "Склад пуст", alert=True)
        return
    with open(path, "rb") as f:
        tg.bot.send_document(call.message.chat.id, f, visible_file_name=e["products_file"])
    tg.answer(call)


def cb_clear(tg: TGBot, call: CallbackQuery):
    c, e = _stock_entry(tg, call)
    if e is None:
        return
    tg.bot.send_message(call.message.chat.id,
                        f"🧹 Очистить склад «{escape(e['item'])}»? Будет удалено {stock_line(c, e)}.",
                        reply_markup=kbs.confirm_kb("adclr", c.id, e["id"]))
    tg.answer(call)


def cb_clear_yes(tg: TGBot, call: CallbackQuery):
    c, e = _stock_entry(tg, call)
    if e is None:
        return
    c.storage.write_products(e["products_file"], [])
    tg.edit(call, "🧹 Склад очищен.", stock_kb(c, e))
    tg.answer(call, "Очищено")


def cb_delete(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return
    tg.bot.send_message(call.message.chat.id,
                        f"🗑 Удалить «{escape(e['item'])}» из автовыдачи?\n"
                        "Склад не удаляется, а переносится в папку <code>products/_removed/</code>.",
                        reply_markup=kbs.confirm_kb("addel", c.id, e["id"]))
    tg.answer(call)


def cb_delete_yes(tg: TGBot, call: CallbackQuery):
    c, e = entry_of(tg, call)
    if e is None:
        return
    c.update_settings(lambda s: s.__setitem__("auto_delivery", [x for x in s["auto_delivery"] if x["id"] != e["id"]]))
    products_file = e.get("products_file")
    if products_file and os.path.exists(c.storage.products_path(products_file)):
        target_dir = os.path.join(c.storage.products_dir, "_removed")
        os.makedirs(target_dir, exist_ok=True)
        os.replace(c.storage.products_path(products_file),
                   os.path.join(target_dir, f"{datetime.now():%Y%m%d-%H%M%S}-{products_file}"))
    tg.edit(call, f"🗑 «{escape(e['item'])}» удалён из автовыдачи.", kbs.back_kb(kbs.cb("ad", c.id, 0)))
    tg.answer(call, "Удалено")


def register(tg: TGBot):
    if not hasattr(tg, "flows"):
        tg.flows = {}
    for prefix, handler in {"ad:": cb_list, "adnew:": cb_new, "adpick:": cb_pick, "adname:": cb_name,
                            "ad1:": cb_entry, "adtxt:": cb_text, "adtgl:": cb_toggle, "adst:": cb_stock,
                            "adadd:": cb_add, "adget:": cb_get, "adclr:": cb_clear, "adclr_yes:": cb_clear_yes,
                            "addel:": cb_delete, "addel_yes:": cb_delete_yes}.items():
        tg.on_prefix(prefix, handler)
