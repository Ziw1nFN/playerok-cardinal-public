from __future__ import annotations

import threading
import time
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING

from Utils.errors import short_error
from Utils.profiles import normalize as normalize_profile
from Utils.stats import SOLD, deal_row
from Utils.variables import render
from playerokapi.enums import ChatTypes, ItemDealDirections, ItemDealStatuses

if TYPE_CHECKING:
    from cardinal import Cardinal


def _kb():
    from tg_bot import keyboards
    return keyboards


def _sync(c: Cardinal):
    from tg_bot.chatsync import sync_of
    return sync_of(c)


def is_system_text(text: str | None) -> bool:
    return bool(text) and text.startswith("{{") and text.endswith("}}")


def is_own(c: Cardinal, user) -> bool:
    return user is not None and user.id == c.account.id


def is_pm(c: Cardinal, chat) -> bool:
    if chat is None:
        return False
    chat_type = getattr(chat, "type", ChatTypes.PM)
    if chat_type is not None:
        return chat_type == ChatTypes.PM
    service = {getattr(c.account, "support_chat_id", None), getattr(c.account, "system_chat_id", None)}
    return chat.id not in service - {None}


def last_activity(chat) -> float | None:
    created = getattr(getattr(chat, "last_message", None), "created_at", None)
    if not created:
        return None
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def deal_item_name(c: Cardinal, deal) -> str:
    item = deal.item
    if item is not None and not getattr(item, "name", None):
        try:
            deal.item = c.account.get_item(item.id)
        except Exception as e:
            c.log.warning(f"Не удалось получить товар сделки {deal.id}: {e}")
    return getattr(deal.item, "name", "") or ""


def deal_price(deal) -> str:
    price = getattr(deal.item, "price", None)
    return "" if price is None else str(price)


def chat_id_of(event) -> str | None:
    return event.chat.id if getattr(event, "chat", None) else None


def remember_chat(c: Cardinal, event):
    if event.chat.id in c.greeted:
        return
    last = last_activity(event.chat)
    if last is not None and last > c.start_time:
        return
    c.mark_greeted(event.chat.id)


def log_message(c: Cardinal, event):
    msg = event.message
    author = msg.user.username if msg.user else "?"
    text = msg.text if msg.text is not None else "[изображение]"
    if not is_system_text(text):
        c.log.info(f"💬 {author}: {text[:120]}")


def message_notification(c: Cardinal, event) -> tuple:
    msg = event.message
    author = msg.user.username if msg.user else "Playerok"
    title = "💬 Новое сообщение" if is_pm(c, event.chat) else "🛎 Сообщение от Playerok"
    body = escape(msg.text) if msg.text is not None else "<i>🖼 изображение</i>"
    return f"{title}\n👤 <b>{escape(author)}</b>\n\n{body}", _kb().message_kb(c.id, event.chat.id)


def synced(c: Cardinal) -> bool:
    sync = _sync(c)
    return sync is not None and sync.is_active(c)


def notify_message(c: Cardinal, event):
    msg = event.message
    if is_own(c, msg.user) or is_system_text(msg.text):
        return
    if is_pm(c, event.chat) and synced(c) and not c.settings["chat_sync"].get("duplicate_private"):
        return
    text, keyboard = message_notification(c, event)
    c.notify(text, keyboard, kind="newMessage")


def sync_message(c: Cardinal, event):
    msg = event.message
    sync = _sync(c)
    if (sync is None or not sync.is_active(c) or event.chat is None or not is_pm(c, event.chat)
            or is_system_text(msg.text)):
        return
    own = is_own(c, msg.user)
    if own and sync.is_echo(c, msg.id):
        return
    from tg_bot.chatview import buyer_label
    who = "🧑‍💼 <b>Вы</b>" if own else buyer_label(c, getattr(msg.user, 'username', None) or '?', event.chat.id)
    fallback = None if own else (*message_notification(c, event), "newMessage")
    if msg.text:
        sync.post(c, event.chat.id, f"{who}: {escape(msg.text)}", fallback=fallback, source_message_id=msg.id)
        fallback = None
    for image in getattr(msg, "images", None) or []:
        sync.post(c, event.chat.id, f"{who}:", photo=image.url, fallback=fallback, source_message_id=msg.id)
        fallback = None


DEAL_EVENT_TITLES = {
    "new": "💰 <b>Новая сделка</b>",
    "sent": "📤 Сделка отмечена «Отправлено»",
    "confirmed": "✅ Покупатель подтвердил заказ",
    "problem": "🚨 Покупатель открыл проблему",
    "rolled_back": "↩️ Деньги по сделке возвращены",
    "review": "🌟 Новый отзыв",
}


def sync_deal(kind: str):
    def handler(c: Cardinal, event):
        deal = event.deal
        sync = _sync(c)
        chat_id = chat_id_of(event)
        if sync is None or not sync.is_active(c) or chat_id is None or not is_pm(c, event.chat):
            return
        if is_own(c, deal.user):
            return
        price = deal_price(deal)
        lines = [DEAL_EVENT_TITLES[kind], f"📦 {escape(deal_item_name(c, deal))}" + (f" — {price} ₽" if price else "")]
        review = getattr(deal, "review", None)
        if kind == "review" and review is not None:
            lines.append("⭐" * max(0, min(5, int(review.rating or 0))))
            if review.text:
                lines.append(escape(review.text))
        from tg_bot.chatsync_ui import topic_deal_kb
        keyboard = topic_deal_kb(c.id, deal.id) if c.is_on("dealButtons") else None
        sync.post(c, chat_id, "\n".join(lines), keyboard=keyboard)
    handler.__name__ = f"sync_deal_{kind}"
    return handler


def greet(c: Cardinal, event):
    msg = event.message
    if (not c.is_on("greetings") or not is_pm(c, event.chat) or is_own(c, msg.user)
            or msg.text is None or is_system_text(msg.text)):
        return
    if not c.should_greet(event.chat.id):
        return
    c.mark_greeted(event.chat.id)
    if c.is_blacklisted(msg.user.username):
        return
    text = render(c.settings["texts"]["greeting"],
                  {"nickname": msg.user.username, "seller": c.label, "chat_id": event.chat.id})
    if text.strip():
        c.send_message(event.chat.id, text)


def item_places_of(item) -> list[str] | None:
    names = [getattr(getattr(item, key, None), "name", None) for key in ("game", "category")]
    names = [n for n in names if n]
    return names or None


def deal_profile(c: Cardinal, deal) -> dict | None:
    item = getattr(deal, "item", None)
    if item is None:
        return None
    return c.profile_for(getattr(item, "name", None), getattr(item, "price", None), getattr(item, "id", None),
                         item_places_of(item))


def command_entry(profile: dict | None, wanted: str) -> dict | None:
    if not profile:
        return None
    return next((r for r in profile["auto_responses"]
                 if wanted in [cmd.strip().lower() for cmd in r.get("commands", [])]), None)


def latest_sale(c: Cardinal, sales: list):
    if not sales:
        return None

    def created(deal) -> float:
        try:
            return (c.journal.get(deal.id) or {}).get("created_at") or 0
        except Exception:
            return 0

    return max(sales, key=created)


def profile_command(c: Cardinal, event) -> dict | None:
    wanted = (event.message.text or "").strip().lower()
    profiles = [normalize_profile(p) for p in c.settings.get("profiles") or []]
    if not wanted or not any(p["enabled"] and command_entry(p, wanted) for p in profiles):
        return None
    try:
        chat = c.account.get_chat(event.chat.id)
    except Exception as e:
        c.log.warning(f"Не удалось получить сделки чата для команды профиля: {short_error(e)}")
        return None
    deal = latest_sale(c, chat_sales(c, chat, set(ItemDealStatuses)))
    return command_entry(deal_profile(c, deal), wanted) if deal is not None else None


def auto_response(c: Cardinal, event):
    msg = event.message
    if not c.is_on("autoResponse") or not is_pm(c, event.chat) or is_own(c, msg.user):
        return
    if c.is_blacklisted(getattr(msg.user, "username", None)):
        return
    entry = profile_command(c, event) or c.find_auto_response(msg.text)
    if not entry:
        return
    text = render(entry["text"], {"nickname": msg.user.username, "seller": c.label, "chat_id": event.chat.id})
    if c.send_message(event.chat.id, text):
        c.log.info(f"🤖 Автоответ на «{msg.text.strip()}» для {msg.user.username}")
    if entry.get("notify"):
        c.notify(f"🤖 <b>{escape(msg.user.username)}</b> написал команду <code>{escape(msg.text.strip())}</code>",
                 _kb().message_kb(c.id, event.chat.id), kind="autoResponse")


def log_deal(c: Cardinal, event):
    deal = event.deal
    c.log.info(f"💰 Новая сделка {deal.id}: {deal.user.username} купил «{deal_item_name(c, deal)}» "
               f"за {deal_price(deal) or '?'} ₽")


def notify_deal(c: Cardinal, event):
    deal = event.deal
    if is_own(c, deal.user):
        return
    lines = [
        "💰 <b>Новая сделка!</b>",
        f"👤 Покупатель: <b>{escape(deal.user.username or '?')}</b>",
        f"📦 Товар: <b>{escape(deal_item_name(c, deal))}</b>",
        f"💵 Сумма: <b>{deal_price(deal) or '?'} ₽</b>",
    ]
    if getattr(deal, "comment_from_buyer", None):
        lines.append(f"📝 Комментарий: {escape(deal.comment_from_buyer)}")
    c.notify("\n".join(lines), _kb().deal_markup(c, deal.id, event.chat.id), kind="newDeal")


def auto_delivery(c: Cardinal, event):
    deal = event.deal
    if is_own(c, deal.user):
        return
    c.mark_greeted(event.chat.id)
    if not c.is_on("autoDelivery") or c.is_delivered(deal.id):
        return
    name = deal_item_name(c, deal)
    entry = c.find_auto_delivery(name)
    if not entry:
        return
    username = deal.user.username
    keyboard = _kb().deal_markup(c, deal.id, event.chat.id)
    action = _kb().deal_kb(c.id, deal.id, event.chat.id)
    if c.is_blacklisted(username):
        c.log.info(f"⛔ {username} в чёрном списке — товар не выдан")
        c.notify(f"⛔ <b>{escape(username)}</b> из чёрного списка купил <b>{escape(name)}</b>. Товар не выдан.",
                 action, kind="delivery")
        return
    if not c.claim_deal(deal.id):
        return
    c.run_handlers("BIND_TO_PRE_DELIVERY", c, event, entry)
    products_file = entry.get("products_file", "").strip()
    product = None
    if products_file:
        product = c.storage.take_product(products_file)
        if product is None:
            c.release_deal(deal.id)
            c.log.warning(f"📭 Закончились товары в {products_file} для «{name}»")
            c.notify(f"📭 Закончились товары для <b>{escape(name)}</b>!\n"
                     f"Покупатель <b>{escape(username)}</b> ждёт выдачу.", action, kind="delivery")
            c.run_handlers("BIND_TO_POST_DELIVERY", c, event, entry, "", False)
            return
    text = render(entry["text"], {"nickname": username, "item": name, "price": deal_price(deal),
                                  "order_id": deal.id, "product": product, "seller": c.label,
                                  "chat_id": event.chat.id})
    if not c.send_message(event.chat.id, text):
        if product is not None:
            c.storage.return_product(products_file, product)
        c.release_deal(deal.id)
        c.log.error(f"❌ Не удалось выдать «{name}» покупателю {username}")
        c.notify(f"❌ Не удалось выдать <b>{escape(name)}</b> покупателю <b>{escape(username)}</b>. "
                 f"Выдайте вручную.", action, kind="delivery")
        c.run_handlers("BIND_TO_POST_DELIVERY", c, event, entry, text, False)
        return
    c.log.info(f"📦 Выдан «{name}» покупателю {username}")
    sent = ""
    if c.is_on("autoSent"):
        try:
            c.account.update_deal(deal.id, ItemDealStatuses.SENT)
            sent = "\n📤 Сделка отмечена как отправленная"
        except Exception as e:
            c.log.error(f"Не удалось отметить сделку {deal.id} отправленной: {e}")
            sent = "\n⚠️ Не удалось отметить сделку отправленной"
    left = f"\n📦 Осталось: {c.storage.count_products(products_file)}" if products_file else ""
    c.notify(f"✅ Выдан <b>{escape(name)}</b> покупателю <b>{escape(username)}</b>{left}{sent}",
             keyboard, kind="delivery")
    c.run_handlers("BIND_TO_POST_DELIVERY", c, event, entry, text, True)


def restore_sold(c: Cardinal, event):
    deal = event.deal
    if not c.is_on("autoRestoreSold") or is_own(c, deal.user) or deal.item is None:
        return

    def worker():
        time.sleep(5)
        for attempt in range(3):
            try:
                c.restore_sold(deal)
                return
            except Exception as e:
                c.log.warning(f"Восстановление товара сделки {deal.id} не удалось (попытка {attempt + 1}/3): {e}")
                time.sleep(10)

    threading.Thread(target=worker, daemon=True, name=f"restore-{deal.id}").start()


def notify_review(c: Cardinal, event):
    deal = event.deal
    review = getattr(deal, "review", None)
    if review is None:
        return
    stars = "⭐" * max(0, min(5, int(review.rating or 0)))
    text = f"\n\n{escape(review.text)}" if review.text else ""
    c.notify(f"🌟 <b>Новый отзыв</b> {stars}\n👤 <b>{escape(deal.user.username or '?')}</b>\n"
             f"📦 {escape(deal_item_name(c, deal))}{text}",
             _kb().deal_markup(c, deal.id, chat_id_of(event)), kind="review")


def thank_review(c: Cardinal, event):
    deal = event.deal
    if not c.is_on("reviewThanks") or getattr(deal, "review", None) is None or event.chat is None:
        return
    text = render(c.settings["texts"]["review_thanks"],
                  {"nickname": deal.user.username, "item": deal_item_name(c, deal), "price": deal_price(deal),
                   "order_id": deal.id, "seller": c.label, "chat_id": event.chat.id})
    if text.strip():
        c.send_message(event.chat.id, text)


def _deal_status_notifier(title: str, kind: str):
    def handler(c: Cardinal, event):
        deal = event.deal
        if is_own(c, deal.user):
            return
        c.log.info(f"{title}: сделка {deal.id} ({deal.user.username})")
        c.notify(f"{title}\n👤 <b>{escape(deal.user.username or '?')}</b>\n📦 {escape(deal_item_name(c, deal))}",
                 _kb().deal_markup(c, deal.id, chat_id_of(event)), kind=kind)
    handler.__name__ = f"notify_{kind}"
    return handler


COMMAND_ACTIONS = {
    "complete": ({ItemDealStatuses.PAID}, ItemDealStatuses.SENT, "📤 Отмечено «Отправлено»"),
    "refund": ({ItemDealStatuses.PAID, ItemDealStatuses.SENT}, ItemDealStatuses.ROLLED_BACK, "↩️ Возврат денег"),
}


def chat_sales(c: Cardinal, chat, statuses: set) -> list:
    return [d for d in (getattr(chat, "deals", None) or [])
            if getattr(d, "status", None) in statuses and not is_own(c, getattr(d, "user", None))
            and getattr(d, "direction", None) != ItemDealDirections.IN]


def chat_commands(c: Cardinal, event):
    msg = event.message
    config = c.settings.get("chat_commands", {})
    if not config.get("enabled") or event.chat is None or not is_own(c, msg.user) or msg.text is None:
        return
    if not is_pm(c, event.chat):
        return
    text = msg.text.strip().lower()
    action = next((name for name in COMMAND_ACTIONS
                   if text in [w.strip().lower() for w in config.get(name, []) if w.strip()]), None)
    if action is None or not c.remember_command(msg.id):
        return
    statuses, status, title = COMMAND_ACTIONS[action]
    command = escape(msg.text.strip())
    try:
        chat = c.account.get_chat(event.chat.id)
    except Exception as e:
        c.notify(f"⚠️ Команда <code>{command}</code>: не удалось получить сделки чата ({escape(short_error(e))})",
                 _kb().message_kb(c.id, event.chat.id))
        return
    if not is_pm(c, chat):
        return
    deals = chat_sales(c, chat, statuses)
    if not deals:
        c.notify(f"⚠️ Команда <code>{command}</code>: в этом чате нет подходящей сделки",
                 _kb().message_kb(c.id, event.chat.id))
        return
    if len(deals) > 1:
        c.notify(f"⚠️ Команда <code>{command}</code>: в этом чате несколько подходящих сделок ({len(deals)}), "
                 f"ничего не изменено. Выберите нужную кнопками ниже.", _kb().message_kb(c.id, event.chat.id))
        for d in deals[:5]:
            buyer = getattr(getattr(d, "user", None), "username", None) or "?"
            c.notify(f"📦 {escape(getattr(d.item, 'name', '') or '')}\n👤 <b>{escape(buyer)}</b>",
                     _kb().deal_kb(c.id, d.id, event.chat.id))
        return
    deal = deals[0]
    try:
        c.account.update_deal(deal.id, status)
    except Exception as e:
        c.notify(f"❌ Команда <code>{command}</code> не выполнена: {escape(short_error(e))}",
                 _kb().deal_markup(c, deal.id, event.chat.id))
        return
    buyer = getattr(getattr(deal, "user", None), "username", None) or "?"
    c.log.info(f"Команда «{msg.text.strip()}»: сделка {deal.id} → {status.name}")
    c.notify(f"{title} по команде <code>{command}</code>\n👤 <b>{escape(buyer)}</b>\n"
             f"📦 {escape(getattr(deal.item, 'name', '') or '')}", _kb().deal_markup(c, deal.id, event.chat.id))


def deal_values(c: Cardinal, event) -> dict:
    deal = event.deal
    return {"nickname": deal.user.username, "item": deal_item_name(c, deal), "price": deal_price(deal),
            "order_id": deal.id, "chat_id": chat_id_of(event), "seller": c.label}


def deal_reply(kind: str):
    def handler(c: Cardinal, event):
        deal = event.deal
        reply = c.settings.get("deal_replies", {}).get(kind, {})
        if not reply.get("enabled") or is_own(c, deal.user) or event.chat is None:
            return
        if kind == "sent" and c.is_delivered(deal.id):
            return
        profile = deal_profile(c, deal)
        source = ((profile or {}).get("deal_replies") or {}).get(kind) or ""
        text = render(source if source.strip() else reply.get("text", ""), deal_values(c, event))
        if text.strip() and c.send_message(event.chat.id, text):
            c.log.info(f"🧾 Ответ «{kind}» по сделке {deal.id}")
    handler.__name__ = f"deal_reply_{kind}"
    return handler


def remember_sent_deal(c: Cardinal, event):
    deal = event.deal
    if is_own(c, deal.user) or event.chat is None:
        return
    c.remember_sent(deal.id, event.chat.id, deal_values(c, event))


def forget_sent_deal(c: Cardinal, event):
    c.forget_sent(event.deal.id)


notify_confirmed = _deal_status_notifier("✅ Покупатель подтвердил сделку", "dealConfirmed")
notify_rolled_back = _deal_status_notifier("↩️ Возврат средств по сделке", "dealRolledBack")
notify_problem = _deal_status_notifier("🚨 Проблема в сделке", "dealProblem")
notify_problem_resolved = _deal_status_notifier("🟢 Проблема в сделке решена", "dealProblem")


def record_sale(kind: str):
    def handler(c: Cardinal, event):
        deal = getattr(event, "deal", None)
        if deal is None or not c.is_own_sale(deal):
            return
        row = deal_row(deal)
        status = row["status"]
        if kind == "new":
            status = status or "PAID"
        elif kind == "sent":
            status = "SENT"
        elif kind == "confirmed":
            status = status if status in SOLD else "CONFIRMED"
        elif kind == "rolled_back":
            status = "ROLLED_BACK"
        try:
            c.journal.record(row, status=status, has_problem={"problem": True, "resolved": False}.get(kind))
        except Exception as e:
            c.log.warning(f"Статистика: не удалось записать сделку {deal.id}: {short_error(e)}")
    handler.__name__ = f"record_sale_{kind}"
    return handler


BIND_TO_CHAT_INITIALIZED = [remember_chat]
BIND_TO_NEW_MESSAGE = [log_message, chat_commands, sync_message, notify_message, greet, auto_response]
BIND_TO_NEW_DEAL = [record_sale("new"), log_deal, notify_deal, sync_deal("new"), auto_delivery]
BIND_TO_ITEM_PAID = [restore_sold]
BIND_TO_NEW_REVIEW = [notify_review, sync_deal("review"), thank_review]
BIND_TO_ITEM_SENT = [record_sale("sent"), sync_deal("sent"), deal_reply("sent"), remember_sent_deal]
BIND_TO_DEAL_CONFIRMED = [record_sale("confirmed"), notify_confirmed, sync_deal("confirmed"), deal_reply("confirmed"),
                          forget_sent_deal]
BIND_TO_DEAL_ROLLED_BACK = [record_sale("rolled_back"), notify_rolled_back, sync_deal("rolled_back"),
                            deal_reply("rolled_back"), forget_sent_deal]
BIND_TO_DEAL_HAS_PROBLEM = [record_sale("problem"), notify_problem, sync_deal("problem"), deal_reply("problem")]
BIND_TO_DEAL_PROBLEM_RESOLVED = [record_sale("resolved"), notify_problem_resolved]
