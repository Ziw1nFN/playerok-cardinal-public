from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING

from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from Utils import tools
from Utils.errors import short_error
from tg_bot import chatview
from tg_bot import keyboards as kbs
from tg_bot.sync_queue import GroupQueue, Job, classify

if TYPE_CHECKING:
    from cardinal import Cardinal
    from tg_bot.bot import TGBot

logger = logging.getLogger("cardinal.telegram")

TOPICS_FILE = "topics.json"
HISTORY_ON_CREATE = 10
PAUSE_NOTIFY_INTERVAL = 3600
TOPIC_NAME_LIMIT = 128
CAPTION_LIMIT = 1024
ECHO_KEEP = 200
HANDLED_FILE = "sync_handled.json"
HANDLED_KEEP = 500
CATCH_UP_SECONDS = 600
CATCH_UP_BATCHES = 20
CATCH_UP_REQUEST_TIMEOUT = 10
OWN_POLL_SECONDS = 20
OWN_POLL_CHATS = 24
OWN_LABELS = {"bot": "🤖 <b>Бот</b>", "owner": "🧑‍💼 <b>Вы</b>"}


def message_ts(msg) -> float | None:
    created = getattr(msg, "created_at", None)
    if not created:
        return None
    try:
        return datetime.fromisoformat(str(created).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def sync_of(c: Cardinal) -> ChatSync | None:
    return getattr(getattr(getattr(c, "hub", None), "telegram", None), "sync", None)


def card_kb(acc_id: str, chat_id: str) -> K:
    kb = K()
    kb.row(B("🌐 Чат на сайте", url=tools.chat_link(chat_id)))
    kb.row(B("📋 Сделки чата", callback_data=kbs.cb("tdl", acc_id, chat_id)),
           B("💬 Шаблоны", callback_data=kbs.cb("ttp", acc_id, chat_id)))
    return kb


class ChatSync:
    def __init__(self, tg: TGBot):
        self.tg = tg
        self._lock = threading.RLock()
        self._pause_notified: dict[str, float] = {}
        self._echo: dict[str, deque] = {}
        self._echo_lock = threading.Lock()
        self.caught_up = False
        self.queue = GroupQueue(self.deliver, self.on_failed)
        self._mirrored: dict[str, deque] = {}
        self._own_state: dict[str, dict] = {}
        self._own_thread: threading.Thread | None = None

    def mark_echo(self, c: Cardinal, message_id: str | None) -> None:
        if message_id:
            with self._echo_lock:
                self._echo.setdefault(c.id, deque(maxlen=ECHO_KEEP)).append(message_id)

    def is_echo(self, c: Cardinal, message_id: str | None) -> bool:
        with self._echo_lock:
            return bool(message_id) and message_id in self._echo.get(c.id, ())

    def _claim_own(self, c: Cardinal, message_id: str | None) -> bool:
        if not message_id:
            return True
        with self._echo_lock:
            seen = self._mirrored.setdefault(c.id, deque(maxlen=ECHO_KEEP))
            if message_id in seen:
                return False
            seen.append(message_id)
            return True

    def mirror_own(self, c: Cardinal, chat_id: str, message_id: str | None, text: str | None,
                   has_images: bool = False, who: str = "bot") -> None:
        if not self.is_active(c) or self.is_echo(c, message_id) or not self._claim_own(c, message_id):
            return
        body = escape(text) if text else ""
        if has_images:
            body = f"{body}\n🖼 фото" if body else "🖼 фото"
        if body:
            self.post(c, chat_id, f"{OWN_LABELS.get(who, OWN_LABELS['bot'])}: {body}", source_message_id=message_id)

    def _post_own(self, c: Cardinal, chat_id: str, msg, ts: float) -> None:
        who = f"{OWN_LABELS['owner']} · {datetime.fromtimestamp(ts).strftime('%H:%M')}"
        if msg.text:
            self.post(c, chat_id, f"{who}: {escape(msg.text)}", source_message_id=msg.id)
        for image in getattr(msg, "images", None) or []:
            self.post(c, chat_id, f"{who}:", photo=image.url, source_message_id=msg.id)

    def poll_own(self, c: Cardinal, now: float | None = None) -> None:
        from handlers import is_pm, is_system_text
        now = time.time() if now is None else now
        state = self._own_state.setdefault(c.id, {"since": now, "chats": {}})
        my_id = getattr(c.account, "id", None)
        for chat in c.account.get_chats(count=OWN_POLL_CHATS).chats:
            last = getattr(chat, "last_message", None)
            if last is None or not is_pm(c, chat):
                continue
            seen = state["chats"].get(chat.id)
            if seen is not None and seen[0] == last.id:
                continue
            after = seen[1] if seen is not None and seen[1] is not None else state["since"]
            last_ts = message_ts(last)
            if seen is None and (last_ts is None or last_ts < after):
                state["chats"][chat.id] = (last.id, last_ts)
                continue
            try:
                messages = c.account.get_chat_messages(chat.id, count=24).messages
            except Exception as e:
                c.log.debug(f"Не удалось получить сообщения чата {chat.id}: {short_error(e)}")
                continue
            state["chats"][chat.id] = (last.id, last_ts)
            for msg in sorted(messages, key=lambda m: message_ts(m) or 0):
                ts = message_ts(msg)
                if (ts is None or ts < after or getattr(getattr(msg, "user", None), "id", None) != my_id
                        or (seen is not None and msg.id == seen[0]) or is_system_text(msg.text)
                        or not (msg.text or getattr(msg, "images", None))):
                    continue
                if self.is_echo(c, msg.id) or not self._claim_own(c, msg.id):
                    continue
                self._post_own(c, chat.id, msg, ts)

    def poll_all(self, now: float | None = None) -> None:
        for c in list(self.tg.hub.cardinals.values()):
            if c.status != "running" or c.account is None or not self.is_active(c):
                self._own_state.pop(c.id, None)
                continue
            try:
                self.poll_own(c, now)
            except Exception as e:
                c.log.warning(f"Не удалось проверить свои сообщения в чатах: {short_error(e)}")

    def _own_loop(self) -> None:
        while True:
            try:
                self.poll_all()
            except Exception:
                logger.exception("Ошибка проверки своих сообщений в чатах")
            time.sleep(OWN_POLL_SECONDS)

    def history_without_mirrored(self, c: Cardinal, messages: list) -> list:
        my_id = getattr(c.account, "id", None)
        kept = []
        with self._echo_lock:
            seen = self._mirrored.setdefault(c.id, deque(maxlen=ECHO_KEEP))
            for msg in messages:
                own = getattr(getattr(msg, "user", None), "id", None) == my_id
                if own and msg.id in seen:
                    continue
                if own:
                    seen.append(msg.id)
                kept.append(msg)
        return kept

    def start_own_poller(self) -> None:
        if self._own_thread is not None and self._own_thread.is_alive():
            return
        self._own_thread = threading.Thread(target=self._own_loop, daemon=True, name="own-messages")
        self._own_thread.start()

    def post(self, c: Cardinal, chat_id: str, text: str, keyboard=None, photo=None, fallback=None,
             source_message_id: str | None = None) -> None:
        if self.is_active(c):
            self.queue.put(Job(c.id, chat_id, text, keyboard, photo, fallback, source_message_id))

    def deliver(self, job: Job) -> None:
        c = self.tg.hub.cardinals.get(job.acc_id)
        if c is None:
            return
        if not self.is_active(c):
            if job.fallback:
                c.notify(*job.fallback)
            return
        if self.is_echo(c, job.source_message_id):
            return
        try:
            self._send(c, job)
        except Exception as e:
            kind = classify(e)
            if kind == "thread_missing":
                self.forget_topic(c, job.chat_id)
                self._send(c, job)
                return
            if kind == "lost_group":
                self.pause(c, short_error(e))
            raise

    def on_failed(self, job: Job, e: Exception) -> None:
        c = self.tg.hub.cardinals.get(job.acc_id)
        if c is None:
            return
        c.log.warning(f"Не удалось отправить в группу сообщение чата {job.chat_id}: {short_error(e)}")
        if job.fallback:
            c.notify(*job.fallback)

    def catch_up(self, max_age: int = CATCH_UP_SECONDS, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        self.caught_up = False
        handled, offset = 0, None
        try:
            for _ in range(CATCH_UP_BATCHES):
                updates = self.bot.get_updates(offset=offset, timeout=CATCH_UP_REQUEST_TIMEOUT, long_polling_timeout=1)
                if not updates:
                    break
                for update in updates:
                    m = getattr(update, "message", None)
                    if (m is None or not self.tg.is_group(m) or getattr(m, "date", 0) < now - max_age
                            or not getattr(m, "is_topic_message", False)
                            or self.find_chat(m.chat.id, m.message_thread_id) is None):
                        continue
                    self.on_group_message(m)
                    handled += 1
                offset = max(u.update_id for u in updates) + 1
        except Exception as e:
            logger.warning(f"Не удалось получить накопившиеся сообщения Telegram: {e}")
            return handled
        self.caught_up = True
        if handled:
            logger.info(f"Досланы сообщения из тем, написанные во время перезапуска: {handled}")
        return handled

    def remember_handled(self, chat_id: int, message_id: int) -> bool:
        key = f"{chat_id}:{message_id}"
        with self._lock:
            handled = tools.load_cache(HANDLED_FILE, [])
            if key in handled:
                return False
            handled.append(key)
            tools.save_cache(HANDLED_FILE, handled[-HANDLED_KEEP:])
            return True

    def on_group_message(self, m) -> None:
        try:
            self._on_group_message(m)
        except Exception:
            logger.exception("Ошибка обработки сообщения из группы")

    def _on_group_message(self, m) -> None:
        content = getattr(m, "content_type", "text")
        thread = m.message_thread_id if getattr(m, "is_topic_message", False) else None
        if content in ("forum_topic_closed", "forum_topic_reopened"):
            if getattr(m, "message_thread_id", None):
                self.set_closed(m.chat.id, m.message_thread_id, content == "forum_topic_closed")
            return
        if not thread or not self.tg.is_auth(m.from_user.id) or (m.text or "").startswith("/"):
            return
        found = self.find_chat(m.chat.id, thread)
        if found is None or not self.remember_handled(m.chat.id, m.message_id):
            return
        c, chat_id = found
        if not self.tg.can(m.from_user.id, "chats", c.id):
            self._reply(m, "🔒 Нет доступа к чатам этого аккаунта — сообщение не отправлено")
            return
        if c.status != "running" or self.tg.hub.cardinals.get(c.id) is not c:
            self._reply(m, "⚠️ Аккаунт не запущен, сообщение не отправлено")
            return
        if content == "text":
            self._report(m, c, c.send_once(chat_id, m.text))
        elif content == "photo":
            try:
                file = self.bot.get_file(m.photo[-1].file_id)
                data = self.bot.download_file(file.file_path)
            except Exception as e:
                self._reply(m, f"❌ Не удалось скачать фото: {escape(short_error(e))}")
                return
            result = c.send_once(chat_id, images=[data])
            caption = getattr(m, "caption", None)
            if result.status != "sent" or not caption:
                self._report(m, c, result)
                return
            self.mark_echo(c, result.message_id)
            caption_result = c.send_once(chat_id, caption)
            if caption_result.status == "sent":
                self._report(m, c, caption_result)
                return
            reason = caption_result.error if caption_result.status == "rejected" else \
                "не знаю, дошла ли — проверьте на сайте"
            self._reply(m, f"🖼 Фото отправлено, подпись — нет: {escape(reason)}")
        else:
            self._reply(m, "Можно отправить только текст или фото")

    def _reply(self, m, text: str) -> None:
        try:
            self.bot.send_message(m.chat.id, text, message_thread_id=getattr(m, "message_thread_id", None),
                                  reply_to_message_id=m.message_id)
        except Exception as e:
            logger.warning(f"Не удалось ответить в тему: {e}")

    def _report(self, m, c: Cardinal, result) -> None:
        if result.status == "sent":
            self.mark_echo(c, result.message_id)
        elif result.status == "rejected":
            self._reply(m, f"❌ Не отправлено: {escape(result.error)}")
        else:
            self._reply(m, "⚠️ Не знаю, дошло ли — проверьте на сайте")

    def _send(self, c: Cardinal, job: Job) -> None:
        group_id = self.config(c)["group_id"]
        thread = self.ensure_topic(c, job.chat_id, skip_message_id=job.source_message_id)
        text = job.text
        if job.photo is not None:
            caption, *rest = chatview.split_text(text, CAPTION_LIMIT)
            try:
                self.bot.send_photo(group_id, job.photo, caption=caption, reply_markup=job.keyboard,
                                    message_thread_id=thread)
                text = "\n".join(rest)
            except Exception as e:
                if classify(e) != "other":
                    raise
                link = escape(job.photo) if isinstance(job.photo, str) else "фото"
                text = f"{text}\n🖼 {link}"
            if not text:
                return
        parts = chatview.split_text(text)
        for i, part in enumerate(parts):
            self.bot.send_message(group_id, part, reply_markup=job.keyboard if i == len(parts) - 1 else None,
                                  message_thread_id=thread, disable_web_page_preview=True)

    @property
    def bot(self):
        return self.tg.bot

    def config(self, c: Cardinal) -> dict:
        return c.settings["chat_sync"]

    def is_active(self, c: Cardinal) -> bool:
        cfg = self.config(c)
        return bool(cfg.get("enabled") and cfg.get("group_id") and not cfg.get("paused"))

    def bind(self, c: Cardinal, group_id: int, title: str) -> None:
        c.update_settings(lambda s: s["chat_sync"].update(group_id=group_id, group_title=title, paused=""))

    def unbind(self, c: Cardinal) -> None:
        c.update_settings(lambda s: s["chat_sync"].update(group_id=None, group_title="", paused=""))

    def accounts_in(self, group_id: int) -> list[Cardinal]:
        return [c for c in self.tg.hub.cardinals.values() if self.config(c).get("group_id") == group_id]

    def pause(self, c: Cardinal, reason: str) -> None:
        c.update_settings(lambda s: s["chat_sync"].__setitem__("paused", reason))
        c.log.warning(f"Синхронизация чатов приостановлена: {reason}")
        now = time.time()
        if now - self._pause_notified.get(c.id, 0) < PAUSE_NOTIFY_INTERVAL:
            return
        self._pause_notified[c.id] = now
        c.notify(f"⏸ <b>Синхронизация чатов приостановлена</b>: {escape(reason)}\n"
                 f"Сообщения покупателей снова приходят сюда. Проверьте группу и права бота, затем напишите "
                 f"<code>/bind</code> в группе или включите синхронизацию в «🔗 Синхронизация».",
                 kbs.back_kb(kbs.cb("cs", c.id)))

    def _topics(self, c: Cardinal) -> dict:
        data = c.storage.load_cache(TOPICS_FILE, {})
        if data.get("group_id") != self.config(c).get("group_id"):
            return {}
        return data.get("topics", {})

    def _save_topics(self, c: Cardinal, topics: dict) -> None:
        c.storage.save_cache(TOPICS_FILE, {"group_id": self.config(c).get("group_id"), "topics": topics})

    def topic_of(self, c: Cardinal, chat_id: str) -> dict | None:
        with self._lock:
            topic = self._topics(c).get(chat_id)
            return dict(topic) if topic else None

    def find_chat(self, group_id: int, thread_id: int) -> tuple[Cardinal, str] | None:
        with self._lock:
            for c in self.accounts_in(group_id):
                for chat_id, topic in self._topics(c).items():
                    if topic.get("thread") == thread_id:
                        return c, chat_id
        return None

    def set_closed(self, group_id: int, thread_id: int, closed: bool) -> None:
        with self._lock:
            found = self.find_chat(group_id, thread_id)
            if found is None:
                return
            c, chat_id = found
            topics = self._topics(c)
            topics[chat_id]["closed"] = closed
            self._save_topics(c, topics)

    def forget_topic(self, c: Cardinal, chat_id: str) -> None:
        with self._lock:
            topics = self._topics(c)
            if topics.pop(chat_id, None) is not None:
                self._save_topics(c, topics)

    def topic_name(self, c: Cardinal, chat_id: str, chat) -> str:
        buyer = c.buyer_of_chat(chat) if chat is not None else None
        name = getattr(buyer, "username", None) or f"Покупатель {chat_id[:8]}"
        if len(self.accounts_in(self.config(c).get("group_id"))) > 1:
            name = f"{name} · {c.label}"
        return name[:TOPIC_NAME_LIMIT]

    def _existing_thread(self, c: Cardinal, chat_id: str) -> int | None:
        group_id = self.config(c)["group_id"]
        topics = self._topics(c)
        topic = topics.get(chat_id)
        if topic is None:
            return None
        if topic.get("closed"):
            try:
                self.bot.reopen_forum_topic(group_id, topic["thread"])
            except Exception as e:
                if classify(e) in ("flood", "thread_missing", "lost_group"):
                    raise
                c.log.warning(f"Не удалось открыть тему чата {chat_id}: {short_error(e)}")
            topic["closed"] = False
            self._save_topics(c, topics)
        return topic["thread"]

    def ensure_topic(self, c: Cardinal, chat_id: str, skip_message_id: str | None = None) -> int:
        with self._lock:
            thread = self._existing_thread(c, chat_id)
            if thread is not None:
                return thread
        try:
            chat = c.account.get_chat(chat_id)
        except Exception as e:
            c.log.warning(f"Не удалось получить чат {chat_id} для темы: {e}")
            chat = None
        with self._lock:
            thread = self._existing_thread(c, chat_id)
            if thread is not None:
                return thread
            group_id = self.config(c)["group_id"]
            topics = self._topics(c)
            name = self.topic_name(c, chat_id, chat)
            thread = self.bot.create_forum_topic(group_id, name).message_thread_id
            topics[chat_id] = {"thread": thread, "closed": False}
            self._save_topics(c, topics)
            self._post_intro(c, chat_id, group_id, thread, name, skip_message_id)
            return thread

    def _post_intro(self, c: Cardinal, chat_id: str, group_id: int, thread: int, name: str,
                    skip_message_id: str | None) -> None:
        buyer = name.split(" · ")[0]
        try:
            self.bot.send_message(group_id, f"{chatview.buyer_label(c, buyer, chat_id)}\n💬 Чат Playerok, аккаунт "
                                            f"<b>{escape(c.label)}</b>", reply_markup=card_kb(c.id, chat_id),
                                  message_thread_id=thread, disable_web_page_preview=True)
            messages = self.history_without_mirrored(c, c.account.get_chat_messages(chat_id, count=24).messages)
            for chunk in chatview.history_chunks(c, messages, buyer, HISTORY_ON_CREATE, skip_message_id, chat_id):
                self.bot.send_message(group_id, chunk, message_thread_id=thread, disable_web_page_preview=True)
        except Exception as e:
            c.log.warning(f"Не удалось отправить начало темы чата {chat_id}: {e}")
