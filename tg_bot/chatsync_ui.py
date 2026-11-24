from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.errors import short_error
from handlers import chat_sales
from playerokapi.enums import ItemDealStatuses
from tg_bot import chatview
from tg_bot import keyboards as kbs

if TYPE_CHECKING:
    from tg_bot.bot import TGBot

CHAT_DEALS_LIMIT = 5
TOPIC_ACTIONS = {
    "tsn": (ItemDealStatuses.SENT, "✅ Да, отправлено", "📤 Отмечено «Отправлено»"),
    "trf": (ItemDealStatuses.ROLLED_BACK, "✅ Да, вернуть деньги", "↩️ Деньги возвращены"),
}


def topic_deal_kb(acc_id: str, deal_id: str) -> K:
    kb = K()
    kb.row(B("📤 Отправлено", callback_data=kbs.cb("tsn", acc_id, deal_id)),
           B("↩️ Возврат", callback_data=kbs.cb("trf", acc_id, deal_id)))
    kb.row(B("🌐 Сделка", url=tools.deal_link(deal_id)))
    return kb


def confirm_topic_kb(action: str, acc_id: str, deal_id: str) -> K:
    kb = K()
    kb.row(B(TOPIC_ACTIONS[action][1], callback_data=kbs.cb(f"{action}_yes", acc_id, deal_id)),
           B("❌ Нет", callback_data=kbs.cb("tno", acc_id, deal_id)))
    return kb


def link_kb(deal_id: str) -> K:
    kb = K()
    kb.row(B("🌐 Сделка", url=tools.deal_link(deal_id)))
    return kb


def thread_of(call: CallbackQuery) -> int | None:
    return getattr(call.message, "message_thread_id", None)


def send_in_thread(tg: TGBot, call: CallbackQuery, text: str, keyboard=None) -> None:
    tg.bot.send_message(call.message.chat.id, text, reply_markup=keyboard, message_thread_id=thread_of(call),
                        disable_web_page_preview=True)


def set_markup(tg: TGBot, call: CallbackQuery, keyboard: K) -> None:
    tg.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=keyboard)


def cb_ask(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    action, _, deal_id = call.data.split(":", 2)
    set_markup(tg, call, confirm_topic_kb(action, c.id, deal_id))
    tg.answer(call)


def cb_no(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    set_markup(tg, call, topic_deal_kb(c.id, call.data.split(":", 2)[2]))
    tg.answer(call)


def cb_yes(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    prefix, _, deal_id = call.data.split(":", 2)
    status, _, done = TOPIC_ACTIONS[prefix.removesuffix("_yes")]
    try:
        c.account.update_deal(deal_id, status)
    except Exception as e:
        set_markup(tg, call, topic_deal_kb(c.id, deal_id))
        tg.answer(call, f"Ошибка: {short_error(e)}"[:190], alert=True)
        return
    c.log.info(f"Telegram (тема): сделка {deal_id} → {status.name}")
    tg.edit(call, f"{escape(call.message.text or '')}\n\n— {done}", link_kb(deal_id))
    tg.answer(call, "Готово")


def cb_chat_deals(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    chat_id = call.data.split(":", 2)[2]
    try:
        chat = c.account.get_chat(chat_id)
    except Exception as e:
        tg.answer(call, f"Не удалось получить сделки: {short_error(e)}"[:190], alert=True)
        return
    tg.answer(call)
    deals = chat_sales(c, chat, set(ItemDealStatuses))[:CHAT_DEALS_LIMIT]
    if not deals:
        send_in_thread(tg, call, "В этом чате нет сделок")
    for deal in deals:
        send_in_thread(tg, call, chatview.deal_line(deal), topic_deal_kb(c.id, deal.id))


def cb_templates(tg: TGBot, call: CallbackQuery):
    from tg_bot import templates_ui
    c = tg.account_from(call)
    if c is None:
        return
    kb = templates_ui.reply_kb(c, call.data.split(":", 2)[2])
    tg.answer(call)
    if kb.keyboard:
        send_in_thread(tg, call, "💬 Выберите шаблон:", kb)
    else:
        send_in_thread(tg, call, "Шаблонов нет — добавьте их в меню «💬 Шаблоны»")


def bind_problem(tg: TGBot, group_id: int) -> str:
    chat = tg.bot.get_chat(group_id)
    if getattr(chat, "type", "") != "supergroup" or not getattr(chat, "is_forum", False):
        return "Включите в группе темы (настройки группы → «Темы»): синхронизация работает только в группе с темами."
    member = tg.bot.get_chat_member(group_id, tg.bot.get_me().id)
    if getattr(member, "status", "") not in ("administrator", "creator") or not getattr(member, "can_manage_topics",
                                                                                         False):
        return "Сделайте бота администратором группы с правом «Управление темами»."
    return ""


def bind_text(tg: TGBot, group_id: int) -> str:
    bound = [c.label for c in tg.sync.accounts_in(group_id)]
    state = ", ".join(escape(label) for label in bound) if bound else "ни один"
    return (f"🔗 <b>Синхронизация чатов</b>\nК этой группе привязаны: {state}.\n"
            f"Нажмите на аккаунт, чтобы привязать или отвязать его.")


def bind_kb(tg: TGBot, group_id: int) -> K:
    kb = K()
    for c in tg.hub.cardinals.values():
        mark = "✅" if tg.sync.config(c).get("group_id") == group_id else "▫️"
        kb.row(B(f"{mark} {kbs.status_icon(c)} {tools.short(c.label, 30)}", callback_data=kbs.cb("sb", c.id)))
    return kb


def cmd_bind(tg: TGBot, m) -> None:
    thread = getattr(m, "message_thread_id", None)
    problem = bind_problem(tg, m.chat.id)
    if problem:
        tg.bot.send_message(m.chat.id, f"⚠️ {problem}", message_thread_id=thread)
        return
    for c in tg.sync.accounts_in(m.chat.id):
        if tg.sync.config(c).get("paused"):
            tg.sync.bind(c, m.chat.id, getattr(m.chat, "title", "") or tg.sync.config(c).get("group_title", ""))
    tg.bot.send_message(m.chat.id, bind_text(tg, m.chat.id), reply_markup=bind_kb(tg, m.chat.id),
                        message_thread_id=thread)


def cmd_unbind(tg: TGBot, m) -> None:
    accounts = tg.sync.accounts_in(m.chat.id)
    for c in accounts:
        tg.sync.unbind(c)
    text = ("🔓 Отвязано: " + ", ".join(escape(c.label) for c in accounts)) if accounts else \
        "К этой группе не привязан ни один аккаунт"
    tg.bot.send_message(m.chat.id, text, message_thread_id=getattr(m, "message_thread_id", None))


def cb_bind(tg: TGBot, call: CallbackQuery):
    if getattr(call.message.chat, "type", "") not in ("group", "supergroup"):
        tg.answer(call)
        return
    c = tg.account_from(call)
    if c is None:
        return
    group_id = call.message.chat.id
    if tg.sync.config(c).get("group_id") == group_id:
        tg.sync.unbind(c)
        tg.answer(call, "Отвязано")
    else:
        problem = bind_problem(tg, group_id)
        if problem:
            tg.answer(call, problem[:190], alert=True)
            return
        title = getattr(tg.bot.get_chat(group_id), "title", None) or getattr(call.message.chat, "title", "") or ""
        tg.sync.bind(c, group_id, title)
        tg.answer(call, "Привязано")
    tg.edit(call, bind_text(tg, group_id), bind_kb(tg, group_id))


HOWTO = ("<b>Как привязать группу:</b>\n"
         "1. Создайте группу и включите в ней темы.\n"
         "2. Добавьте бота администратором с правом «Управление темами».\n"
         "3. Напишите в группе <code>/bind</code> и выберите аккаунт.")
SETTING_KEYS = {"on": "enabled", "dup": "duplicate_private", "link": "link_buyer"}


def screen_text(tg: TGBot, c) -> str:
    cfg = tg.sync.config(c)
    if cfg.get("group_id"):
        group = f"👥 Группа: <b>{escape(cfg.get('group_title') or str(cfg['group_id']))}</b>"
    else:
        group = "👥 Группа не привязана"
    lines = [f"🔗 <b>Синхронизация чатов — {escape(c.label)}</b>\n",
             "У каждого покупателя своя тема в Telegram-группе: там вся переписка и его сделки, "
             "ответ в теме уходит покупателю.\n", group]
    if cfg.get("paused"):
        lines.append(f"⏸ <b>На паузе</b>: {escape(cfg['paused'])}\nСообщения покупателей сейчас приходят в личку.")
    lines.append(f"\n{HOWTO}")
    return "\n".join(lines)


def screen_kb(tg: TGBot, c) -> K:
    cfg = tg.sync.config(c)
    kb = K()
    kb.row(B(f"{kbs.dot(cfg.get('enabled'))} Синхронизация", callback_data=kbs.cb("cst", c.id, "on")))
    kb.row(B(f"{kbs.dot(cfg.get('duplicate_private'))} Дублировать сообщения в личку",
             callback_data=kbs.cb("cst", c.id, "dup")))
    kb.row(B(f"{kbs.dot(cfg.get('link_buyer'))} Ссылка на чат в имени покупателя",
             callback_data=kbs.cb("cst", c.id, "link")))
    if cfg.get("group_id"):
        kb.row(B("🔓 Отвязать группу", callback_data=kbs.cb("csu", c.id)))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def cb_screen(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, screen_text(tg, c), screen_kb(tg, c))
    tg.answer(call)


def cb_setting(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    key = SETTING_KEYS.get(call.data.split(":")[2])
    if key is None:
        tg.answer(call, "Нет такого пункта", alert=True)
        return

    def change(s: dict):
        cfg = s["chat_sync"]
        cfg[key] = not cfg.get(key)
        if key == "enabled" and cfg[key]:
            cfg["paused"] = ""

    c.update_settings(change)
    tg.edit(call, screen_text(tg, c), screen_kb(tg, c))
    tg.answer(call)


def cb_unbind(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.bot.send_message(call.message.chat.id, f"🔓 Отвязать группу от аккаунта <b>{escape(c.label)}</b>?\n"
                                              f"Темы в группе останутся, новые сообщения будут приходить в личку.",
                        reply_markup=kbs.confirm_kb("csu", c.id))
    tg.answer(call)


def cb_unbind_yes(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    tg.sync.unbind(c)
    tg.edit(call, "🔓 Группа отвязана.", kbs.back_kb(kbs.cb("cs", c.id)))
    tg.answer(call, "Отвязано")


def register(tg: TGBot):
    for prefix, handler in {"tsn:": cb_ask, "trf:": cb_ask, "tsn_yes:": cb_yes, "trf_yes:": cb_yes, "tno:": cb_no,
                            "tdl:": cb_chat_deals, "ttp:": cb_templates, "sb:": cb_bind, "cs:": cb_screen,
                            "cst:": cb_setting, "csu:": cb_unbind, "csu_yes:": cb_unbind_yes}.items():
        tg.on_prefix(prefix, handler)
    for command, handler in (("bind", cmd_bind), ("unbind", cmd_unbind)):
        tg.bot.register_message_handler(lambda m, h=handler: h(tg, m), commands=[command],
                                        func=lambda m: tg.is_group(m) and tg.is_owner(m.from_user.id))
