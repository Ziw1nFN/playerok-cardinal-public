from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools

if TYPE_CHECKING:
    from cardinal import Cardinal
    from hub import Hub

PLUGIN_SETTINGS = "plugin_settings"
PLUGINS_PER_PAGE = 8

MAIN_TOGGLES = [
    ("autoResponse", "🤖 Автоответ"),
    ("autoDelivery", "📦 Автовыдача"),
    ("autoSent", "📤 Авто-«Отправлено»"),
    ("greetings", "👋 Приветствие"),
    ("reviewThanks", "💙 Спасибо за отзыв"),
    ("autoRestoreSold", "♻️ Восстанавливать проданные"),
    ("autoRestoreExpired", "⏳ Восстанавливать истёкшие"),
    ("autoRaise", "⬆️ Автоподнятие (платно)"),
    ("dealButtons", "🔘 Кнопки под сделками"),
]

NOTIFY_TOGGLES = [
    ("newMessage", "💬 Новые сообщения"),
    ("newDeal", "💰 Новые сделки"),
    ("delivery", "📦 Автовыдача"),
    ("review", "🌟 Отзывы"),
    ("dealConfirmed", "✅ Подтверждение сделок"),
    ("dealProblem", "🚨 Проблемы в сделках"),
    ("dealRolledBack", "↩️ Возвраты"),
    ("autoResponse", "🤖 Команды автоответчика"),
    ("raise", "⬆️ Поднятие"),
    ("restore", "♻️ Восстановление"),
]

STATUS_ICONS = {"running": "🟢", "starting": "🟡", "error": "🔴", "disabled": "⏸", "stopped": "⚪"}


def cb(*parts) -> str:
    data = ":".join(str(p) for p in parts)
    if len(data.encode("utf-8")) > 64:
        raise ValueError(f"callback_data longer than 64 bytes: {data}")
    return data


def dot(on: bool) -> str:
    return "🟢" if on else "🔴"


def status_icon(c: Cardinal) -> str:
    return STATUS_ICONS.get(c.status, "⚪")


def message_kb(acc_id: str, chat_id: str) -> K:
    kb = K()
    kb.row(B("✉️ Ответить", callback_data=cb("reply", acc_id, chat_id)),
           B("🌐 Открыть чат", url=tools.chat_link(chat_id)))
    return kb


def deal_kb(acc_id: str, deal_id: str, chat_id: str | None = None) -> K:
    kb = K()
    row = [B("✉️ Ответить", callback_data=cb("reply", acc_id, chat_id))] if chat_id else []
    row.append(B("🌐 Сделка", url=tools.deal_link(deal_id)))
    kb.row(*row)
    kb.row(B("📤 Отправлено", callback_data=cb("sent", acc_id, deal_id)),
           B("↩️ Возврат", callback_data=cb("refund", acc_id, deal_id)))
    return kb


def reply_kb(acc_id: str, chat_id: str | None) -> K | None:
    if not chat_id:
        return None
    kb = K()
    kb.row(B("✉️ Ответить", callback_data=cb("reply", acc_id, chat_id)))
    return kb


def deal_markup(c: Cardinal, deal_id: str, chat_id: str | None = None) -> K | None:
    if c.is_on("dealButtons"):
        return deal_kb(c.id, deal_id, chat_id)
    return reply_kb(c.id, chat_id)


def account_error_kb(acc_id: str) -> K:
    kb = K()
    kb.row(B("🔑 Обновить токен", callback_data=cb("acctoken", acc_id)), B("👤 Аккаунт", callback_data=cb("acc", acc_id)))
    return kb


def confirm_kb(action: str, *ids) -> K:
    kb = K()
    kb.row(B("✅ Да", callback_data=cb(f"{action}_yes", *ids)), B("❌ Нет", callback_data="close"))
    return kb


def back_kb(target: str = "menu") -> K:
    kb = K()
    kb.row(B("◀️ Назад", callback_data=target))
    return kb


def _row(kb: K, allowed: Callable[[str], bool] | None, *buttons: B) -> None:
    shown = [b for b in buttons if allowed is None or allowed(b.callback_data)]
    if shown:
        kb.row(*shown)


def main_menu(c: Cardinal | None, allowed: Callable[[str], bool] | None = None) -> K:
    kb = K()
    if c is None:
        _row(kb, allowed, B("➕ Добавить аккаунт", callback_data="accadd"))
        _row(kb, allowed, B("🧩 Плагины", callback_data="pl:-:0"), B("📜 Логи", callback_data="logs"))
        _row(kb, allowed, B("👥 Сотрудники", callback_data="sf"))
        return kb
    _row(kb, allowed, B(f"👤 {tools.short(c.label, 24)} ▾", callback_data="accs"))
    _row(kb, allowed, *[B(f"{dot(c.is_on(key))} {title}", callback_data=cb("sw", c.id, key))
                        for key, title in QUICK_TOGGLES])
    visible = [B(SECTIONS[key][0], callback_data=cb("mn", c.id, key)) for key in SECTIONS
               if section_rows(c, key, allowed)]
    for index in range(0, len(visible), 2):
        kb.row(*visible[index:index + 2])
    return kb


QUICK_TOGGLES = [("autoDelivery", "📦 Автовыдача"), ("autoResponse", "🤖 Автоответ")]
SECTIONS = {
    "auto": ("🤖 Автоматизация", "Что бот делает сам: выдаёт товар, отвечает на команды, здоровается и "
                                "благодарит за отзывы.", "a"),
    "chats": ("💬 Сделки и чаты", "Сделки и переписка с покупателями, шаблоны ответов, группа с чатами и "
                                 "чёрный список.", "c"),
    "lots": ("📈 Лоты", "Поднятие и восстановление лотов, профили для разных групп лотов.", "l"),
    "money": ("💰 Финансы", "Статистика продаж, вывод денег и профиль аккаунта.", None),
    "system": ("⚙️ Система", "Уведомления, плагины, сотрудники и логи бота.", None),
}
SECTION_TOGGLES = {
    "auto": ["autoResponse", "autoDelivery", "autoSent", "greetings", "reviewThanks"],
    "chats": ["dealButtons"],
    "lots": ["autoRestoreSold", "autoRestoreExpired", "autoRaise"],
}
SCREEN_SECTIONS = {screen: key for key, (_, _, screen) in SECTIONS.items() if screen}


def section_buttons(c: Cardinal, key: str) -> list[list[B]]:
    titles = dict(MAIN_TOGGLES)
    screen = SECTIONS[key][2]
    rows = [[B(f"{dot(c.is_on(t))} {titles[t]}", callback_data=cb("sw", c.id, t, screen))]
            for t in SECTION_TOGGLES.get(key, [])]
    if key == "auto":
        rows += [[B("📦 Автовыдача: товары", callback_data=cb("ad", c.id, 0)),
                  B("🤖 Автоответчик", callback_data=cb("ar", c.id, 0))],
                 [B("✏️ Тексты", callback_data=cb("txt", c.id)),
                  B("🧾 Ответы на сделки", callback_data=cb("dr", c.id))]]
    elif key == "chats":
        rows += [[B("📋 Сделки", callback_data=cb("dl", c.id, "a", 0)), B("💬 Чаты", callback_data=cb("chl", c.id, 0))],
                 [B("⏳ Ждут подтверждения покупателя", callback_data=cb("dl", c.id, "s", 0))],
                 [B("💬 Шаблоны", callback_data=cb("tp", c.id)),
                  B("🔗 Синхронизация", callback_data=cb("cs", c.id))],
                 [B("⛔ Чёрный список", callback_data="bl:0")]]
    elif key == "lots":
        rows += [[B("⬆️ Поднятие", callback_data=cb("raise", c.id)),
                  B("♻️ Восстановление", callback_data=cb("restore", c.id))],
                 [B("🎯 Профили", callback_data=cb("pf", c.id))]]
    elif key == "money":
        rows += [[B("📊 Статистика", callback_data=cb("st", c.id, "w")), B("💸 Вывод", callback_data=cb("wd", c.id))],
                 [B("👤 Профиль", callback_data=cb("prof", c.id))]]
    elif key == "system":
        rows += [[B("🔔 Уведомления", callback_data=cb("ntf", c.id)),
                  B("🧩 Плагины", callback_data=cb("pl", c.id, 0))],
                 [B("👥 Сотрудники", callback_data="sf"), B("📜 Логи", callback_data="logs")]]
        if c.hub.env.get("control_admin_id") and c.hub.env.get("control_db"):
            rows.append([B("👑 Управление ботом", callback_data="ctl")])
    return rows


def section_rows(c: Cardinal, key: str, allowed: Callable[[str], bool] | None = None) -> list[list[B]]:
    rows = [[b for b in row if allowed is None or allowed(b.callback_data)] for row in section_buttons(c, key)]
    return [row for row in rows if row]


def section_menu(c: Cardinal, key: str, allowed: Callable[[str], bool] | None = None) -> K:
    kb = K()
    for row in section_rows(c, key, allowed):
        kb.row(*row)
    kb.row(B("◀️ Главное меню", callback_data="menu"))
    return kb


def plugins_menu(hub: Hub, acc_id: str, page: int) -> K:
    kb = K()
    uuids = list(hub.plugins.plugins)
    pages = max(1, (len(uuids) + PLUGINS_PER_PAGE - 1) // PLUGINS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    c = hub.cardinals.get(acc_id)
    for uuid in uuids[page * PLUGINS_PER_PAGE:(page + 1) * PLUGINS_PER_PAGE]:
        p = hub.plugins.plugins[uuid]
        on = c.settings["plugins"].get(uuid, True) if c else True
        kb.row(B(f"{dot(on)} {tools.short(p.name, 30)} v{p.version}", callback_data=cb("plg", uuid, acc_id, page)))
    nav = []
    if page > 0:
        nav.append(B("⬅️", callback_data=cb("pl", acc_id, page - 1)))
    if page < pages - 1:
        nav.append(B("➡️", callback_data=cb("pl", acc_id, page + 1)))
    if nav:
        kb.row(*nav)
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def plugin_kb(hub: Hub, acc_id: str, uuid: str, page: int) -> K:
    p = hub.plugins.plugins[uuid]
    c = hub.cardinals.get(acc_id)
    kb = K()
    if c is not None:
        on = c.settings["plugins"].get(uuid, True)
        kb.row(B(f"{dot(on)} {'Выключить' if on else 'Включить'} для {tools.short(c.label, 20)}",
                 callback_data=cb("pltgl", uuid, acc_id, page)))
        if p.settings_page:
            kb.row(B("⚙️ Настройки", callback_data=cb(PLUGIN_SETTINGS, uuid, acc_id, 0)))
    if p.commands:
        kb.row(B("⌨️ Команды", callback_data=cb("plcmd", uuid, acc_id, page)))
    kb.row(B("🗑 Удалить (для всех аккаунтов)", callback_data=cb("pldel", uuid, acc_id, page)))
    kb.row(B("◀️ К плагинам", callback_data=cb("pl", acc_id, page)))
    return kb
