from __future__ import annotations

import time
import logging
from pathlib import Path
from html import escape

from telebot.types import InlineKeyboardButton as B, InlineKeyboardMarkup as K
from Utils.control_store import ControlStore
from tg_bot import keyboards as kbs


def allowed(tg, user_id):
    return str(user_id) == tg.hub.env.get('control_admin_id') and bool(tg.hub.env.get('control_db'))


def guard(tg, call):
    if allowed(tg, call.from_user.id):
        return True
    tg.answer(call, 'Нет доступа', alert=True)
    return False


def store(tg):
    return ControlStore(tg.hub.env['control_db'])


def screen(tg, call):
    if not guard(tg, call):
        return
    records = store(tg).installations()
    now = time.time()
    active = sum(now - r['last_seen'] < 86400 for r in records)
    new = sum(now - r['created'] < 86400 for r in records)
    kb = K()
    kb.row(B('👥 Пользователи', callback_data='ctll:0'))
    kb.row(B('📢 Рассылка', callback_data='ctlmsg'))
    kb.row(B('📦 Загрузить обновление', callback_data='ctlup'))
    kb.row(B('◀️ Назад', callback_data='menu'))
    tg.edit(call, f'👑 <b>Управление ботом</b>\n\nУстановок: {len(records)}\n'
                  f'Активных за 24 часа: {active}\nНовых за 24 часа: {new}', kb)
    tg.answer(call)


def listing(tg, call):
    if not guard(tg, call):
        return
    records = store(tg).installations()
    raw = call.data.split(':')[-1]
    page = int(raw) if raw.isdigit() else 0
    page = max(0, min(page, max(0, (len(records) - 1) // 8)))
    kb = K()
    for r in records[page * 8:(page + 1) * 8]:
        label = str(r['owners'][0]) if r['owners'] else r['id'][:8]
        kb.row(B(('⛔ ' if r['blocked'] else '👤 ') + label, callback_data='ctlu:' + r['id']))
    nav = []
    if page:
        nav.append(B('⬅️', callback_data=f'ctll:{page - 1}'))
    if (page + 1) * 8 < len(records):
        nav.append(B('➡️', callback_data=f'ctll:{page + 1}'))
    if nav:
        kb.row(*nav)
    kb.row(B('◀️ Назад', callback_data='ctl'))
    tg.edit(call, '👥 <b>Установки</b>', kb)
    tg.answer(call)


def detail(tg, call):
    if not guard(tg, call):
        return
    ident = call.data.split(':', 1)[1]
    record = next((r for r in store(tg).installations() if r['id'] == ident), None)
    if not record:
        tg.answer(call, 'Установка не найдена', alert=True)
        return
    kb = K()
    kb.row(B('✅ Разблокировать' if record['blocked'] else '⛔ Заблокировать', callback_data='ctlb:' + ident))
    kb.row(B('◀️ Назад', callback_data='ctll:0'))
    plugins = ', '.join(escape(p) for p in record['plugins']) or 'нет'
    tg.edit(call, f"👤 <code>{escape(ident)}</code>\n"
                  f"Telegram ID: {escape(', '.join(map(str, record['owners'])) or 'нет')}\n"
                  f"Последняя активность: {time.strftime('%d.%m.%Y %H:%M', time.localtime(record['last_seen']))}\n"
                  f"Плагины: {plugins}\nСтатус: {'заблокирован' if record['blocked'] else 'активен'}", kb)
    tg.answer(call)


def block(tg, call):
    if not guard(tg, call):
        return
    ident = call.data.split(':', 1)[1]
    record = next((r for r in store(tg).installations() if r['id'] == ident), None)
    if not record:
        tg.answer(call, 'Установка не найдена', alert=True)
        return
    store(tg).set_blocked(ident, not record['blocked'])
    detail(tg, call)


def broadcast(tg, call):
    if not guard(tg, call):
        return
    def save(message):
        if not allowed(tg, message.from_user.id):
            return
        text = message.text or ''
        try:
            store(tg).broadcast(text)
        except ValueError:
            tg.bot.send_message(message.chat.id, 'Текст должен содержать от 1 до 3500 символов.')
            return
        tg.bot.send_message(message.chat.id, '✅ Рассылка создана; копии получат её при подключении.')
    tg.ask(call.message.chat.id, call.from_user.id, 'Отправьте текст рассылки:', save)
    tg.answer(call)


def upload(tg, call):
    if not guard(tg, call):
        return
    def save(message):
        if not allowed(tg, message.from_user.id):
            return
        from Utils.release_archive import validate_archive, ReleaseError
        document = getattr(message, 'document', None)
        if not document or not document.file_name.lower().endswith('.zip') or document.file_size > 15 * 1024 * 1024:
            tg.bot.send_message(message.chat.id, 'Нужен ZIP размером до 15 МБ.')
            return
        info = tg.bot.get_file(document.file_id)
        data = tg.bot.download_file(info.file_path)
        folder = Path('backup') / 'published-updates'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'upload-{time.time_ns()}.zip'
        path.write_bytes(data)
        try:
            validate_archive(str(path))
            release_id = store(tg).publish_release(data)
        except (ReleaseError, ValueError) as exc:
            tg.bot.send_message(message.chat.id, f'❌ Обновление отклонено: {escape(str(exc))}')
            return
        tg.bot.send_message(message.chat.id, f'✅ Обновление №{release_id} опубликовано.')
    tg.ask(call.message.chat.id, call.from_user.id, 'Отправьте ZIP с файлами обновления:', save, accept_documents=True)
    tg.answer(call)


def update_prompt(tg, call):
    if not getattr(tg, 'control_client', None):
        tg.answer(call, 'Сервис обновлений не настроен', alert=True)
        return
    kb = K().row(B('✅ Установить', callback_data='upd_yes'), B('❌ Отмена', callback_data='close'))
    tg.bot.send_message(call.message.chat.id, 'Установить обновление? После установки потребуется /restart.', reply_markup=kb)
    tg.answer(call)


def update_install(tg, call):
    client = getattr(tg, 'control_client', None)
    if client is None:
        tg.answer(call, 'Сервис обновлений не настроен', alert=True)
        return
    try:
        client.install()
    except Exception:
        logging.getLogger('cardinal.control').exception('Unable to install release')
        tg.answer(call, 'Не удалось установить обновление. Проверьте логи.', alert=True)
        return
    tg.bot.send_message(call.message.chat.id, '✅ Обновление установлено. Для применения выполните /restart.')
    tg.answer(call)


def register(tg):
    for prefix, handler in [('ctll:', listing), ('ctlu:', detail), ('ctlb:', block)]:
        tg.on_prefix(prefix, handler)
    tg.on_exact('ctl', screen)
    tg.on_exact('ctlmsg', broadcast)
    tg.on_exact('ctlup', upload)
    tg.on_exact('upd', update_prompt)
    tg.on_exact('upd_yes', update_install)
