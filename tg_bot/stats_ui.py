from __future__ import annotations

import threading
import time
from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.errors import short_error
from Utils.stats import period_start
from tg_bot import keyboards as kbs

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

PERIODS = {"d": "Сегодня", "w": "7 дней", "m": "30 дней", "a": "Всё время"}
BAR = 10
DAYS = 7
EMPTY = "Продаж за этот период нет."


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₽"


def summary_lines(s: dict) -> list[str]:
    if not (s["sold"] or s["work"] or s["refunds"]):
        return [EMPTY]
    lines = [f"✅ Продажи: {s['sold']} на {money(s['revenue'])}", f"🧾 Средний чек: {money(s['avg'])}"]
    work = f"⏳ В работе: {s['work']} на {money(s['work_sum'])}"
    if s["work_problem"]:
        work += f" (🚨 {s['work_problem']} с проблемой)"
    lines += [work, f"↩️ Возвраты: {s['refunds']} на {money(s['refund_sum'])}"]
    return lines


def days_lines(days: list[tuple[str, int, int]]) -> list[str]:
    top = max((total for _, _, total in days), default=0)
    lines = []
    for day, count, total in days:
        bar = "▇" * max(1, round(BAR * total / top)) if total and top else "·"
        lines.append(f"<code>{day} {bar:<{BAR}} {count} · {money(total)}</code>")
    return lines


def account_text(c: Cardinal, period: str, now: float) -> str:
    start = period_start(period, now)
    journal = c.journal
    s = journal.summary(start)
    lines = [f"📊 <b>Статистика — {escape(c.label)}</b> · {PERIODS[period]}", "", *summary_lines(s)]
    top = journal.top_items(start)
    if top:
        lines += ["", "🏆 <b>Топ товаров</b>"]
        lines += [f"{n}. {escape(tools.short(name, 40))} — {count} шт · {money(total)}"
                  for n, (name, count, total) in enumerate(top, 1)]
    if period != "d" and s["sold"]:
        lines += ["", "📅 <b>По дням</b>", *days_lines(journal.by_day(DAYS, now))]
    lines += ["", "<i>Выручка — по цене продажи, до комиссии Playerok.</i>"]
    return "\n".join(lines)


def all_text(tg: TGBot, period: str, now: float) -> str:
    start = period_start(period, now)
    total = {"sold": 0, "revenue": 0, "work": 0, "work_sum": 0, "work_problem": 0, "refunds": 0, "refund_sum": 0}
    rows = []
    for c in tg.hub.cardinals.values():
        try:
            s = c.journal.summary(start)
        except Exception as e:
            c.log.warning(f"Статистика: журнал недоступен: {e}")
            continue
        for key in total:
            total[key] += s[key]
        rows.append(f"👤 {escape(c.label)} — {s['sold']} · {money(s['revenue'])}")
    total["avg"] = round(total["revenue"] / total["sold"]) if total["sold"] else 0
    lines = [f"📊 <b>Статистика — Все аккаунты</b> · {PERIODS[period]}", "", *summary_lines(total)]
    if rows:
        lines += ["", *rows]
    return "\n".join(lines)


def screen_kb(target: str, period: str) -> K:
    kb = K()
    kb.row(*[B(("• " if code == period else "") + title, callback_data=kbs.cb("st", target, code))
             for code, title in PERIODS.items()])
    if target == "all":
        kb.row(B("◀️ Назад", callback_data="menu"))
        return kb
    kb.row(B("👥 Все аккаунты", callback_data=kbs.cb("st", "all", period)),
           B("🔄 Обновить из Playerok", callback_data=kbs.cb("str", target)))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def cb_screen(tg: TGBot, call: CallbackQuery):
    parts = call.data.split(":")
    period = parts[2] if len(parts) > 2 and parts[2] in PERIODS else "w"
    now = time.time()
    if len(parts) > 1 and parts[1] == "all":
        tg.edit(call, all_text(tg, period, now), screen_kb("all", period))
        tg.answer(call)
        return
    c = tg.account_from(call)
    if c is None:
        return
    tg.edit(call, account_text(c, period, now), screen_kb(c.id, period))
    tg.answer(call)


def cb_refresh(tg: TGBot, call: CallbackQuery):
    c = tg.account_from(call)
    if c is None:
        return
    if c.status != "running":
        tg.answer(call, "Аккаунт не запущен", alert=True)
        return
    if c.stats_busy():
        tg.answer(call, "Уже обновляю")
        return
    tg.answer(call, "Обновляю…")
    chat_id = call.message.chat.id

    def run():
        try:
            count = c.refresh_stats()
        except Exception as e:
            c.log.warning(f"Статистика: обновление не удалось: {short_error(e)}")
            tg.bot.send_message(chat_id, f"❌ Не удалось обновить статистику: {escape(short_error(e))}",
                                reply_markup=kbs.back_kb(kbs.cb("st", c.id, "w")))
            return
        text = "⏳ Уже обновляю" if count is None else f"✅ Статистика обновлена: {count} продаж из Playerok"
        tg.bot.send_message(chat_id, text, reply_markup=kbs.back_kb(kbs.cb("st", c.id, "w")))

    threading.Thread(target=run, daemon=True, name=f"{c.id}-stats-refresh").start()


def register(tg: TGBot):
    tg.on_prefix("st:", cb_screen)
    tg.on_prefix("str:", cb_refresh)
