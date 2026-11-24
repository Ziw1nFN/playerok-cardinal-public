from __future__ import annotations

import threading
from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, CopyTextButton, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.errors import short_error
from Utils.settings import NUMBER_LIMITS
from Utils.variables import TEXT_KINDS, VARIABLES, render, sample_values, unknown_variables
from tg_bot import chatview
from tg_bot import keyboards as kbs
from tg_bot import misc_ui

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

TEXT_TITLES = {
    "greeting": "👋 Приветствие",
    "review_thanks": "💙 Спасибо за отзыв",
    "watermark": "🔖 Подпись к сообщениям",
}

NUMBER_TITLES = {
    "greetingsCooldown": ("⏱ Пауза приветствия", "дн."),
    "restoreCheckInterval": ("⏳ Проверка истёкших", "мин"),
    "raiseInterval": ("🔁 Интервал поднятия", "мин"),
    "raiseMaxPrice": ("💸 Макс. цена поднятия", "₽"),
    "raiseDailyLimit": ("📅 Лимит в день", "₽"),
}

TOGGLE_KEYS = dict(kbs.MAIN_TOGGLES)
NOTIFY_KEYS = dict(kbs.NOTIFY_TOGGLES)


def _fmt(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def raise_text(c: Cardinal) -> str:
    n = c.settings["numbers"]
    spent = c.raise_state.get("spent", 0) if c.raise_state.get("date") else 0
    return (f"⬆️ <b>Поднятие — {escape(c.label)}</b>\n\n"
            f"Состояние: {'🟢 включено' if c.is_on('autoRaise') else '🔴 выключено'} (на Playerok это платно)\n"
            f"🔁 Интервал: {_fmt(n['raiseInterval'])} мин на товар\n"
            f"💸 Максимальная цена одного поднятия: {_fmt(n['raiseMaxPrice'])} ₽\n"
            f"📅 Лимит расходов в день: {_fmt(n['raiseDailyLimit'])} ₽ (сегодня потрачено {_fmt(spent)} ₽)\n"
            f"🔎 Поднимать только: {escape(c.settings['raise_items']) or 'все товары'}")


def raise_kb(c: Cardinal) -> K:
    kb = K()
    kb.row(B(f"{kbs.dot(c.is_on('autoRaise'))} Автоподнятие", callback_data=kbs.cb("sw", c.id, "autoRaise", "r")))
    for key in ("raiseInterval", "raiseMaxPrice", "raiseDailyLimit"):
        kb.row(B(f"{NUMBER_TITLES[key][0]}: {_fmt(c.settings['numbers'][key])} {NUMBER_TITLES[key][1]}",
                 callback_data=kbs.cb("num", c.id, key)))
    kb.row(B("🔎 Какие товары поднимать", callback_data=kbs.cb("num", c.id, "raiseItems")))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def restore_text(c: Cardinal) -> str:
    return (f"♻️ <b>Восстановление — {escape(c.label)}</b>\n\n"
            "Товары выставляются заново только бесплатно. Проданный товар не выставляется, если для него "
            "закончился склад автовыдачи.\n\n"
            f"♻️ Проданные: {'🟢' if c.is_on('autoRestoreSold') else '🔴'}\n"
            f"⏳ Истёкшие: {'🟢' if c.is_on('autoRestoreExpired') else '🔴'} "
            f"(проверка раз в {_fmt(c.settings['numbers']['restoreCheckInterval'])} мин)\n\n"
            "Проданный лот бот выставляет заново сразу после покупки. Если на Playerok у лота включено "
            "«Оставлять в продаже», бот его не трогает — лот остаётся в продаже сам. «📋 Лоты после продажи» "
            "покажет, какие лоты перевыставит бот.")


def restore_kb(c: Cardinal) -> K:
    kb = K()
    kb.row(B(f"{kbs.dot(c.is_on('autoRestoreSold'))} Восстанавливать проданные",
             callback_data=kbs.cb("sw", c.id, "autoRestoreSold", "s")))
    kb.row(B(f"{kbs.dot(c.is_on('autoRestoreExpired'))} Восстанавливать истёкшие",
             callback_data=kbs.cb("sw", c.id, "autoRestoreExpired", "s")))
    kb.row(B(f"⏳ Проверка истёкших: {_fmt(c.settings['numbers']['restoreCheckInterval'])} мин",
             callback_data=kbs.cb("num", c.id, "restoreCheckInterval")))
    kb.row(B("📋 Лоты после продажи", callback_data=kbs.cb("rsl", c.id)))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


KEEP_LIST = 25
_keep_running: set[str] = set()


def keep_report_text(c: Cardinal, report: dict) -> str:
    def block(title: str, names: list) -> list[str]:
        if not names:
            return []
        lines = ["", f"{title} ({len(names)}):"] + [f"• {escape(tools.short(n, 60))}" for n in names[:KEEP_LIST]]
        return lines + ([f"… и ещё {len(names) - KEEP_LIST}"] if len(names) > KEEP_LIST else [])

    lines = [f"📋 <b>Лоты после продажи — {escape(c.label)}</b>"]
    lines += block("✅ Playerok оставляет в продаже сам", report["kept"])
    lines += block("♻️ Перевыставит бот", report["bot"])
    lines += block("❔ Не удалось проверить", report["unknown"])
    if not any(report.values()):
        lines += ["", "Активных лотов нет."]
    if report["bot"] and not c.is_on("autoRestoreSold"):
        lines += ["", "⚠️ «Восстанавливать проданные» выключено — лоты из списка «Перевыставит бот» после продажи "
                      "уйдут с витрины."]
    return "\n".join(lines)


def notifications_kb(c: Cardinal) -> K:
    kb = K()
    for key, title in kbs.NOTIFY_TOGGLES:
        kb.row(B(f"{kbs.dot(c.is_on(key, 'Notifications'))} {title}", callback_data=kbs.cb("nsw", c.id, key)))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def notifications_text(c: Cardinal) -> str:
    return f"🔔 <b>Уведомления — {escape(c.label)}</b>\nВыберите, о чём сообщать:"


def texts_text(c: Cardinal) -> str:
    lines = [f"✏️ <b>Тексты — {escape(c.label)}</b>\n"]
    for kind, title in TEXT_TITLES.items():
        value = c.settings["texts"].get(kind, "")
        lines.append(f"{title}:\n<blockquote>{escape(value) if value else '<i>пусто</i>'}</blockquote>")
    lines.append("Тексты автоответчика и автовыдачи — в их разделах.")
    return "\n".join(lines)


def texts_kb(c: Cardinal) -> K:
    kb = K()
    for kind, title in TEXT_TITLES.items():
        kb.row(B(f"✏️ {title}", callback_data=kbs.cb("txtedit", c.id, kind)))
    days = c.settings["numbers"]["greetingsCooldown"]
    kb.row(B(f"⏱ Пауза приветствия: {_fmt(days)} дн.", callback_data=kbs.cb("num", c.id, "greetingsCooldown")))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def variables_help(kind: str) -> str:
    return "\n".join(f"<code>#{name}</code> — {VARIABLES[name]}" for name in TEXT_KINDS[kind])


def variables_kb(kind: str) -> K:
    kb = K()
    buttons = [B(f"#{name}", copy_text=CopyTextButton(f"#{name}")) for name in TEXT_KINDS[kind]]
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i + 2])
    return kb


def preview(text: str, kind: str) -> str:
    result = f"👀 Пример:\n<blockquote>{escape(render(text, sample_values()))}</blockquote>"
    unknown = unknown_variables(text, kind)
    if unknown:
        result += ("\n⚠️ Неизвестные переменные: " + ", ".join(f"<code>#{escape(n)}</code>" for n in unknown)
                   + " — проверьте опечатки.")
    return result


def ask_text(tg: TGBot, chat_id: int, user_id: int, title: str, current: str, kind: str, on_text, allow_clear=False):
    clear = "\nОтправьте <code>-</code>, чтобы очистить." if allow_clear else ""
    prompt = (f"✏️ <b>{escape(title)}</b>\n\nСейчас:\n<blockquote>{escape(current) or '<i>пусто</i>'}</blockquote>\n"
              f"Пришлите новый текст.{clear} /cancel — отмена.\n\n"
              f"Переменные (нажмите кнопку, чтобы скопировать):\n{variables_help(kind)}")
    tg.ask(chat_id, user_id, prompt, on_text, keyboard=variables_kb(kind))


def cb_switch(tg: TGBot, call: CallbackQuery):
    parts = call.data.split(":")
    c = tg.account_from(call)
    if c is None:
        return
    key = parts[2] if len(parts) > 2 else ""
    if key not in TOGGLE_KEYS:
        tg.answer(call)
        return
    on = c.toggle(key)
    screen = parts[3] if len(parts) > 3 else "m"
    if screen == "r":
        tg.edit(call, raise_text(c), raise_kb(c))
    elif screen == "s":
        tg.edit(call, restore_text(c), restore_kb(c))
    elif screen in kbs.SCREEN_SECTIONS:
        misc_ui.show_section(tg, call, c, kbs.SCREEN_SECTIONS[screen])
    else:
        tg.edit(call, misc_ui.menu_text(tg, c), tg.menu_markup(call.from_user.id, c))
    if key == "autoRaise" and on:
        n = c.settings["numbers"]
        tg.answer(call, f"⚠️ Поднятие на Playerok платное!\nМакс. цена: {_fmt(n['raiseMaxPrice'])} ₽, "
                        f"лимит в день: {_fmt(n['raiseDailyLimit'])} ₽ (меняется в «⬆️ Поднятие»)", alert=True)
    else:
        tg.answer(call, "Включено" if on else "Выключено")


def cb_notifications(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, notifications_text(c), notifications_kb(c))
    tg.answer(call)


def cb_notify_switch(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    key = call.data.split(":")[2]
    if key in NOTIFY_KEYS:
        c.toggle(key, "Notifications")
    tg.edit(call, notifications_text(c), notifications_kb(c))
    tg.answer(call)


def cb_texts(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, texts_text(c), texts_kb(c))
    tg.answer(call)


def cb_text_edit(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    kind = call.data.split(":")[2]
    if kind not in TEXT_TITLES:
        tg.answer(call, "Нет такого текста", alert=True)
        return

    def on_text(m: Message):
        value = (m.text or "").strip()
        if value == "-" and kind == "watermark":
            value = ""
        elif not value:
            tg.bot.send_message(m.chat.id, "❌ Текст пустой, ничего не изменено")
            return
        c.update_settings(lambda s: s["texts"].__setitem__(kind, value))
        result = f"✅ <b>{TEXT_TITLES[kind]}</b> сохранено.\n\n"
        result += preview(value, kind) if value else "Текст очищен."
        tg.bot.send_message(m.chat.id, result, reply_markup=kbs.back_kb(kbs.cb("txt", c.id)))

    ask_text(tg, call.message.chat.id, call.from_user.id, TEXT_TITLES[kind], c.settings["texts"].get(kind, ""),
             kind, on_text, allow_clear=kind == "watermark")
    tg.answer(call)


def _back_for(c: Cardinal, key: str) -> str:
    if key.startswith("raise"):
        return kbs.cb("raise", c.id)
    if key == "restoreCheckInterval":
        return kbs.cb("restore", c.id)
    return kbs.cb("txt", c.id)


def cb_number(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    key = call.data.split(":")[2]
    back = kbs.back_kb(_back_for(c, key))
    if key == "raiseItems":
        def on_filter(m: Message):
            value = "" if (m.text or "").strip() == "-" else (m.text or "").strip()
            c.update_settings(lambda s: s.__setitem__("raise_items", value))
            tg.bot.send_message(m.chat.id, f"✅ Поднимать: {escape(value) or 'все товары'}", reply_markup=back)

        tg.ask(call.message.chat.id, call.from_user.id,
               "🔎 Пришлите слова через запятую: будут подниматься только товары, в названии которых они есть.\n"
               f"Сейчас: {escape(c.settings['raise_items']) or 'все товары'}. <code>-</code> — поднимать все.",
               on_filter)
        tg.answer(call)
        return
    if key not in NUMBER_LIMITS:
        tg.answer(call, "Нет такой настройки", alert=True)
        return
    low, high = NUMBER_LIMITS[key]
    title, unit = NUMBER_TITLES[key]

    def on_number(m: Message):
        try:
            value = float((m.text or "").strip().replace(",", "."))
        except ValueError:
            value = None
        if value is None or not low <= value <= high:
            tg.bot.send_message(m.chat.id, f"❌ Введите число от {low} до {high}", reply_markup=back)
            return
        value = int(value) if value.is_integer() else value
        c.update_settings(lambda s: s["numbers"].__setitem__(key, value))
        tg.bot.send_message(m.chat.id, f"✅ {title}: {_fmt(value)} {unit}", reply_markup=back)

    tg.ask(call.message.chat.id, call.from_user.id,
           f"{title}\nСейчас: {_fmt(c.settings['numbers'][key])} {unit}\n"
           f"Введите число от {low} до {high}. /cancel — отмена.", on_number)
    tg.answer(call)


def cb_raise(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, raise_text(c), raise_kb(c))
    tg.answer(call)


def cb_restore(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, restore_text(c), restore_kb(c))
    tg.answer(call)


def cb_keep_report(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    if c.status != "running" or c.account is None:
        tg.answer(call, "Аккаунт не запущен", alert=True)
        return
    if c.id in _keep_running:
        tg.answer(call, "Уже проверяю")
        return
    _keep_running.add(c.id)
    tg.answer(call, "Проверяю лоты…")
    chat_id = call.message.chat.id
    back = kbs.back_kb(kbs.cb("restore", c.id))

    def run():
        try:
            parts = chatview.split_text(keep_report_text(c, c.keep_in_sale_report()))
        except Exception as e:
            parts = [f"❌ Не удалось проверить лоты: {escape(short_error(e))}"]
        finally:
            _keep_running.discard(c.id)
        try:
            for i, part in enumerate(parts):
                tg.bot.send_message(chat_id, part, reply_markup=back if i == len(parts) - 1 else None)
        except Exception as e:
            c.log.warning(f"Не удалось отправить список лотов: {short_error(e)}")

    threading.Thread(target=run, daemon=True, name=f"{c.id}-keep-report").start()


def register(tg: TGBot):
    tg.on_prefix("rsl:", cb_keep_report)
    for prefix, handler in {"sw:": cb_switch, "nsw:": cb_notify_switch, "ntf:": cb_notifications,
                            "num:": cb_number, "txt:": cb_texts, "txtedit:": cb_text_edit, "raise:": cb_raise,
                            "restore:": cb_restore}.items():
        tg.on_prefix(prefix, handler)
