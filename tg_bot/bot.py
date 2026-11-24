from __future__ import annotations

import hmac
import logging
import threading
import time
from html import escape
from typing import TYPE_CHECKING, Callable

import telebot
from telebot.types import BotCommand, CallbackQuery, Message

from Utils import tools
from Utils.settings import SettingsError
from tg_bot import keyboards as kbs
from tg_bot.access import ANY, COMMAND_PERMS, KIND_PERMS, OWNER, required
from tg_bot.keyboards import cb  # noqa: F401

if TYPE_CHECKING:
    from cardinal import Cardinal
    from hub import Hub

logger = logging.getLogger("cardinal.telegram")

MAX_ATTEMPTS = 5
BLOCK_SECONDS = 600
GROUP_TYPES = ("group", "supergroup")
GROUP_CONTENT = ["text", "photo", "document", "voice", "video", "video_note", "sticker", "audio", "animation",
                 "forum_topic_closed", "forum_topic_reopened"]

BUILTIN_COMMANDS = [
    ("menu", "⚙️ Главное меню"),
    ("profile", "👤 Профиль Playerok"),
    ("plugins", "🧩 Плагины"),
    ("blacklist", "⛔ Чёрный список"),
    ("logs", "📜 Логи"),
    ("restart", "♻️ Перезапуск бота"),
    ("help", "❓ Помощь"),
]


def uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    parts.append(f"{minutes} мин")
    return " ".join(parts)


class TGBot:
    def __init__(self, hub: Hub, bot: telebot.TeleBot | None = None):
        self.hub = hub
        self.bot = bot or telebot.TeleBot(hub.env["tg_token"], parse_mode="HTML", threaded=True)
        self.password = hub.env.get("tg_password", "")
        self.users: list[int] = tools.load_cache("tg_users.json", [])
        self.state: dict[str, str] = tools.load_cache("tg_state.json", {})
        self.attempts: dict[int, list] = {}
        self.pending: dict[int, tuple[Callable[[Message], None], bool]] = {}
        self.menu_commands: list[tuple[str, str]] = list(BUILTIN_COMMANDS)
        self.started = False
        self._finalized = False
        self._polling_conflict_reported = False
        from tg_bot.chatsync import ChatSync
        from tg_bot.staff import StaffStore
        self.sync = ChatSync(self)
        self.staff = StaffStore()
        self.staff_drafts: dict[int, dict] = {}
        self.registered_keys: set[str] = set()
        self._bot_username = ""

    def bot_username(self) -> str:
        if not self._bot_username:
            self._bot_username = self.bot.get_me().username
        return self._bot_username

    def forget_user(self, user_id: int) -> None:
        self.pending.pop(user_id, None)
        self.staff_drafts.pop(user_id, None)
        if self.state.pop(str(user_id), None) is not None:
            tools.save_cache("tg_state.json", self.state)

    def remove_owner(self, user_id: int) -> None:
        self.staff.revoke_invites(created_by=user_id)
        if user_id in self.users:
            self.users.remove(user_id)
            tools.save_cache("tg_users.json", self.users)
        self.forget_user(user_id)

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.users

    def is_auth(self, user_id: int) -> bool:
        return self.is_owner(user_id) or self.staff.get(user_id) is not None

    def can(self, user_id: int, perm: str | None, acc_id: str | None = None) -> bool:
        if self.is_owner(user_id):
            return True
        member = self.staff.get(user_id)
        if member is None or perm is None or perm == OWNER:
            return False
        if perm != ANY and perm not in member.get("perms", []):
            return False
        accounts = member.get("accounts", [])
        return acc_id is None or "*" in accounts or acc_id in accounts

    def can_call(self, user_id: int, data: str) -> bool:
        parts = data.split(":")
        if parts[0] in {"ctl", "ctll", "ctlu", "ctlb", "ctlmsg", "ctlup"}:
            if str(user_id) != self.hub.env.get("control_admin_id") or not self.hub.env.get("control_db"):
                return False
        acc_id = parts[1] if len(parts) > 1 and parts[1] in self.hub.cardinals else None
        return self.can(user_id, required(data), acc_id)

    @staticmethod
    def is_private(m: Message) -> bool:
        return getattr(m.chat, "type", "private") == "private"

    @staticmethod
    def is_group(m: Message) -> bool:
        return getattr(m.chat, "type", "") in GROUP_TYPES

    def msg_handler(self, handler: Callable, perm: str = OWNER, **kwargs):
        user_func = kwargs.pop("func", None)
        self.bot.register_message_handler(
            handler, func=lambda m: self.is_private(m) and self.can(m.from_user.id, perm)
            and (user_func is None or user_func(m)), **kwargs
        )

    def cbq_handler(self, handler: Callable, func: Callable, owner_only: bool = True, **kwargs):
        check = self.is_owner if owner_only else self.is_auth
        self.bot.register_callback_query_handler(
            handler, func=lambda call: check(call.from_user.id) and func(call), **kwargs
        )

    def _guarded_call(self, handler: Callable[[TGBot, CallbackQuery], None], call: CallbackQuery):
        if not self.can_call(call.from_user.id, call.data):
            self.answer(call, "🔒 Нет доступа", alert=True)
            return
        try:
            handler(self, call)
        except SettingsError as e:
            self.answer(call, f"⚠️ {e.text}"[:190], alert=True)

    def _guarded_message(self, handler: Callable[[Message], None], m: Message):
        try:
            handler(m)
        except SettingsError as e:
            self.bot.send_message(m.chat.id, f"⚠️ {escape(e.text)}")

    def on_prefix(self, prefix: str, handler: Callable[[TGBot, CallbackQuery], None]):
        self.registered_keys.add(prefix.rstrip(":"))
        self.cbq_handler(lambda call: self._guarded_call(handler, call), lambda call, p=prefix: call.data.startswith(p),
                         owner_only=False)

    def on_exact(self, data: str, handler: Callable[[TGBot, CallbackQuery], None]):
        self.registered_keys.add(data)
        self.cbq_handler(lambda call: self._guarded_call(handler, call), lambda call, d=data: call.data == d,
                         owner_only=False)

    def on_command(self, command: str, handler: Callable[[TGBot, Message], None]):
        self.msg_handler(lambda m: handler(self, m), perm=COMMAND_PERMS.get(command, OWNER), commands=[command])

    def add_command_to_menu(self, command: str, description: str):
        if command not in [c for c, _ in self.menu_commands]:
            self.menu_commands.append((command, description))
        if self.started:
            self._set_commands()

    def _set_commands(self):
        try:
            self.bot.set_my_commands([BotCommand(c, d[:256]) for c, d in self.menu_commands])
        except Exception as e:
            logger.warning(f"Не удалось обновить меню команд: {e}")

    def menu_markup(self, user_id: int, c: Cardinal | None):
        return kbs.main_menu(c, lambda data: self.can_call(user_id, data))

    def recipients(self, account_id: str | None = None, kind: str | None = None) -> list[int]:
        users = list(self.users)
        if account_id is not None:
            users += [uid for uid in self.staff.all() if uid not in users
                      and self.can(uid, KIND_PERMS.get(kind), account_id)]
        return users

    def send_notification(self, text: str, keyboard=None, account_id: str | None = None, kind: str | None = None):
        text = text if len(text) <= 4000 else text[:3990] + "…"
        for user_id in self.recipients(account_id, kind):
            try:
                self.bot.send_message(user_id, text, reply_markup=keyboard, disable_web_page_preview=True)
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление {user_id}: {e}")

    def current(self, user_id: int) -> Cardinal | None:
        cardinals = self.hub.cardinals
        selected = self.state.get(str(user_id))
        if selected in cardinals and self.can(user_id, ANY, selected):
            return cardinals[selected]
        for info in self.hub.registry.accounts:
            if info.id in cardinals and self.can(user_id, ANY, info.id):
                return cardinals[info.id]
        return None

    def select(self, user_id: int, acc_id: str) -> None:
        self.state[str(user_id)] = acc_id
        tools.save_cache("tg_state.json", self.state)

    def ask(self, chat_id: int, user_id: int, prompt: str, on_answer: Callable[[Message], None], keyboard=None,
            accept_documents: bool = False):
        self.pending[user_id] = (on_answer, accept_documents)
        return self.bot.send_message(chat_id, prompt, reply_markup=keyboard)

    def account_from(self, call: CallbackQuery, index: int = 1) -> Cardinal | None:
        parts = call.data.split(":")
        cardinal = self.hub.cardinals.get(parts[index]) if len(parts) > index else None
        if cardinal is None:
            self.bot.answer_callback_query(call.id, "Аккаунт не найден", show_alert=True)
        return cardinal

    def edit(self, call: CallbackQuery, text: str, keyboard=None):
        try:
            self.bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=keyboard,
                                       disable_web_page_preview=True)
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                raise

    def answer(self, call: CallbackQuery, text: str | None = None, alert: bool = False):
        self.bot.answer_callback_query(call.id, text, show_alert=alert)

    def modules(self) -> list:
        from tg_bot import (accounts_ui, autodelivery_ui, autoresponse_ui, blacklist_ui, chats_ui, chatsync_ui,
                            dealreplies_ui, deals_ui, misc_ui, profiles_ui, settings_ui, staff_ui, stats_ui,
                            templates_ui, withdraw_ui, control_ui)
        return [misc_ui, accounts_ui, settings_ui, autoresponse_ui, autodelivery_ui, blacklist_ui, dealreplies_ui,
                templates_ui, chatsync_ui, deals_ui, chats_ui, stats_ui, profiles_ui, staff_ui, withdraw_ui, control_ui]

    def init(self):
        for module in self.modules():
            module.register(self)

    def finalize(self):
        if self._finalized:
            return
        self._finalized = True
        self.bot.register_message_handler(self.sync.on_group_message, func=self.is_group, content_types=GROUP_CONTENT)
        self.msg_handler(self.on_document, perm=ANY, content_types=["document"])
        self.bot.register_message_handler(self.on_text, func=lambda m: True, content_types=["text"])
        self.bot.register_callback_query_handler(self.on_unknown_callback, func=lambda call: True)

    def start(self):
        self.finalize()
        self.started = True
        self._set_commands()
        self.sync.queue.start()
        self.sync.catch_up()
        self.sync.start_own_poller()
        if self.hub.env.get("control_url"):
            from Utils.control_client import ControlClient
            self.control_client = ControlClient(self, self.hub.env["control_url"])
            self.control_client.start()
        threading.Thread(target=self._poll, daemon=True, name="telegram").start()
        self.send_notification(f"✅ <b>Playerok Cardinal запущен</b>\n👥 Аккаунтов: {len(self.hub.cardinals)}\n"
                               f"🧩 Плагинов: {len(self.hub.plugins.plugins)}", kbs.back_kb("menu"))

    def polling_kwargs(self) -> dict:
        return {"skip_pending": not self.sync.caught_up, "timeout": 30, "long_polling_timeout": 30,
                "logger_level": logging.ERROR}

    def _poll(self):
        while True:
            try:
                self.bot.infinity_polling(**self.polling_kwargs())
            except Exception as e:
                error_text = str(e).lower()
                if getattr(e, "error_code", None) == 409 or ("409" in error_text and "conflict" in error_text):
                    if not self._polling_conflict_reported:
                        self._polling_conflict_reported = True
                        message = ("⚠️ Telegram остановил получение сообщений: этот токен бота уже используется "
                                   "другим запущенным экземпляром. Остановите вторую копию и перезапустите эту.")
                        logger.error(message)
                        self.send_notification(message)
                    time.sleep(60)
                    continue
                logger.error(f"Telegram polling упал: {e}")
            time.sleep(5)

    def check_password(self, user_id: int, text: str, username: str | None) -> str:
        now = time.time()
        count, blocked_until = self.attempts.get(user_id, [0, 0])
        if blocked_until > now:
            return "blocked"
        if not self.password:
            return "no_password"
        if hmac.compare_digest(text.strip().encode(), self.password.encode()):
            self.attempts.pop(user_id, None)
            if user_id not in self.users:
                self.send_notification(f"🔐 В панель вошёл новый пользователь: "
                                       f"{escape('@' + username if username else str(user_id))} (ID {user_id})")
                self.users.append(user_id)
                tools.save_cache("tg_users.json", self.users)
            logger.info(f"Telegram: пользователь {user_id} авторизован")
            return "ok"
        count += 1
        if count >= MAX_ATTEMPTS:
            self.attempts[user_id] = [0, now + BLOCK_SECONDS]
            logger.warning(f"Telegram: {user_id} заблокирован на 10 минут после {MAX_ATTEMPTS} неверных паролей")
            return "blocked"
        self.attempts[user_id] = [count, 0]
        return "wrong"

    def on_text(self, m: Message):
        if not self.is_private(m):
            return
        user_id = m.from_user.id
        if not self.is_auth(user_id):
            if m.text.startswith("/start inv_"):
                from tg_bot import staff_ui
                staff_ui.accept_invite(self, m)
                return
            if m.text.startswith("/"):
                self.bot.send_message(user_id, "🔒 Это панель управления Playerok Cardinal.\nВведите пароль:")
                return
            result = self.check_password(user_id, m.text, m.from_user.username)
            answers = {
                "ok": "✅ Доступ открыт! Теперь сюда будут приходить уведомления.",
                "wrong": "❌ Неверный пароль.",
                "blocked": "⛔ Слишком много попыток. Попробуйте через 10 минут.",
                "no_password": "⚠️ Пароль панели не задан (TELEGRAM_PASSWORD в .env).",
            }
            self.bot.send_message(user_id, answers[result])
            if result == "ok":
                from tg_bot import misc_ui
                misc_ui.cmd_menu(self, m)
            return
        pending = self.pending.pop(user_id, None)
        if pending:
            self._guarded_message(pending[0], m)
            return
        self.bot.send_message(user_id, "🤔 Не понял команду. Откройте меню: /menu")

    def on_document(self, m: Message):
        if not self.is_private(m):
            return
        pending = self.pending.get(m.from_user.id)
        if pending and pending[1]:
            self.pending.pop(m.from_user.id, None)
            self._guarded_message(pending[0], m)
            return
        if not self.is_owner(m.from_user.id):
            return
        from tg_bot import misc_ui
        self._guarded_message(lambda msg: misc_ui.handle_upload(self, msg), m)

    def on_unknown_callback(self, call: CallbackQuery):
        if not self.is_auth(call.from_user.id):
            self.bot.answer_callback_query(call.id, "🔒 Нет доступа", show_alert=True)
            return
        self.bot.answer_callback_query(call.id, "Кнопка устарела")
