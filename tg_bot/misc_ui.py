from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import config as cfg
from Utils import tools
from Utils.migration import settings_from_v1
from playerokapi.enums import ItemDealStatuses, TransactionDirections, TransactionOperations, TransactionStatuses
from tg_bot import keyboards as kbs
from tg_bot import templates_ui

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

MAX_UPLOAD = 5 * 1024 * 1024
STATUS_TEXT = {"running": "работает", "starting": "запускается", "error": "ошибка", "disabled": "выключен",
               "stopped": "остановлен"}
CONFIG_NAMES = {"_main.cfg", "auto_response.cfg", "auto_delivery.cfg"}


def account_line(c: Cardinal) -> str:
    line = f"{kbs.status_icon(c)} <b>{escape(c.label)}</b> — {STATUS_TEXT.get(c.status, c.status)}"
    if c.status == "error" and c.error:
        line += f"\n   <i>{escape(tools.short(c.error, 120))}</i>"
    return line


def menu_text(tg: TGBot, c: Cardinal | None) -> str:
    hub = tg.hub
    head = "⚙️ <b>Playerok Cardinal</b>\n\n"
    if c is None:
        return head + "Аккаунтов пока нет. Нажмите «➕ Добавить аккаунт»."
    return (head + f"👤 {account_line(c)}\n"
            f"👥 Аккаунтов: {len(hub.cardinals)}\n"
            f"⏱ Работает: {tools_uptime(hub)}\n"
            f"🧩 Плагинов: {len(hub.plugins.plugins)}\n\n"
            f"🟢 — включено, 🔴 — выключено")


def tools_uptime(hub) -> str:
    from tg_bot.bot import uptime
    return uptime(time.time() - hub.start_time)


def cmd_menu(tg: TGBot, m: Message):
    c = tg.current(m.from_user.id)
    tg.bot.send_message(m.chat.id, menu_text(tg, c), reply_markup=tg.menu_markup(m.from_user.id, c))


def cb_menu(tg: TGBot, call: CallbackQuery):
    tg.pending.pop(call.from_user.id, None)
    c = tg.current(call.from_user.id)
    tg.edit(call, menu_text(tg, c), tg.menu_markup(call.from_user.id, c))
    tg.answer(call)


def section_text(c: Cardinal, key: str) -> str:
    title, description, _ = kbs.SECTIONS[key]
    text = f"{title} — <b>{escape(c.label)}</b>\n\n{description}"
    if key in kbs.SECTION_TOGGLES:
        text += "\n\n🟢 — включено, 🔴 — выключено"
    return text


def show_section(tg: TGBot, call: CallbackQuery, c: Cardinal, key: str):
    tg.edit(call, section_text(c, key), kbs.section_menu(c, key, lambda data: tg.can_call(call.from_user.id, data)))


def cb_section(tg: TGBot, call: CallbackQuery):
    parts = call.data.split(":")
    c = tg.account_from(call)
    if c is None:
        return
    key = parts[2] if len(parts) > 2 else ""
    if key not in kbs.SECTIONS:
        tg.answer(call, "Раздел не найден")
        return
    tg.pending.pop(call.from_user.id, None)
    show_section(tg, call, c, key)
    tg.answer(call)


def cb_close(tg: TGBot, call: CallbackQuery):
    try:
        tg.bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    tg.answer(call)


def cb_reply(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    chat_id = call.data.split(":", 2)[2]

    templates = templates_ui.reply_kb(c, chat_id)
    holder = {}

    def on_answer(m: Message):
        prompt = holder.get("prompt")
        if templates.keyboard and prompt is not None:
            templates_ui.claim_prompt(prompt.chat.id, prompt.message_id)
            templates_ui.close_prompt(tg, prompt.chat.id, prompt.message_id)
        ok = c.send_message(chat_id, m.text, mirror="owner")
        tg.bot.reply_to(m, "✅ Сообщение отправлено" if ok else "❌ Не удалось отправить сообщение",
                        reply_markup=kbs.message_kb(c.id, chat_id))

    text = "✏️ Напишите ответ покупателю (или /cancel)" + (" — или выберите шаблон:" if templates.keyboard else ":")
    holder["prompt"] = tg.ask(call.message.chat.id, call.from_user.id, text, on_answer,
                              keyboard=templates if templates.keyboard else None)
    tg.answer(call)


def cb_sent(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    deal_id = call.data.split(":", 2)[2]
    tg.bot.send_message(call.message.chat.id, "📤 Отметить сделку как отправленную?",
                        reply_markup=kbs.confirm_kb("sent", c.id, deal_id))
    tg.answer(call)


def cb_refund(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    deal_id = call.data.split(":", 2)[2]
    tg.bot.send_message(call.message.chat.id, "↩️ <b>Вернуть деньги покупателю?</b>\nЭто нельзя отменить.",
                        reply_markup=kbs.confirm_kb("refund", c.id, deal_id))
    tg.answer(call)


def _update_deal(tg: TGBot, call: CallbackQuery, status: ItemDealStatuses, done: str):
    c = tg.account_from(call)
    if c is None:
        return
    deal_id = call.data.split(":", 2)[2]
    try:
        c.account.update_deal(deal_id, status)
        c.log.info(f"Telegram: сделка {deal_id} → {status.name}")
        tg.edit(call, done, kbs.deal_markup(c, deal_id))
        tg.answer(call, "Готово")
    except Exception as e:
        tg.answer(call, f"Ошибка: {e}"[:190], alert=True)


def cb_sent_yes(tg: TGBot, call: CallbackQuery):
    _update_deal(tg, call, ItemDealStatuses.SENT, "📤 Сделка отмечена как отправленная")


def cb_refund_yes(tg: TGBot, call: CallbackQuery):
    _update_deal(tg, call, ItemDealStatuses.ROLLED_BACK, "↩️ Деньги возвращены покупателю")


def plugins_text(tg: TGBot) -> str:
    if not tg.hub.plugins.plugins:
        return "🧩 <b>Плагины</b>\n\nПлагинов пока нет.\nОтправьте сюда .py файл плагина, затем /restart."
    return (f"🧩 <b>Плагины</b> ({len(tg.hub.plugins.plugins)})\n\n"
            "🟢/🔴 — включён ли плагин для выбранного аккаунта. Новый плагин — отправьте .py файл.")


def cmd_plugins(tg: TGBot, m: Message):
    c = tg.current(m.from_user.id)
    tg.bot.send_message(m.chat.id, plugins_text(tg), reply_markup=kbs.plugins_menu(tg.hub, c.id if c else "-", 0))


def cb_plugins(tg: TGBot, call: CallbackQuery):
    _, acc_id, page = call.data.split(":")
    tg.edit(call, plugins_text(tg), kbs.plugins_menu(tg.hub, acc_id, int(page)))
    tg.answer(call)


def _plugin_parts(tg: TGBot, call: CallbackQuery):
    _, uuid, acc_id, page = call.data.split(":")
    if uuid not in tg.hub.plugins.plugins:
        tg.answer(call, "Плагин не найден", alert=True)
        return None
    return uuid, acc_id, int(page)


def plugin_text(tg: TGBot, uuid: str, acc_id: str) -> str:
    p = tg.hub.plugins.plugins[uuid]
    c = tg.hub.cardinals.get(acc_id)
    state = ""
    if c is not None:
        on = c.settings["plugins"].get(uuid, True)
        state = f"\nДля <b>{escape(c.label)}</b>: {'🟢 включён' if on else '🔴 выключен'}"
    return (f"🧩 <b>{escape(p.name)}</b> v{escape(p.version)}\n\n{escape(p.description)}\n\n"
            f"👨‍💻 Автор: {escape(p.credits)}\n🆔 <code>{p.uuid}</code>\n"
            f"📄 <code>{escape(os.path.basename(p.path))}</code>{state}")


def cb_plugin(tg: TGBot, call: CallbackQuery):
    parts = _plugin_parts(tg, call)
    if parts:
        uuid, acc_id, page = parts
        tg.edit(call, plugin_text(tg, uuid, acc_id), kbs.plugin_kb(tg.hub, acc_id, uuid, page))
        tg.answer(call)


def cb_plugin_toggle(tg: TGBot, call: CallbackQuery):
    parts = _plugin_parts(tg, call)
    if not parts:
        return
    uuid, acc_id, page = parts
    c = tg.account_from(call, 2)
    if c is None:
        return
    on = c.toggle_plugin(uuid)
    tg.edit(call, plugin_text(tg, uuid, acc_id), kbs.plugin_kb(tg.hub, acc_id, uuid, page))
    tg.answer(call, "Плагин включён" if on else "Плагин выключен")


def cb_plugin_commands(tg: TGBot, call: CallbackQuery):
    parts = _plugin_parts(tg, call)
    if parts:
        uuid, acc_id, page = parts
        p = tg.hub.plugins.plugins[uuid]
        lines = [f"/{cmd} — {escape(desc)}" for cmd, desc in p.commands.items()] or ["Команд нет"]
        tg.edit(call, f"⌨️ <b>Команды «{escape(p.name)}»</b>\n\n" + "\n".join(lines),
                kbs.back_kb(kbs.cb("plg", uuid, acc_id, page)))
        tg.answer(call)


def cb_plugin_delete(tg: TGBot, call: CallbackQuery):
    parts = _plugin_parts(tg, call)
    if parts:
        uuid, acc_id, page = parts
        p = tg.hub.plugins.plugins[uuid]
        tg.bot.send_message(call.message.chat.id,
                            f"🗑 Удалить плагин <b>{escape(p.name)}</b> для всех аккаунтов?\n"
                            f"Файл <code>{escape(os.path.basename(p.path))}</code> будет удалён.",
                            reply_markup=kbs.confirm_kb("pldel", uuid, acc_id, page))
        tg.answer(call)


def cb_plugin_delete_yes(tg: TGBot, call: CallbackQuery):
    parts = _plugin_parts(tg, call)
    if parts:
        uuid, acc_id, _ = parts
        name = tg.hub.plugins.plugins[uuid].name
        tg.hub.plugins.delete(uuid, tg.hub)
        tg.edit(call, f"🗑 Плагин <b>{escape(name)}</b> удалён.", kbs.back_kb(kbs.cb("pl", acc_id, 0)))
        tg.answer(call, "Удалено")


def pending_release_lines(c: Cardinal, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    cursor, seen, deadlines = None, set(), []
    while True:
        page = c.account.get_transactions(status=TransactionStatuses.PROCESSING, operation=TransactionOperations.SELL,
                                          count=24, after_cursor=cursor)
        for transaction in page.transactions:
            if (transaction.status != TransactionStatuses.PROCESSING or
                    transaction.operation != TransactionOperations.SELL or
                    transaction.direction != TransactionDirections.IN):
                continue
            raw = getattr(transaction, "status_expiration_date", None)
            if not raw:
                continue
            try:
                deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    continue
                deadlines.append((deadline.astimezone(timezone.utc), getattr(transaction, "value", None)))
            except (ValueError, TypeError, AttributeError):
                continue
        info = page.page_info
        if not isinstance(info.has_next_page, bool):
            raise ValueError("Playerok не сообщил число страниц")
        if not info.has_next_page:
            break
        cursor = info.end_cursor
        if not cursor or cursor in seen:
            raise ValueError("Playerok вернул неверную страницу")
        seen.add(cursor)
    if not deadlines:
        return []
    lines = ["", "⏳ <b>Сроки удержания по продажам</b>"]
    for deadline, value in sorted(deadlines, key=lambda row: row[0])[:5]:
        seconds = max(0, int((deadline - now).total_seconds()))
        minutes = (seconds + 59) // 60
        days, minutes = divmod(minutes, 1440)
        hours, minutes = divmod(minutes, 60)
        remaining = f"{days} д. {hours} ч. {minutes} мин." if days else f"{hours} ч. {minutes} мин."
        amount = f"{value} ₽ — " if isinstance(value, (int, float)) else ""
        lines.append(f"• {amount}{'ещё ' + remaining if seconds else 'срок истёк, ожидается обновление Playerok'}")
    if len(deadlines) > 5:
        lines.append(f"И ещё операций: {len(deadlines) - 5}")
    lines.append("<i>Суммы и сроки операций указаны Playerok и могут измениться; доступность денег определяет площадка.</i>")
    return lines


def profile_text(c: Cardinal) -> str:
    acc = c.account.get()
    p = getattr(acc, "profile", None)
    lines = [f"👤 <b>{escape(str(acc.username))}</b>"]
    if p is not None:
        if p.balance is not None:
            lines.append(f"💰 Доступно: <b>{p.balance.available} ₽</b>")
            withdrawable = getattr(p.balance, "withdrawable", None)
            lines.append(f"💸 Доступно для вывода: <b>{withdrawable} ₽</b>" if withdrawable is not None
                         else "💸 Доступно для вывода: не удалось получить")
            lines.append(f"💼 Всего: {p.balance.value} ₽, заморожено: {p.balance.frozen} ₽")
            lines.append(f"⏳ Ожидает зачисления: {p.balance.pending_income} ₽")
        lines.append(f"⭐ Рейтинг: {p.rating} ({p.reviews_count} отзывов)")
    lines.append(f"🌐 {tools.profile_link(acc.username)}")
    try:
        lines.extend(pending_release_lines(c))
    except Exception:
        lines += ["", "⏳ Сроки удержания: не удалось получить от Playerok."]
    return "\n".join(lines)


def profile_kb(c: Cardinal) -> K:
    kb = K()
    kb.row(B("📊 Статистика продаж", callback_data=kbs.cb("st", c.id, "w")))
    kb.row(B("⏳ Ожидают подтверждения", callback_data=kbs.cb("dl", c.id, "s", 0)))
    kb.row(B("◀️ В меню", callback_data="menu"))
    return kb


def cb_profile(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    try:
        tg.edit(call, profile_text(c), profile_kb(c))
        tg.answer(call)
    except Exception as e:
        tg.answer(call, f"Ошибка: {e}"[:190], alert=True)


def cmd_profile(tg: TGBot, m: Message):
    c = tg.current(m.from_user.id)
    if c is None:
        tg.bot.send_message(m.chat.id, "Аккаунтов пока нет")
        return
    try:
        tg.bot.send_message(m.chat.id, profile_text(c), reply_markup=profile_kb(c), disable_web_page_preview=True)
    except Exception as e:
        tg.bot.send_message(m.chat.id, f"❌ Не удалось получить профиль: {escape(str(e))}")


def send_logs(tg: TGBot, chat_id: int):
    path = "logs/log.log"
    if not os.path.exists(path):
        tg.bot.send_message(chat_id, "📜 Логов пока нет")
        return
    with open(path, "rb") as f:
        tg.bot.send_document(chat_id, f, visible_file_name="log.txt")


def cmd_logs(tg: TGBot, m: Message):
    send_logs(tg, m.chat.id)


def cb_logs(tg: TGBot, call: CallbackQuery):
    send_logs(tg, call.message.chat.id)
    tg.answer(call)


def cmd_restart(tg: TGBot, m: Message):
    tg.bot.send_message(m.chat.id, "♻️ Перезапускаю...")
    threading.Timer(1.5, tg.hub.restart).start()


def cmd_help(tg: TGBot, m: Message):
    from tg_bot.bot import BUILTIN_COMMANDS
    lines = ["❓ <b>Команды</b>\n"] + [f"/{c} — {d}" for c, d in BUILTIN_COMMANDS]
    lines += ["/ban ник — ⛔ в чёрный список", "/unban ник — убрать из чёрного списка", "/cancel — отменить ввод"]
    for p in tg.hub.plugins.plugins.values():
        if p.commands:
            lines.append(f"\n🧩 <b>{escape(p.name)}</b>")
            lines += [f"/{c} — {escape(d)}" for c, d in p.commands.items()]
    tg.bot.send_message(m.chat.id, "\n".join(lines))


def _arg(m: Message) -> str:
    parts = m.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def cmd_ban(tg: TGBot, m: Message):
    name = _arg(m)
    if not name:
        tg.bot.send_message(m.chat.id, "Использование: <code>/ban ник</code>")
        return
    added = tg.hub.ban(name)
    tg.bot.send_message(m.chat.id, f"⛔ {escape(name)} добавлен в чёрный список" if added
                        else f"{escape(name)} уже в чёрном списке")


def cmd_unban(tg: TGBot, m: Message):
    name = _arg(m)
    if not name:
        tg.bot.send_message(m.chat.id, "Использование: <code>/unban ник</code>")
        return
    removed = tg.hub.unban(name)
    tg.bot.send_message(m.chat.id, f"✅ {escape(name)} убран из чёрного списка" if removed
                        else f"{escape(name)} не найден в чёрном списке")


def cmd_blacklist(tg: TGBot, m: Message):
    names = tg.hub.blacklist
    if not names:
        tg.bot.send_message(m.chat.id, "⛔ Чёрный список пуст.\nДобавить: <code>/ban ник</code>")
        return
    tg.bot.send_message(m.chat.id, "⛔ <b>Чёрный список</b>\n\n" + "\n".join(f"• {escape(n)}" for n in names))


def cmd_cancel(tg: TGBot, m: Message):
    tg.pending.pop(m.from_user.id, None)
    tg.bot.send_message(m.chat.id, "❎ Отменено")


def handle_upload(tg: TGBot, m: Message):
    doc = m.document
    name = os.path.basename(doc.file_name or "")
    lower = name.lower()
    if doc.file_size and doc.file_size > MAX_UPLOAD:
        tg.bot.reply_to(m, "❌ Файл больше 5 МБ")
        return
    if lower.endswith(".py") and cfg.is_safe_filename(name, ".py"):
        target = "plugin"
    elif lower.endswith(".txt") and cfg.is_safe_filename(name, ".txt"):
        target = "products"
    elif name in CONFIG_NAMES:
        target = "config"
    else:
        tg.bot.reply_to(m, "🤔 Я принимаю: .py (плагин), .txt (товары), "
                           "_main.cfg / auto_response.cfg / auto_delivery.cfg")
        return
    c = tg.current(m.from_user.id)
    if target != "plugin" and c is None:
        tg.bot.reply_to(m, "Сначала добавьте аккаунт")
        return
    data = tg.bot.download_file(tg.bot.get_file(doc.file_id).file_path)
    if target == "plugin":
        save_plugin(tg, m, name, data)
    elif target == "products":
        save_products(tg, m, c, name, data)
    else:
        save_config(tg, m, c, name, data)


def save_plugin(tg: TGBot, m: Message, name: str, data: bytes):
    try:
        compile(data.decode("utf-8"), name, "exec")
    except (SyntaxError, UnicodeDecodeError) as e:
        tg.bot.reply_to(m, f"❌ Ошибка в коде плагина:\n<code>{escape(str(e))}</code>")
        return
    os.makedirs("plugins", exist_ok=True)
    with open(os.path.join("plugins", name), "wb") as f:
        f.write(data)
    tg.bot.reply_to(m, f"🧩 Плагин <code>{escape(name)}</code> сохранён.\nЧтобы он заработал — /restart")


def save_products(tg: TGBot, m: Message, c: Cardinal, name: str, data: bytes):
    try:
        lines = data.decode("utf-8-sig").split("\n")
    except UnicodeDecodeError:
        tg.bot.reply_to(m, "❌ Файл должен быть в кодировке UTF-8")
        return
    c.storage.write_products(name, [])
    count = c.storage.add_products(name, lines)
    c.log.info(f"Telegram: загружен файл товаров {name} ({count} шт.)")
    tg.bot.reply_to(m, f"🗂 <b>{escape(c.label)}</b>: файл <code>{escape(name)}</code> сохранён, {count} товаров")


def save_config(tg: TGBot, m: Message, c: Cardinal, name: str, data: bytes):
    tmp = os.path.join(c.storage.root, name + ".upload")
    os.makedirs(c.storage.root, exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        if name == "_main.cfg":
            converted = settings_from_v1(cfg.load_main(tmp), None, None)
            keys = ("toggles", "texts", "numbers", "notifications", "raise_items")
        elif name == "auto_response.cfg":
            converted = settings_from_v1(None, cfg.load_auto_response(tmp), None)
            keys = ("auto_responses",)
        else:
            converted = settings_from_v1(None, None, cfg.load_auto_delivery(tmp, c.storage.products_dir))
            keys = ("auto_delivery",)
    except cfg.ConfigError as e:
        tg.bot.reply_to(m, f"❌ Конфиг не принят:\n<code>{escape(e.text)}</code>")
        return
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    c.update_settings(lambda s: s.update({key: converted[key] for key in keys}))
    c.log.info(f"Telegram: загружен конфиг {name}")
    tg.bot.reply_to(m, f"✅ <b>{escape(c.label)}</b>: конфиг <code>{escape(name)}</code> применён")


def register(tg: TGBot):
    for command, handler in {"start": cmd_menu, "menu": cmd_menu, "profile": cmd_profile, "plugins": cmd_plugins,
                             "logs": cmd_logs, "restart": cmd_restart, "help": cmd_help, "ban": cmd_ban,
                             "unban": cmd_unban, "blacklist": cmd_blacklist, "cancel": cmd_cancel}.items():
        tg.on_command(command, handler)
    for data, handler in {"menu": cb_menu, "close": cb_close, "logs": cb_logs}.items():
        tg.on_exact(data, handler)
    for prefix, handler in {"reply:": cb_reply, "sent:": cb_sent, "sent_yes:": cb_sent_yes, "refund:": cb_refund,
                            "refund_yes:": cb_refund_yes, "pl:": cb_plugins, "plg:": cb_plugin,
                            "pltgl:": cb_plugin_toggle, "plcmd:": cb_plugin_commands, "pldel:": cb_plugin_delete,
                            "pldel_yes:": cb_plugin_delete_yes, "prof:": cb_profile, "mn:": cb_section}.items():
        tg.on_prefix(prefix, handler)
