from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telebot.types import CallbackQuery, Message
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from tg_bot import keyboards as kbs
from tg_bot.access import PERM_TITLES, PERMS

if TYPE_CHECKING:
    from tg_bot.bot import TGBot

DEFAULT_PERMS = ["chats", "deals"]
SHARED_GROUP_NOTE = ("⚠️ Если сотрудник состоит в общей группе синхронизации, он видит все её чаты, даже по аккаунтам "
                     "без доступа: бот не может прятать темы. Для разных людей лучше разные группы.")


def perms_line(perms: list[str]) -> str:
    return ", ".join(PERM_TITLES[p] for p in PERMS if p in perms) or "нет прав"


def accounts_line(tg: TGBot, accounts: list[str]) -> str:
    if "*" in accounts:
        return "все"
    labels = [tg.hub.cardinals[a].label for a in accounts if a in tg.hub.cardinals]
    return escape(", ".join(labels)) or "нет"


def bound_groups(tg: TGBot) -> list[str]:
    titles = []
    for c in tg.hub.cardinals.values():
        cfg = c.settings.get("chat_sync", {})
        if cfg.get("group_id") and cfg.get("group_title", "") not in titles:
            titles.append(cfg.get("group_title") or str(cfg["group_id"]))
    return titles


def list_text(tg: TGBot, user_id: int) -> str:
    lines = ["👥 <b>Сотрудники</b>", "", "👑 <b>Владельцы</b> (вошли по паролю панели):"]
    lines += [f"• ID {uid}" + (" — это вы" if uid == user_id else "") for uid in tg.users]
    staff = tg.staff.all()
    lines += ["", "🧑‍💼 <b>Сотрудники</b>:"]
    lines += [f"• {escape(m['name'])} — {perms_line(m['perms'])}; аккаунты: {accounts_line(tg, m['accounts'])}"
              for m in staff.values()] or ["пока нет"]
    return "\n".join(lines)


def list_kb(tg: TGBot, user_id: int) -> K:
    kb = K()
    for uid, member in tg.staff.all().items():
        kb.row(B(f"🧑‍💼 {tools.short(member['name'], 30)}", callback_data=kbs.cb("sf1", uid)))
    for uid in tg.users:
        if uid != user_id:
            kb.row(B(f"🗑 Убрать владельца ID {uid}", callback_data=kbs.cb("sfo", uid)))
    kb.row(B("➕ Пригласить сотрудника", callback_data="sfn"))
    kb.row(B("🚫 Отозвать все приглашения", callback_data="sfr"))
    kb.row(B("◀️ Назад", callback_data="menu"))
    return kb


def cb_list(tg: TGBot, call: CallbackQuery):
    tg.edit(call, list_text(tg, call.from_user.id), list_kb(tg, call.from_user.id))
    tg.answer(call)


def uid_of(call: CallbackQuery) -> int | None:
    parts = call.data.split(":")
    return int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else None


def card_text(tg: TGBot, member: dict) -> str:
    return (f"🧑‍💼 <b>{escape(member['name'])}</b>\n\nПрава: {perms_line(member['perms'])}\n"
            f"Аккаунты: {accounts_line(tg, member['accounts'])}\n\n"
            f"Деньги, аккаунты, плагины, поднятие и сотрудники — всегда только у владельца.")


def access_rows(tg: TGBot, kb: K, perms: list[str], accounts: list[str], perm_prefix: tuple, acc_prefix: tuple) -> None:
    for perm in PERMS:
        kb.row(B(f"{kbs.dot(perm in perms)} {PERM_TITLES[perm]}", callback_data=kbs.cb(*perm_prefix, perm)))
    kb.row(B(f"{kbs.dot('*' in accounts)} Все аккаунты", callback_data=kbs.cb(*acc_prefix, "*")))
    if "*" not in accounts:
        for info in tg.hub.registry.accounts:
            c = tg.hub.cardinals.get(info.id)
            if c is not None:
                kb.row(B(f"{kbs.dot(c.id in accounts)} {tools.short(c.label, 30)}",
                         callback_data=kbs.cb(*acc_prefix, c.id)))


def card_kb(tg: TGBot, uid: int, member: dict) -> K:
    kb = K()
    access_rows(tg, kb, member["perms"], member["accounts"], ("sfp", uid), ("sfa", uid))
    kb.row(B("🗑 Удалить сотрудника", callback_data=kbs.cb("sfd", uid)))
    kb.row(B("◀️ К сотрудникам", callback_data="sf"))
    return kb


def member_of(tg: TGBot, call: CallbackQuery) -> tuple[int | None, dict | None]:
    uid = uid_of(call)
    member = tg.staff.get(uid) if uid is not None else None
    if member is None:
        tg.answer(call, "Сотрудник не найден", alert=True)
    return uid, member


def show_card(tg: TGBot, call: CallbackQuery, uid: int):
    member = tg.staff.get(uid)
    tg.edit(call, card_text(tg, member), card_kb(tg, uid, member))


def cb_card(tg: TGBot, call: CallbackQuery):
    uid, member = member_of(tg, call)
    if member is None:
        return
    show_card(tg, call, uid)
    tg.answer(call)


def cb_perm(tg: TGBot, call: CallbackQuery):
    uid, member = member_of(tg, call)
    if member is None:
        return
    perm = call.data.split(":")[2] if len(call.data.split(":")) > 2 else ""
    if perm not in PERMS:
        tg.answer(call, "Нет такого права", alert=True)
        return
    tg.staff.set_perm(uid, perm, perm not in member["perms"])
    tg.pending.pop(uid, None)
    show_card(tg, call, uid)
    tg.answer(call)


def toggle_accounts(accounts: list[str], value: str) -> list[str]:
    if value == "*":
        return [] if "*" in accounts else ["*"]
    return [a for a in accounts if a != value] if value in accounts else [a for a in accounts if a != "*"] + [value]


def cb_account(tg: TGBot, call: CallbackQuery):
    uid, member = member_of(tg, call)
    if member is None:
        return
    value = call.data.split(":")[2] if len(call.data.split(":")) > 2 else ""
    if value != "*" and value not in tg.hub.cardinals:
        tg.answer(call, "Аккаунт не найден", alert=True)
        return
    accounts = toggle_accounts(member["accounts"], value)
    tg.staff.add(uid, member["name"], member["perms"], accounts)
    tg.pending.pop(uid, None)
    show_card(tg, call, uid)
    tg.answer(call)


def cb_delete(tg: TGBot, call: CallbackQuery):
    uid, member = member_of(tg, call)
    if member is None:
        return
    tg.bot.send_message(call.message.chat.id, f"🗑 Удалить сотрудника <b>{escape(member['name'])}</b>? "
                                              f"Доступ к панели пропадёт сразу.",
                        reply_markup=kbs.confirm_kb("sfd", uid))
    tg.answer(call)


def cb_delete_yes(tg: TGBot, call: CallbackQuery):
    uid, member = member_of(tg, call)
    if member is None:
        return
    tg.staff.remove(uid)
    tg.forget_user(uid)
    text = f"🗑 Сотрудник <b>{escape(member['name'])}</b> удалён, доступ к панели закрыт."
    groups = bound_groups(tg)
    if groups:
        text += ("\n\n⚠️ Если он состоит в группах синхронизации, удалите его оттуда вручную: "
                 + ", ".join(f"<b>{escape(g)}</b>" for g in groups))
    tg.edit(call, text, kbs.back_kb("sf"))
    tg.answer(call, "Удалено")


def cb_owner_remove(tg: TGBot, call: CallbackQuery):
    uid = uid_of(call)
    if uid is None or uid not in tg.users:
        tg.answer(call, "Владелец не найден", alert=True)
        return
    tg.bot.send_message(call.message.chat.id, f"🗑 Убрать владельца ID {uid}? Он потеряет доступ к панели "
                                              f"(вернуться сможет только по паролю).",
                        reply_markup=kbs.confirm_kb("sfo", uid))
    tg.answer(call)


def cb_owner_remove_yes(tg: TGBot, call: CallbackQuery):
    uid = uid_of(call)
    if uid == call.from_user.id:
        tg.answer(call, "Себя убрать нельзя", alert=True)
        return
    if uid not in tg.users or len(tg.users) <= 1:
        tg.answer(call, "Нельзя убрать последнего владельца", alert=True)
        return
    tg.remove_owner(uid)
    tg.edit(call, f"🗑 Владелец ID {uid} убран.", kbs.back_kb("sf"))
    tg.answer(call, "Убран")


def draft(tg: TGBot, user_id: int) -> dict:
    return tg.staff_drafts.setdefault(user_id, {"perms": list(DEFAULT_PERMS), "accounts": ["*"]})


def draft_kb(tg: TGBot, user_id: int) -> K:
    d = draft(tg, user_id)
    kb = K()
    access_rows(tg, kb, d["perms"], d["accounts"], ("sfnp",), ("sfna",))
    kb.row(B("🔗 Создать ссылку", callback_data="sfnc"))
    kb.row(B("◀️ К сотрудникам", callback_data="sf"))
    return kb


def draft_text() -> str:
    return ("➕ <b>Приглашение сотрудника</b>\n\nВыберите права и аккаунты, затем «Создать ссылку». Ссылка одноразовая "
            "и действует 24 часа.")


def cb_new(tg: TGBot, call: CallbackQuery):
    tg.staff_drafts.pop(call.from_user.id, None)
    tg.edit(call, draft_text(), draft_kb(tg, call.from_user.id))
    tg.answer(call)


def cb_new_perm(tg: TGBot, call: CallbackQuery):
    perm = call.data.split(":")[1] if ":" in call.data else ""
    if perm not in PERMS:
        tg.answer(call, "Нет такого права", alert=True)
        return
    d = draft(tg, call.from_user.id)
    d["perms"] = [p for p in d["perms"] if p != perm] if perm in d["perms"] else d["perms"] + [perm]
    tg.edit(call, draft_text(), draft_kb(tg, call.from_user.id))
    tg.answer(call)


def cb_new_account(tg: TGBot, call: CallbackQuery):
    value = call.data.split(":")[1] if ":" in call.data else ""
    if value != "*" and value not in tg.hub.cardinals:
        tg.answer(call, "Аккаунт не найден", alert=True)
        return
    d = draft(tg, call.from_user.id)
    d["accounts"] = toggle_accounts(d["accounts"], value)
    tg.edit(call, draft_text(), draft_kb(tg, call.from_user.id))
    tg.answer(call)


def cb_new_create(tg: TGBot, call: CallbackQuery):
    d = draft(tg, call.from_user.id)
    if not d["perms"] or not d["accounts"]:
        tg.answer(call, "Выберите хотя бы одно право и один аккаунт", alert=True)
        return
    code = tg.staff.create_invite(d["perms"], d["accounts"], created_by=call.from_user.id)
    tg.staff_drafts.pop(call.from_user.id, None)
    link = f"https://t.me/{tg.bot_username()}?start=inv_{code}"
    text = (f"🔗 <b>Ссылка для сотрудника</b> (одноразовая, 24 часа):\n<code>{link}</code>\n\n"
            f"Права: {perms_line(d['perms'])}\nАккаунты: {accounts_line(tg, d['accounts'])}\n\n{SHARED_GROUP_NOTE}")
    tg.edit(call, text, kbs.back_kb("sf"))
    tg.answer(call)


def cb_revoke_invites(tg: TGBot, call: CallbackQuery):
    count = tg.staff.revoke_invites()
    tg.answer(call, f"Отозвано приглашений: {count}", alert=True)


def accept_invite(tg: TGBot, m: Message) -> None:
    code = (m.text or "").split("inv_", 1)[-1].strip()
    invite = tg.staff.take_invite(code) if code else None
    if invite is None:
        tg.bot.send_message(m.chat.id, "❌ Приглашение недействительно или истекло. Попросите владельца прислать "
                                       "новую ссылку.")
        return
    user = m.from_user
    name = f"@{user.username}" if getattr(user, "username", None) else (getattr(user, "first_name", None)
                                                                      or str(user.id))
    tg.staff.add(user.id, name, invite["perms"], invite["accounts"])
    tg.bot.send_message(m.chat.id, f"✅ Вы добавлены как сотрудник.\nПрава: {perms_line(invite['perms'])}\n"
                                   f"Аккаунты: {accounts_line(tg, invite['accounts'])}")
    tg.send_notification(f"👥 Новый сотрудник: <b>{escape(name)}</b> (ID {user.id})\n"
                         f"Права: {perms_line(invite['perms'])}")
    from tg_bot import misc_ui
    misc_ui.cmd_menu(tg, m)


def register(tg: TGBot):
    tg.on_exact("sf", cb_list)
    tg.on_exact("sfn", cb_new)
    tg.on_exact("sfnc", cb_new_create)
    tg.on_exact("sfr", cb_revoke_invites)
    for prefix, handler in {"sf1:": cb_card, "sfp:": cb_perm, "sfa:": cb_account, "sfd:": cb_delete,
                            "sfd_yes:": cb_delete_yes, "sfo:": cb_owner_remove, "sfo_yes:": cb_owner_remove_yes,
                            "sfnp:": cb_new_perm, "sfna:": cb_new_account}.items():
        tg.on_prefix(prefix, handler)
