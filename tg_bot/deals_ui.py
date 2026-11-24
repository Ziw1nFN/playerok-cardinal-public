from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.errors import short_error
from handlers import deal_price
from playerokapi.enums import ItemDealDirections, ItemDealStatuses
from tg_bot import chatview
from tg_bot import keyboards as kbs

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

PER_PAGE = 8
FILTERS = {
    "p": ("💰 Оплачены", [ItemDealStatuses.PAID]),
    "s": ("⏳ Ждут подтверждения покупателя", [ItemDealStatuses.SENT]),
    "x": ("🚨 Проблема", [ItemDealStatuses.PAID, ItemDealStatuses.SENT]),
    "a": ("📋 Все", None),
}


def fetch(c: Cardinal, f: str) -> list:
    if f == "s":
        deals, cursor, seen = [], None, set()
        while True:
            page = c.account.get_deals(statuses=FILTERS[f][1], direction=ItemDealDirections.OUT,
                                       count=24, after_cursor=cursor)
            deals.extend(d for d in page.deals if d.status == ItemDealStatuses.SENT)
            info = getattr(page, "page_info", None)
            if info is None or not isinstance(info.has_next_page, bool):
                raise ValueError("Playerok не сообщил, есть ли ещё страницы сделок")
            if not info.has_next_page:
                return deals
            cursor = info.end_cursor
            if not cursor or cursor in seen:
                raise ValueError("Playerok вернул неверный указатель страницы сделок")
            seen.add(cursor)
    deals = c.account.get_deals(statuses=FILTERS[f][1], direction=ItemDealDirections.OUT, count=24).deals
    if f == "x":
        deals = [d for d in deals if getattr(d, "has_problem", False)]
    return list(deals)


def list_kb(c: Cardinal, f: str, deals: list, page: int) -> K:
    kb = K()
    kb.row(*[B(("• " if code == f else "") + title.split(" ", 1)[0], callback_data=kbs.cb("dl", c.id, code, 0))
             for code, (title, _) in FILTERS.items()])
    for d in deals[page * PER_PAGE:(page + 1) * PER_PAGE]:
        kb.row(B(chatview.deal_button(d), callback_data=kbs.cb("dl1", c.id, f, d.id)))
    nav = []
    if page > 0:
        nav.append(B("⬅️", callback_data=kbs.cb("dl", c.id, f, page - 1)))
    if (page + 1) * PER_PAGE < len(deals):
        nav.append(B("➡️", callback_data=kbs.cb("dl", c.id, f, page + 1)))
    if nav:
        kb.row(*nav)
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def parse_list(call: CallbackQuery) -> tuple[str, int]:
    parts = call.data.split(":")
    f = parts[2] if len(parts) > 2 and parts[2] in FILTERS else "a"
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    return f, page


def cb_list(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    f, page = parse_list(call)
    try:
        deals = fetch(c, f)
    except Exception as e:
        tg.edit(call, f"❌ Не удалось получить сделки: {escape(short_error(e))}", kbs.back_kb("menu"))
        tg.answer(call)
        return
    pages = max(1, (len(deals) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    scope = f" · всего: {len(deals)}" if f == "s" else " · последние 24 продажи"
    text = (f"📋 <b>Сделки — {escape(c.label)}</b>\nФильтр: {FILTERS[f][0]}{scope}"
            + (f" · стр. {page + 1}/{pages}" if pages > 1 else "")
            + ("\n\nСделок нет." if not deals else ""))
    tg.edit(call, text, list_kb(c, f, deals, page))
    tg.answer(call)


def card_text(d) -> str:
    item = getattr(getattr(d, "item", None), "name", None) or "товар"
    price = deal_price(d) if getattr(d, "item", None) is not None else ""
    buyer = getattr(getattr(d, "user", None), "username", None) or "?"
    lines = [f"📦 <b>{escape(item)}</b>"]
    if price:
        lines.append(f"💵 {price} ₽")
    lines += [f"👤 {escape(buyer)}", f"📌 {chatview.deal_status_label(d)}",
              f"🕒 {chatview.format_date(getattr(d, 'created_at', None))}"]
    if getattr(d, "has_problem", False):
        lines.append("🚨 <b>Проблема</b>: покупатель открыл проблему по заказу")
    return "\n".join(lines)


def card_kb(tg: TGBot, c: Cardinal, f: str, d) -> K:
    kb = K()
    status = getattr(d, "status", None)
    actions = []
    if status == ItemDealStatuses.PAID:
        actions.append(B("📤 Отправлено", callback_data=kbs.cb("sent", c.id, d.id)))
    if status in (ItemDealStatuses.PAID, ItemDealStatuses.SENT):
        actions.append(B("↩️ Возврат", callback_data=kbs.cb("refund", c.id, d.id)))
    if actions:
        kb.row(*actions)
    chat_id = getattr(getattr(d, "chat", None), "id", None)
    if chat_id:
        kb.row(B("✉️ Ответить", callback_data=kbs.cb("reply", c.id, chat_id)),
               B("💬 Чат", callback_data=kbs.cb("ch1", c.id, chat_id, 0)))
        if tg.sync.is_active(c):
            kb.row(B("🧵 Тема в группе", callback_data=kbs.cb("cht", c.id, chat_id)))
    kb.row(B("🌐 Сделка на сайте", url=tools.deal_link(d.id)))
    kb.row(B("◀️ Назад", callback_data=kbs.cb("dl", c.id, f, 0)))
    return kb


def cb_card(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    _, _, f, deal_id = call.data.split(":", 3)
    f = f if f in FILTERS else "a"
    try:
        d = c.account.get_deal(deal_id)
    except Exception as e:
        tg.answer(call, f"Не удалось получить сделку: {short_error(e)}"[:190], alert=True)
        return
    tg.edit(call, card_text(d), card_kb(tg, c, f, d))
    tg.answer(call)


def register(tg: TGBot):
    tg.on_prefix("dl:", cb_list)
    tg.on_prefix("dl1:", cb_card)
