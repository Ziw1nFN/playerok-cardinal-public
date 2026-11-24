from __future__ import annotations

from datetime import datetime
from html import escape
from typing import TYPE_CHECKING

from handlers import deal_price, is_system_text
from playerokapi.enums import ItemDealStatuses
from Utils import tools

if TYPE_CHECKING:
    from cardinal import Cardinal

TEXT_LIMIT = 4096
HISTORY_TITLE = "📜 <b>Последние сообщения</b>"
DEAL_STATUS_LABELS = {
    ItemDealStatuses.PAID: "💰 Оплачена",
    ItemDealStatuses.PENDING: "⏳ В обработке",
    ItemDealStatuses.SENT: "📤 Отправлена",
    ItemDealStatuses.CONFIRMED: "✅ Завершена",
    ItemDealStatuses.CONFIRMED_AUTOMATICALLY: "✅ Завершена",
    ItemDealStatuses.ROLLED_BACK: "↩️ Возврат",
}


def deal_status_label(deal) -> str:
    label = DEAL_STATUS_LABELS.get(getattr(deal, "status", None), "•")
    return f"{label} 🚨" if getattr(deal, "has_problem", False) else label


def format_date(value: str | None) -> str:
    if not value:
        return "?"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def deal_button(deal) -> str:
    item = getattr(getattr(deal, "item", None), "name", None) or "товар"
    price = deal_price(deal) if getattr(deal, "item", None) is not None else ""
    buyer = getattr(getattr(deal, "user", None), "username", None) or "?"
    icon = deal_status_label(deal).split(" ")[0]
    problem = " 🚨" if getattr(deal, "has_problem", False) else ""
    text = f"{icon}{problem} {item}" + (f" · {price} ₽" if price else "") + f" · {buyer}"
    return text if len(text) <= 60 else text[:59] + "…"


def deal_line(deal) -> str:
    item = getattr(getattr(deal, "item", None), "name", None) or "товар"
    price = deal_price(deal) if getattr(deal, "item", None) is not None else ""
    return f"{deal_status_label(deal)} · 📦 {escape(item)}" + (f" — {price} ₽" if price else "")


def is_own_message(c: Cardinal, msg) -> bool:
    user = getattr(msg, "user", None)
    return user is not None and user.id == getattr(c.account, "id", None)


def buyer_label(c: Cardinal, buyer: str, chat_id: str | None = None) -> str:
    name = escape(buyer)
    if chat_id and c.settings["chat_sync"].get("link_buyer"):
        return f'👤 <a href="{escape(tools.chat_link(chat_id), quote=True)}">{name}</a>'
    return f"👤 <b>{name}</b>"


def message_line(c: Cardinal, msg, buyer: str, limit: int | None = None, chat_id: str | None = None) -> str:
    text = getattr(msg, "text", None)
    if is_system_text(text):
        return ""
    if text and limit and len(text) > limit:
        text = text[:limit - 1] + "…"
    body = escape(text) if text else ""
    if getattr(msg, "images", None):
        body = f"{body}\n🖼 фото" if body else "🖼 фото"
    if not body:
        return ""
    who = "🧑‍💼 <b>Вы</b>" if is_own_message(c, msg) else buyer_label(c, buyer, chat_id)
    return f"{who}: {body}"


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
            amp = text.rfind("&", 0, cut)
            if amp > text.rfind(";", 0, cut):
                cut = amp
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n ")
    parts.append(text)
    return parts


def history_lines(c: Cardinal, messages: list, buyer: str, skip_id: str | None = None,
                  limit: int | None = None, chat_id: str | None = None) -> list[str]:
    ordered = sorted((m for m in messages if m.id != skip_id), key=lambda m: getattr(m, "created_at", "") or "")
    return [line for line in (message_line(c, m, buyer, limit, chat_id) for m in ordered) if line]


def buyer_name(c: Cardinal, chat=None, messages: list | None = None) -> str:
    buyer = c.buyer_of_chat(chat) if chat is not None else None
    if getattr(buyer, "username", None):
        return buyer.username
    for m in messages or []:
        if getattr(m, "user", None) is not None and not is_own_message(c, m) and m.user.username:
            return m.user.username
    return "Покупатель"


def pack(lines: list[str], title: str = HISTORY_TITLE, limit: int = TEXT_LIMIT) -> list[str]:
    chunks, current = [], title
    for line in lines:
        for part in split_text(line, limit - len(title) - 2):
            candidate = f"{current}\n\n{part}" if current else part
            if len(candidate) > limit:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current and current != title:
        chunks.append(current)
    return chunks


def history_chunks(c: Cardinal, messages: list, buyer: str, limit: int = 10, skip_id: str | None = None, chat_id: str | None = None) -> list[str]:
    lines = history_lines(c, messages, buyer, skip_id, chat_id=chat_id)[-limit:]
    return pack(lines) if lines else []


def topic_url(group_id: int, thread_id: int) -> str:
    raw = str(group_id)
    raw = raw[4:] if raw.startswith("-100") else raw.lstrip("-")
    return f"https://t.me/c/{raw}/{thread_id}"
